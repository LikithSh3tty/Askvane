import logging

import pytest

from src.catalog.loader import get_catalog
from src.engine import requirements
from src.extractor import Extraction, extract
from src.grounding import Grounded, Rejection, ground, mentions
from src.llm.stub import StubLLM
from src.state import WorkflowState

CATALOG = get_catalog()
REFERENCE_OPENING = "Whenever a new invoice arrives, notify my finance team."


def offered_for(**chosen):
    state = WorkflowState.new("g", CATALOG)
    for key, value in chosen.items():
        state.set(key.replace("__", "."), value, span=str(value), turn=0)
    return requirements(state, CATALOG, include_optional=True)


def run(requirement, value, span, utterance, **chosen):
    accepted, rejected = ground([Extraction(requirement, value, span)], utterance,
                                offered_for(**chosen), CATALOG)
    return (accepted + rejected)[0]


def test_present_span_is_accepted():
    out = run("trigger", "gmail_trigger", "Gmail", "Gmail")
    assert isinstance(out, Grounded) and out.value == "gmail_trigger"


def test_absent_span_is_rejected():
    out = run("trigger.monitor_location", "Invoices", "Invoices", "Finance", trigger="gmail_trigger")
    assert isinstance(out, Rejection) and "span" in out.reason


def test_enum_violation_is_rejected():
    out = run("filter.operator", "between", "between", "only between 5 and 10", filter__mode="filtered")
    assert isinstance(out, Rejection) and "not one of" in out.reason


def test_unknown_node_type_is_rejected():
    out = run("action", "mattermost_notify", "Mattermost", "send it to Mattermost")
    assert isinstance(out, Rejection)


def test_span_that_does_not_name_the_option_is_rejected():
    # "Yes" is present, but it does not say "false".
    out = run("dedupe.enabled", "false", "Yes", "Yes")
    assert isinstance(out, Rejection) and "does not name" in out.reason


def test_boolean_answer_is_typed():
    out = run("dedupe.enabled", "true", "Yes", "Yes")
    assert isinstance(out, Grounded) and out.value is True


def test_amount_is_read_as_a_number():
    out = run("filter.value", "10000", "₹10,000", "Only above ₹10,000", filter__mode="filtered")
    assert isinstance(out, Grounded) and out.value == 10000


def test_wrong_number_is_rejected():
    out = run("filter.value", "1000", "₹10,000", "Only above ₹10,000", filter__mode="filtered")
    assert isinstance(out, Rejection)


def test_currency_symbol_grounds_the_amount_field():
    out = run("filter.field", "amount", "₹", "Only above ₹10,000", filter__mode="filtered")
    assert isinstance(out, Grounded) and out.value == "amount"


def test_value_must_appear_in_its_own_span():
    # The span is real text, but it does not contain the value claimed from it.
    out = run("action.channel", "#finance", "finance team", REFERENCE_OPENING, action="slack_notify")
    assert isinstance(out, Rejection)


def test_rejections_are_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="src.grounding"):
        run("trigger", "gmail_trigger", "Gmail", "Outlook please")
    assert any("grounding rejected trigger" in r.message for r in caplog.records)


def test_stub_hallucinates_finance_channel_and_guard_rejects_it():
    """The stub reads '#finance' and 'Slack' out of a message that names neither.

    Slack is already chosen here so the channel requirement is on offer; the
    extractor passes the hallucination through and only the guard stands between
    it and the state.
    """
    offered = offered_for(action="slack_notify")
    result = extract(StubLLM(), REFERENCE_OPENING, offered, CATALOG)
    assert any(e.requirement == "action.channel" and e.value == "#finance" for e in result.extractions)
    accepted, rejected = ground(result.extractions, REFERENCE_OPENING, offered, CATALOG)
    assert accepted == []
    assert {r.requirement for r in rejected} == {"action.channel"}


def test_guessed_slack_from_notify_is_rejected():
    offered = offered_for()
    result = extract(StubLLM(), REFERENCE_OPENING, offered, CATALOG)
    accepted, rejected = ground(result.extractions, REFERENCE_OPENING, offered, CATALOG)
    assert accepted == []
    assert [r.requirement for r in rejected] == ["action"]
    # The channel was never on offer, so the extractor dropped it before grounding.
    assert [d.requirement for d in result.dropped] == ["action.channel"]


@pytest.mark.parametrize("text,phrase,expected", [
    ("Gmail", "mail", False),
    ("send an email", "email", True),
    ("Only above ₹10,000", "₹", True),
    ("Yes", "yes", True),
    ("yesterday", "yes", False),
])
def test_mentions_uses_word_boundaries(text, phrase, expected):
    assert mentions(text, phrase) is expected
