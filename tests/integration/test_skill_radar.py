"""skillRadar 포팅 기능 테스트 — collect/dedupe/summarize/rank/publish."""
from jobmate.adapters.fakes import FakeGitHub, FakeNotifier
from jobmate.models import RepoSummary
from jobmate.workflows.registry_builder import build_runner

from .helpers import make_env, scripted_llm


def _repo(name, stars=100, **kw):
    r = {"full_name": name, "html_url": f"https://github.com/{name}",
         "description": f"desc {name}", "stars": stars,
         "language": "Python", "topics": ["agent"], "archived": False,
         "fork": False, "license": "MIT"}
    r.update(kw)
    return r


def _summaries(n=5):
    return {"RepoSummary": [RepoSummary(
        summary_en=f"en {i}", summary_zh=f"zh {i}", summary_ko=f"ko {i}",
        taxonomy="agent-skill", taxonomy_confidence=0.9) for i in range(n)]}


def test_radar_full_flow():
    settings, store = make_env()
    gh = FakeGitHub(
        repos={"*": [_repo("a/x", 500), _repo("b/y", 300),
                     _repo("c/z", 100, archived=True)]},
        readmes={"a/x": "# readme x", "b/y": "# readme y"})
    # FakeGitHub는 query별 repos dict — 모든 query에 같은 결과를 주도록 수정 불가하므로
    # queries가 여러 개면 중복 제거로 검증
    notifier = FakeNotifier()
    adapters = {"github": gh, "notifier": notifier}
    llm = scripted_llm(_summaries(10))

    run = store.create_run("skill_radar", "0.1.0", {"task": "radar"},
                           trace_id="t")
    runner = build_runner("skill_radar", store=store, llm=llm,
                          settings=settings, adapters=adapters)
    runner.invoke(run.id, {"task": "radar"},
                          gmail_ctx={"platform": "claude", "force": True})
    # approve human stage에서 interrupt — approve로 resume
    runner.resume(run.id, {"decision": "approve", "comment": ""})

    dates = store.radar_dates("claude")
    assert dates, "no snapshot written"
    snap = store.radar_snapshot("claude", dates[0])
    items = store.radar_items(snap["id"])
    assert len(items) >= 1
    # archived repo는 제외
    names = [i["repo_full_name"] for i in items]
    assert "c/z" not in names
    assert items[0]["rank"] == 1
    assert items[0]["summary_ko"].startswith("ko")


def test_radar_dedupe_only():
    settings, store = make_env()
    from jobmate.engine.functions import FunctionContext, radar_dedupe
    ctx = FunctionContext(
        state={"stage_outputs": {"collect": {"repos": [
            _repo("a/x"), _repo("a/x"), _repo("b/y", fork=True),
            _repo("c/z", archived=True)]}}},
        store=store, settings=settings, adapters={}, run_id="t")
    out = radar_dedupe(ctx)
    names = [r["full_name"] for r in out["repos"]]
    assert names == ["a/x"]
    assert out["dropped"] == 3


def test_radar_publish_requires_approval():
    settings, store = make_env()
    from jobmate.engine.functions import FunctionContext, radar_publish
    ctx = FunctionContext(
        state={"approval": {"decision": "reject"},
               "stage_outputs": {"rank": {"repos": [], "date": "2026-01-01"}}},
        store=store, settings=settings, adapters={}, run_id="t")
    assert radar_publish(ctx)["status"] == "rejected"
