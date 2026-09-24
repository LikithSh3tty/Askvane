import json
from types import SimpleNamespace

import pytest
import yaml

from src.catalog.loader import get_catalog
from src.llm.base import LLMError, make_llm
from src.llm.stub import RESPONSES_PATH, StubLLM, StubMiss

EXTRACTION = {"title": "extraction", "type": "object"}
QUESTION = {"title": "question", "type": "object"}
SCRIPT = yaml.safe_load(RESPONSES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stub():
    return StubLLM()


def extract(stub, text):
    return stub.complete_json("sys", json.dumps({"utterance": text, "requirements": []}), EXTRACTION)


@pytest.mark.parametrize("utterance", list(SCRIPT["extraction"]))
def test_stub_returns_valid_json_for_every_scripted_utterance(stub, utterance):
    out = extract(stub, utterance)
    assert set(out) == {"extractions", "unsupported"}
    for item in out["extractions"]:
        assert set(item) == {"requirement", "value", "span"}
        assert all(isinstance(v, str) for v in item.values())


def test_stub_matches_case_and_whitespace_insensitively(stub):
    assert extract(stub, "  gmail ")["extractions"][0]["value"] == "gmail_trigger"


@pytest.mark.parametrize("key", list(SCRIPT["question"]))
def test_stub_phrases_every_scripted_requirement(stub, key):
    req, _, node = key.partition(":")
    for rephrase in (False, True):
        payload = {"requirement": req, "node": node or None, "rephrase": rephrase}
        out = stub.complete_json("sys", json.dumps(payload), QUESTION)
        assert out["question"].strip()


def test_stub_can_phrase_every_question_the_catalog_can_raise():
    catalog = get_catalog()
    missing = []
    for slot in catalog.slots:
        node_types = catalog.choices(slot.id) if slot.choose else [slot.node]
        if slot.choose and slot.id not in SCRIPT["question"]:
            missing.append(slot.id)
        for node_type in node_types:
            for name, spec in catalog.nodes[node_type].params.items():
                if not (spec.required or spec.required_if):
                    continue
                req = f"{slot.id}.{name}"
                if f"{req}:{node_type}" not in SCRIPT["question"] and req not in SCRIPT["question"]:
                    missing.append(f"{req}:{node_type}")
    assert missing == []


def test_rephrase_differs_from_first_ask(stub):
    ask = stub.complete_json("s", json.dumps({"requirement": "trigger"}), QUESTION)
    again = stub.complete_json("s", json.dumps({"requirement": "trigger", "rephrase": True}), QUESTION)
    assert ask != again


def ask(stub, req, conversation):
    payload = {"requirement": req, "conversation": conversation}
    return stub.complete_json("s", json.dumps(payload), QUESTION)["question"]


def test_question_uses_what_the_user_said_the_workflow_is_about(stub):
    orders = ["user: When our webhook at /orders receives a call, forward it"]
    assert ask(stub, "dedupe.enabled", orders) == "Should duplicate orders be ignored?"
    assert "every order" in ask(stub, "filter.mode", orders)


def test_question_without_a_named_subject_is_generic(stub):
    schedule = ["user: Every day at 9am, post a summary to Slack in #daily"]
    for req in ("trigger", "filter.mode", "dedupe.enabled"):
        assert "invoice" not in ask(stub, req, schedule)
    assert ask(stub, "dedupe.enabled", schedule) == "Should duplicates be ignored?"


def test_agent_turns_do_not_set_the_subject(stub):
    assert ask(stub, "trigger", ["agent: Which platform receives the invoice?"]) == \
        "What should start this workflow?"


def test_unknown_utterance_raises(stub):
    with pytest.raises(StubMiss):
        extract(stub, "Post every new tweet to Discord and also order pizza")


def test_unknown_requirement_raises(stub):
    with pytest.raises(StubMiss):
        stub.complete_json("s", json.dumps({"requirement": "nope.nothing"}), QUESTION)


def test_unknown_task_raises(stub):
    with pytest.raises(StubMiss):
        stub.complete_json("s", json.dumps({}), {"title": "poetry"})


def test_make_llm_selects_stub_explicitly():
    assert make_llm("stub").name == "stub"


# -- the real provider, with the network call replaced ---------------------

class FakeMessages:
    def __init__(self, text, stop_reason="end_turn"):
        self.text, self.stop_reason, self.calls = text, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=self.text)])


def fake_provider(monkeypatch, text, stop_reason="end_turn"):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src.llm.anthropic import AnthropicLLM
    llm = AnthropicLLM(model="claude-test")
    messages = FakeMessages(text, stop_reason)
    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return llm, messages


def test_anthropic_provider_sends_schema_as_structured_output(monkeypatch):
    llm, messages = fake_provider(monkeypatch, '{"question": "Which one?"}')
    out = llm.complete_json("sys", "user", {"title": "question", "type": "object"})
    assert out == {"question": "Which one?"}
    fmt = messages.calls[0]["output_config"]["format"]
    assert fmt == {"type": "json_schema", "schema": {"type": "object"}}   # title stripped


def test_anthropic_provider_raises_on_refusal(monkeypatch):
    llm, _ = fake_provider(monkeypatch, "", stop_reason="refusal")
    with pytest.raises(LLMError):
        llm.complete_json("s", "u", {"type": "object"})


@pytest.mark.parametrize("value", ["", "  "])
def test_blank_model_env_falls_back_to_the_default(monkeypatch, value):
    from src.llm.anthropic import DEFAULT_MODEL, AnthropicLLM
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", value)
    assert AnthropicLLM().model == DEFAULT_MODEL


def test_anthropic_api_errors_become_llm_errors(monkeypatch):
    import anthropic
    import httpx2
    llm, messages = fake_provider(monkeypatch, "{}")
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def reject(**kwargs):
        raise anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=req), body=None)

    messages.create = reject
    with pytest.raises(LLMError, match="401 AuthenticationError: invalid x-api-key"):
        llm.complete_json("s", "u", {"type": "object"})

    def unreachable(**kwargs):
        raise anthropic.APIConnectionError(request=req)

    messages.create = unreachable
    with pytest.raises(LLMError, match="could not reach"):
        llm.complete_json("s", "u", {"type": "object"})


def test_anthropic_provider_raises_on_non_json(monkeypatch):
    llm, _ = fake_provider(monkeypatch, "Sure! here you go")
    with pytest.raises(LLMError):
        llm.complete_json("s", "u", {"type": "object"})

