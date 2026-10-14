"""Provider adapters (Anthropic, Gemini) against fake SDK clients: request shape and refusal
handling. No network, no API key."""

import functools
import json
from types import SimpleNamespace

import pytest
from google.genai import types

from app.core.config import settings
from app.services import doctor, llm_planner, llm_providers
from app.services.importer import import_project
from app.services.llm_providers import ProviderError, anthropic_complete, gemini_complete

SCHEMA = llm_planner.RATIONALE_SCHEMA
OK_JSON = json.dumps({"steps": []})


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_provider", "anthropic")


# --- Anthropic --------------------------------------------------------------------------------


class FakeAnthropic:
    def __init__(self, text=OK_JSON, stop_reason="end_turn"):
        self.calls: list[dict] = []
        self.messages = self
        self._text, self._stop = text, stop_reason

    def create(self, **kw):
        self.calls.append(kw)
        content = [SimpleNamespace(type="text", text=self._text)] if self._text is not None else []
        return SimpleNamespace(stop_reason=self._stop, content=content)


def test_anthropic_request_is_locked_down():
    client = FakeAnthropic()
    assert anthropic_complete("SYS", "USER", SCHEMA, client=client) == OK_JSON
    call = client.calls[0]
    assert call["model"] == "claude-opus-5" and call["system"] == "SYS"
    assert "tools" not in call and "tool_choice" not in call
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert call["messages"] == [{"role": "user", "content": "USER"}]


def test_anthropic_model_override(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "claude-sonnet-5")
    client = FakeAnthropic()
    anthropic_complete("s", "u", SCHEMA, client=client)
    assert client.calls[0]["model"] == "claude-sonnet-5"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_anthropic_incomplete_responses_are_errors(stop):
    with pytest.raises(ProviderError, match=stop):
        anthropic_complete("s", "u", SCHEMA, client=FakeAnthropic(stop_reason=stop))


def test_anthropic_empty_text_is_an_error():
    with pytest.raises(ProviderError, match="no text"):
        anthropic_complete("s", "u", SCHEMA, client=FakeAnthropic(text=None))


# --- Gemini -----------------------------------------------------------------------------------


class FakeGemini:
    def __init__(self, text=OK_JSON, finish=types.FinishReason.STOP, block_reason=None, candidates=True):
        self.calls: list[dict] = []
        self.models = self
        self._resp = SimpleNamespace(
            text=text,
            prompt_feedback=SimpleNamespace(block_reason=block_reason) if block_reason else None,
            candidates=[SimpleNamespace(finish_reason=finish)] if candidates else [],
        )

    def generate_content(self, **kw):
        self.calls.append(kw)
        return self._resp


def test_gemini_request_is_locked_down():
    client = FakeGemini()
    assert gemini_complete("SYS", "USER", SCHEMA, client=client) == OK_JSON
    call = client.calls[0]
    config = call["config"]
    assert call["model"] == "gemini-2.5-flash" and call["contents"] == "USER"
    assert config.system_instruction == "SYS"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == SCHEMA
    assert not config.tools and not config.tool_config  # no tools / function calling


def test_gemini_model_override(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "gemini-3.5-flash-lite")
    client = FakeGemini()
    gemini_complete("s", "u", SCHEMA, client=client)
    assert client.calls[0]["model"] == "gemini-3.5-flash-lite"


@pytest.mark.parametrize("finish", [types.FinishReason.SAFETY, types.FinishReason.MAX_TOKENS,
                                    types.FinishReason.PROHIBITED_CONTENT])
def test_gemini_incomplete_responses_are_errors(finish):
    with pytest.raises(ProviderError, match=finish.name):
        gemini_complete("s", "u", SCHEMA, client=FakeGemini(finish=finish))


def test_gemini_blocked_prompt_is_an_error():
    with pytest.raises(ProviderError, match="blocked"):
        gemini_complete("s", "u", SCHEMA, client=FakeGemini(block_reason="SAFETY"))


def test_gemini_no_candidates_or_text_are_errors():
    with pytest.raises(ProviderError, match="no candidates"):
        gemini_complete("s", "u", SCHEMA, client=FakeGemini(candidates=False))
    with pytest.raises(ProviderError, match="no text"):
        gemini_complete("s", "u", SCHEMA, client=FakeGemini(text=None))


def test_gemini_answers_go_through_the_same_checks_as_any_provider(demo_project):
    """End to end through the shared core: Gemini's JSON is validated like any other provider's."""
    from app.services import planner

    findings = doctor.diagnose(import_project(demo_project))
    proposals = planner.rule_proposals(findings)
    good = {"steps": [{"index": 0, "rationale": "The server needs it to start."}]}
    bad = {"steps": [{"index": 9, "rationale": "?"}]}

    ok = functools.partial(gemini_complete, client=FakeGemini(text=json.dumps(good)))
    [explained] = llm_planner.explain(findings, proposals, complete=ok)
    assert explained.rationale == "The server needs it to start." and explained.params == proposals[0].params

    evil = functools.partial(gemini_complete, client=FakeGemini(text=json.dumps(bad)))
    with pytest.raises(llm_planner.LLMPlanError, match="does not exist"):
        llm_planner.explain(findings, proposals, complete=evil)


# --- provider selection -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [("anthropic", anthropic_complete), ("gemini", gemini_complete), (" Gemini ", gemini_complete)],
)
def test_provider_selection(monkeypatch, name, expected):
    monkeypatch.setattr(settings, "llm_provider", name)
    assert llm_providers.get_provider() is expected


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    with pytest.raises(ProviderError, match="unknown LLM provider"):
        llm_providers.get_provider()
