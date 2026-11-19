"""jobMate CLI — run 제출/조회/승인, gmail 상태."""
from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings
from .llm import CompositeLLM
from .models import RunStatus
from .store.db import Store
from .workflows import WORKFLOWS
from .workflows.registry_builder import build_runner


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jobmate")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="dev_review run 실행 (동기)")
    sp.add_argument("task")
    sp.add_argument("--workflow", default="dev_review")
    sp.add_argument("--coding-tool", default=None,
                    choices=["none", "claude", "codex", "antigravity",
                             "opencode"])

    sp = sub.add_parser("status", help="run 상태 조회")
    sp.add_argument("run_id")

    sp = sub.add_parser("approve", help="승인 대기 run 승인/거부")
    sp.add_argument("run_id")
    sp.add_argument("decision", choices=["approve", "reject"])
    sp.add_argument("--comment", default="")

    sp = sub.add_parser("resume", help="인터럽트된 run 재개(내부용)")
    sp.add_argument("run_id")
    sp.add_argument("decision", choices=["approve", "reject"])

    sp = sub.add_parser("workflows", help="등록된 워크플로 목록")
    args = p.parse_args(argv)

    settings = get_settings()
    store = Store(settings.db_path)
    llm = CompositeLLM()

    if args.cmd == "workflows":
        for w in WORKFLOWS.values():
            print(f"{w.key} v{w.version}")
        return 0

    if args.cmd == "run":
        if getattr(args, "coding_tool", None):
            settings = settings.model_copy(
                update={"coding_tool": args.coding_tool})
        run = store.create_run(args.workflow, WORKFLOWS[args.workflow].version,
                               {"task": args.task}, trace_id="cli")
        runner = build_runner(args.workflow, store=store, llm=llm,
                              settings=settings)
        try:
            runner.invoke(run.id, {"task": args.task})
        except Exception as e:
            store.update_run(run.id, status=RunStatus.FAILED, error=str(e)[:300])
        r = store.get_run(run.id)
        print(json.dumps({"id": run.id,
                          "status": r.status.value if r else "?",
                          "error": r.error if r else ""}, ensure_ascii=False))
        return 0

    if args.cmd == "status":
        r = store.get_run(args.run_id)
        if not r:
            print("not found", file=sys.stderr)
            return 1
        print(json.dumps({"id": r.id, "status": r.status.value,
                          "stages": [s.stage_key for s in store.get_stage_runs(r.id)]},
                         ensure_ascii=False))
        return 0

    if args.cmd in ("approve", "resume"):
        r = store.get_run(args.run_id)
        if not r:
            print("not found", file=sys.stderr)
            return 1
        runner = build_runner(r.workflow_key, store=store, llm=llm,
                              settings=settings)
        runner.resume(r.id, {"decision": args.decision,
                             "comment": getattr(args, "comment", "")})
        r2 = store.get_run(r.id)
        print(json.dumps({"id": r.id,
                          "status": r2.status.value if r2 else "?"}))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
