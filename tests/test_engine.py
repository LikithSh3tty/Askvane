"""Engine tests. No LLM, no API key, no network."""
import pytest

from src.catalog.loader import get_catalog
from src.engine import is_complete, next_question, open_requirements, requirements
from src.state import WorkflowState


@pytest.fixture
def state():
    return WorkflowState.new("t", get_catalog())


def fill(state, **values):
    # Keyword names use "__" for "." so tests read naturally: trigger__monitor_location.
    for key, value in values.items():
        state.set(key.replace("__", "."), value, span=str(value), turn=0)
    return state


def ids(state):
    return [r.id for r in open_requirements(state)]


def full(state):
    return fill(state, trigger="gmail_trigger", trigger__monitor_location="Finance",
                filter__mode="filtered", filter__field="amount", filter__operator="gt",
                filter__value=10000, action="slack_notify", action__channel="#finance",
                dedupe__enabled=True)


def test_empty_state_asks_for_trigger(state):
    q = next_question(state)
    assert q.id == "trigger"
    assert "gmail_trigger" in q.options


def test_empty_state_has_no_param_requirements_for_unchosen_nodes(state):
    assert ids(state) == ["trigger", "filter.mode", "action", "dedupe.enabled"]


def test_choosing_gmail_creates_monitor_location(state):
    assert "trigger.monitor_location" not in ids(state)
    fill(state, trigger="gmail_trigger")
    assert "trigger.monitor_location" in ids(state)
    assert next_question(state).id == "trigger.monitor_location"


def test_webhook_creates_path_not_monitor_location(state):
    fill(state, trigger="webhook_trigger")
    assert "trigger.path" in ids(state)
    assert "trigger.monitor_location" not in ids(state)


def test_slack_and_email_create_different_requirements(state):
    slack = ids(fill(WorkflowState.new("a", get_catalog()), action="slack_notify"))
    email = ids(fill(WorkflowState.new("b", get_catalog()), action="email_send"))
    assert "action.channel" in slack and "action.recipient" not in slack
    assert "action.recipient" in email and "action.channel" not in email


def test_switching_action_drops_old_params(state):
    fill(state, action="slack_notify", action__channel="#finance")
    fill(state, action="email_send")
    assert state.value("action.channel") is None
    assert "action.recipient" in ids(state)


def test_condition_params_only_required_when_filtered(state):
    fill(state, filter__mode="all")
    assert not any(i.startswith("filter.") for i in ids(state))
    other = fill(WorkflowState.new("x", get_catalog()), filter__mode="filtered")
    assert [i for i in ids(other) if i.startswith("filter.")] == \
        ["filter.field", "filter.operator", "filter.value"]


def test_weekly_schedule_adds_day_of_week(state):
    fill(state, trigger="schedule_trigger", trigger__frequency="daily")
    assert "trigger.time" in ids(state) and "trigger.day_of_week" not in ids(state)
    fill(state, trigger__frequency="weekly")
    assert "trigger.day_of_week" in ids(state)


def test_question_order_follows_slots(state):
    fill(state, trigger="gmail_trigger", trigger__monitor_location="Finance")
    assert next_question(state).id == "filter.mode"
    fill(state, filter__mode="all")
    assert next_question(state).id == "action"
    fill(state, action="slack_notify")
    assert next_question(state).id == "action.channel"
    fill(state, action__channel="#finance")
    assert next_question(state).id == "dedupe.enabled"


def test_fully_populated_state_is_complete(state):
    full(state)
    assert is_complete(state)
    assert next_question(state) is None


def test_trigger_without_action_is_incomplete(state):
    fill(state, trigger="gmail_trigger", trigger__monitor_location="Finance",
         filter__mode="all", dedupe__enabled=True)
    assert not is_complete(state)
    assert next_question(state).id == "action"


def test_next_question_returns_exactly_one(state):
    q = next_question(state)
    assert not isinstance(q, list)


def test_twice_asked_requirement_is_flagged_for_rephrase(state):
    assert not next_question(state).rephrase
    state.mark_asked("trigger")
    assert not next_question(state).rephrase
    state.mark_asked("trigger")
    q = next_question(state)
    assert q.id == "trigger" and q.rephrase


def test_optional_params_are_offered_but_never_required(state):
    fill(state, action="slack_notify")
    optional = [r for r in requirements(state, include_optional=True) if not r.required]
    assert {r.id for r in optional} >= {"action.workspace", "action.message"}
    assert "action.workspace" not in ids(state)
