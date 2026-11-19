import pytest

from jobmate.llm import FakeLLM, extract_json
from jobmate.models import ModelConfig

MC = ModelConfig(model="m")


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}


def test_extract_json_embedded():
    assert extract_json('here is {"a": 3} done') == {"a": 3}


def test_extract_json_fail():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_fake_llm_records_calls():
    llm = FakeLLM(handler=lambda **kw: {"result": "x"})
    out, usage = llm.complete(model=MC, system="s", user="u")
    assert out == {"result": "x"}
    assert usage == {"tokens_in": 10, "tokens_out": 10}
    assert len(llm.calls) == 1


def test_fake_llm_schema():
    from jobmate.models import QaVerdict
    llm = FakeLLM(handler=lambda **kw: {"decision": "pass", "rationale": "ok"})
    out, _ = llm.complete(model=MC, system="s", user="u", schema=QaVerdict)
    assert isinstance(out, QaVerdict)


def test_iter_sse_data():
    from jobmate.llm.codex import iter_sse_data
    lines = [
        "event: response.created",
        'data: {"type":"response.created"}',
        "",
        "data: not-json",
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        "data: [DONE]",
    ]
    evs = list(iter_sse_data(lines))
    assert evs == [{"type": "response.created"},
                   {"type": "response.output_text.delta", "delta": "hi"}]


def test_codex_auth_missing_file(tmp_path):
    from jobmate.llm import LLMError
    from jobmate.llm.codex import CodexAuth
    with pytest.raises(LLMError, match="codex login"):
        CodexAuth(tmp_path / "nope.json").headers()


def test_composite_llm_dispatch():
    from jobmate.llm.codex import CompositeLLM
    calls = []

    class Rec:
        def __init__(self, tag):
            self.tag = tag

        def complete(self, **kw):
            calls.append(self.tag)
            return {"ok": True}, {"tokens_in": 1, "tokens_out": 1}

    comp = CompositeLLM(compat=Rec("compat"), codex=Rec("codex"))
    codex_model = ModelConfig(model="gpt-5.6-sol", provider="codex")
    compat_model = ModelConfig(model="gemini-3.5-flash-lite")
    comp.complete(model=codex_model, system="s", user="u")
    comp.complete(model=compat_model, system="s", user="u")
    assert calls == ["codex", "compat"]


def test_settings_mode_routing():
    from jobmate.config import Settings
    debug = Settings(mode="debug")
    rel = Settings(mode="release")
    assert debug.model_for("implementer", "important").provider == "openai_compat"
    assert rel.model_for("analysis", "default").provider == "openai_compat"
    imp = rel.model_for("implementer", "important")
    assert imp.provider == "codex" and imp.model == rel.important_model
