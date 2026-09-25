"""Phase 5: ambiguity, phrasing and generation, ending with the reference conversation end to end."""
import json

import pytest

from src.agent import COMPLETE_REPLY, handle_turn
from src.ambiguity import find
from src.catalog.loader import get_catalog
from src.engine import next_question, requirements
from src.generator import generate, state_table
from src.grounding import Grounded
from src.llm.base import LLM
from src.llm.stub import StubLLM
from src.phraser import fallback, phrase, plain
from src.state import WorkflowState

CATALOG = get_catalog()

REFERENCE = [
    ("Whenever a new invoice arrives, notify my finance team.", "Which platform receives the invoice?"),
    ("Gmail", "Which Gmail label or folder should I monitor?"),
    ("Finance", "Should every invoice trigger the workflow, or only invoices above a certain amount?"),
    ("Only above ₹10,000", "Where should the notification be sent?"),
    ("Slack", "Which Slack workspace and channel?"),
    ("#finance channel", "Should duplicate invoices be ignored?"),
    ("Yes", COMPLETE_REPLY),
]

REFERENCE_WORKFLOW = {
    "nodes": [
        {"id": "n1", "type": "gmail_trigger", "label": "Gmail Trigger", "params": {"only_unread": True}},
        {"id": "n2", "type": "filter_by_label", "label": "Filter by Label", "params": {"label": "Finance"}},
        {"id": "n3", "type": "deduplicate", "label": "Duplicate Handling", "params": {"enabled": True}},
        {"id": "n4", "type": "extract_fields", "label": "Extract Fields", "params": {"fields": ["amount"]}},
        {"id": "n5", "type": "condition", "label": "Condition",
         "params": {"field": "amount", "operator": "gt", "value": 10000}},
        {"id": "n6", "type": "slack_notify", "label": "Slack Notification", "params": {"channel": "#finance"}},
        {"id": "end", "type": "end", "label": "End", "params": {}},
    ],
    "edges": [
        {"from": "n1", "to": "n2"},
        {"from": "n2", "to": "n3"},
        {"from": "n3", "to": "n4"},
        {"from": "n4", "to": "n5"},
        {"from": "n5", "to": "n6", "branch": "yes"},
        {"from": "n5", "to": "end", "branch": "no"},
    ],
}

REFERENCE_TABLE = [
    {"parameter": "Trigger Source", "value": "Gmail"},
    {"parameter": "Monitor Location", "value": "Finance (Label)"},
    {"parameter": "Condition", "value": "Amount > ₹10,000"},
    {"parameter": "Notification Channel", "value": "Slack"},
    {"parameter": "Channel / Recipient", "value": "#finance"},
    {"parameter": "Duplicate Handling", "value": "Yes"},
    {"parameter": "Additional Preferences", "value": "–"},
    {"parameter": "Status", "value": "All information collected"},
]


def new_state():
    return WorkflowState.new("gen", CATALOG)


def fill(state, **values):
    for key, value in values.items():
        state.set(key.replace("__", "."), value, span=str(value), turn=0)
    return state


# -- the reference conversation, end to end through the stub ----------------

@pytest.fixture(scope="module")
def reference_run():
    state, llm, results = new_state(), StubLLM(), []
    for utterance, _ in REFERENCE:
        results.append(handle_turn(state, utterance, llm, CATALOG))
    return state, results


def test_reference_questions_match_the_brief(reference_run):
    _, results = reference_run
    assert [r.reply for r in results] == [expected for _, expected in REFERENCE]


def test_reference_completes_only_on_the_last_turn(reference_run):
    _, results = reference_run
    assert [r.complete for r in results] == [False] * 6 + [True]


def test_reference_produces_the_exact_workflow(reference_run):
    _, results = reference_run
    assert json.dumps(results[-1].workflow, sort_keys=True) == json.dumps(REFERENCE_WORKFLOW, sort_keys=True)


def test_reference_produces_the_state_table(reference_run):
    _, results = reference_run
    assert results[-1].state_table == REFERENCE_TABLE


def test_reference_hallucination_was_rejected_not_applied(reference_run):
    state, results = reference_run
    assert [r["requirement"] for r in results[0].rejected] == ["action"]
    assert state.get("action").span == "Slack"   # set on turn 5, from the user's own word


def test_state_table_fills_in_turn_by_turn(reference_run):
    _, results = reference_run
    filled = [sum(row["value"] != "–" for row in r.state_table[:6]) for r in results]
    assert filled == sorted(filled) and filled[0] == 0 and filled[-1] == 6


# -- generation shapes ------------------------------------------------------

def test_no_condition_means_straight_line_and_no_end():
    state = fill(new_state(), trigger="webhook_trigger", trigger__path="/in", filter__mode="all",
                 action="email_send", action__recipient="a@b.co", dedupe__enabled=False)
    wf = generate(state, CATALOG)
    assert [n["type"] for n in wf["nodes"]] == ["webhook_trigger", "email_send"]
    assert wf["nodes"][0]["params"] == {"path": "/in", "method": "POST"}
    assert wf["edges"] == [{"from": "n1", "to": "n2"}]


def test_outlook_filter_is_labelled_as_folder():
    state = fill(new_state(), trigger="outlook_trigger", trigger__monitor_location="Invoices",
                 filter__mode="all", action="slack_notify", action__channel="#ap", dedupe__enabled=True)
    labels = [n["label"] for n in generate(state, CATALOG)["nodes"]]
    assert labels[:2] == ["Outlook Trigger", "Filter by Folder"]


def test_partial_state_table_shows_what_is_missing():
    table = {r["parameter"]: r["value"] for r in state_table(fill(new_state(), trigger="gmail_trigger"), CATALOG)}
    assert table["Trigger Source"] == "Gmail"
    assert table["Monitor Location"] == "–"
    assert table["Status"] == "4 items still needed"


# -- state-table row labels ---------------------------------------------------

BRIEF_ROWS = ["Trigger Source", "Monitor Location", "Condition", "Notification Channel",
              "Channel / Recipient", "Duplicate Handling", "Additional Preferences", "Status"]


def row_names(state, catalog=CATALOG):
    return [r["parameter"] for r in state_table(state, catalog)]


def test_reference_path_keeps_the_briefs_row_names_in_order(reference_run):
    _, results = reference_run
    assert row_names(new_state()) == BRIEF_ROWS
    for r in results:
        assert [row["parameter"] for row in r.state_table] == BRIEF_ROWS


def jira_state():
    return fill(new_state(), trigger="github_trigger", trigger__repository="acme/webapp", trigger__event="issue",
                filter__mode="all", action="jira_create_issue", action__project="WEB",
                action__issue_type="bug", dedupe__enabled=True)


def test_jira_action_names_its_own_rows():
    table = state_table(jira_state(), CATALOG)
    assert [r["parameter"] for r in table] == [
        "Trigger Source", "Repository / Repository Event", "Condition", "Issue Tracker",
        "Project / Issue Type", "Duplicate Handling", "Additional Preferences", "Status"]
    assert table[4]["value"] == "WEB, bug"


def test_node_without_state_row_falls_back_to_the_briefs_names():
    jira = CATALOG.nodes["jira_create_issue"]
    bare = jira.model_copy(update={
        "state_row": None,
        "params": {k: v.model_copy(update={"state_row": None}) for k, v in jira.params.items()}})
    catalog = CATALOG.model_copy(update={"nodes": {**CATALOG.nodes, "jira_create_issue": bare}})
    names = row_names(jira_state(), catalog)
    assert names[3:5] == ["Notification Channel", "Channel / Recipient"]


# -- ambiguity ----------------------------------------------------------------

def offered(state):
    return requirements(state, CATALOG, include_optional=True)


def test_word_naming_two_options_is_ambiguous():
    clear, amb = find([Grounded("trigger", "gmail_trigger", "an email")], offered(new_state()), CATALOG, None)
    assert clear == []
    assert amb[0].requirement == "trigger"
    assert set(amb[0].options) == {"gmail_trigger", "outlook_trigger"}


def test_specific_word_is_not_ambiguous():
    clear, amb = find([Grounded("trigger", "gmail_trigger", "Gmail")], offered(new_state()), CATALOG, None)
    assert amb == [] and clear[0].value == "gmail_trigger"


def test_every_ambiguity_in_a_message_is_returned():
    said = [Grounded("trigger", "google_forms_trigger", "form"),
            Grounded("action", "google_sheets_append", "spreadsheet")]
    clear, amb = find(said, offered(new_state()), CATALOG, None)
    assert clear == []
    assert [(a.slot, a.span, set(a.options)) for a in amb] == [
        ("trigger", "form", {"google_forms_trigger", "typeform_trigger"}),
        ("action", "spreadsheet", {"google_sheets_append", "excel_append"})]


def test_word_inside_a_longer_name_belongs_to_that_name():
    clear, amb = find([Grounded("trigger", "google_forms_trigger", "Google Forms")],
                      offered(new_state()), CATALOG, None)
    assert amb == [] and clear[0].value == "google_forms_trigger"


@pytest.mark.parametrize("span, expected", [
    ("a Gmail email", ["gmail_trigger"]),        # "Gmail" is its own word; "email" is shared
    ("Outlook mail", ["outlook_trigger"]),
    ("an email", ["gmail_trigger", "outlook_trigger"]),   # nothing specific: still ambiguous
    ("Gmail or Outlook", ["gmail_trigger", "outlook_trigger"]),
])
def test_a_specific_word_outranks_a_shared_one(span, expected):
    clear, amb = find([Grounded("trigger", expected[0], span)], offered(new_state()), CATALOG, None)
    named = [clear[0].value] if clear else sorted(amb[0].options)
    assert named == expected


def test_one_span_filling_two_text_slots_is_ambiguous():
    state = fill(new_state(), trigger="gmail_trigger", action="slack_notify")
    both = [Grounded("trigger.monitor_location", "Finance", "Finance"),
            Grounded("action.channel", "Finance", "Finance")]
    clear, amb = find(both, offered(state), CATALOG, last_asked=None)
    assert clear == [] and amb[0].requirements == ["trigger.monitor_location", "action.channel"]


def test_the_question_just_asked_resolves_a_shared_span():
    state = fill(new_state(), trigger="gmail_trigger", action="slack_notify")
    both = [Grounded("trigger.monitor_location", "Finance", "Finance"),
            Grounded("action.channel", "Finance", "Finance")]
    clear, amb = find(both, offered(state), CATALOG, last_asked="action.channel")
    assert amb == [] and [g.requirement for g in clear] == ["action.channel"]


# -- phrasing -----------------------------------------------------------------

class Recorder(LLM):
    name = "recorder"

    def __init__(self, answer):
        self.answer, self.payloads = answer, []

    def complete_json(self, system, user, schema):
        self.payloads.append(json.loads(user))
        return self.answer


def test_phraser_passes_rephrase_flag_and_options():
    state = new_state()
    state.mark_asked("trigger")
    state.mark_asked("trigger")
    req = next_question(state, CATALOG)
    llm = Recorder({"question": "Gmail, Outlook, a webhook or a schedule?"})
    phrase(llm, req, state, CATALOG)
    sent = llm.payloads[0]
    assert sent["requirement"] == "trigger" and sent["rephrase"] is True
    assert sent["options"][:4] == ["Gmail", "Outlook", "Webhook", "Schedule"]
    assert "Google Sheets" in sent["options"]


def test_stub_rephrase_is_worded_differently():
    state = new_state()
    first = phrase(StubLLM(), next_question(state, CATALOG), state, CATALOG)
    state.mark_asked("trigger")
    state.mark_asked("trigger")
    third = phrase(StubLLM(), next_question(state, CATALOG), state, CATALOG)
    assert first != third


def test_phraser_falls_back_on_empty_wording():
    req = next_question(new_state(), CATALOG)
    assert phrase(Recorder({"question": ""}), req, new_state(), CATALOG) == fallback(req)


def test_ambiguity_question_quotes_the_users_words():
    state = new_state()
    req = next_question(state, CATALOG)
    _, amb = find([Grounded("trigger", "gmail_trigger", "an email")], offered(state), CATALOG, None)
    question = phrase(StubLLM(), req, state, CATALOG, ambiguity=amb[0])
    assert question == 'When you say "an email", do you mean Gmail or Outlook?'


@pytest.mark.parametrize("raw, clean", [
    # What Claude sent on the live app: LaTeX \ndash, which JSON turns into a newline and "dash".
    ("Which Stripe event should kick this off \ndash a payment succeeding or failing?",
     "Which Stripe event should kick this off \u2014 a payment succeeding or failing?"),
    ("Slack \\mdash or email?", "Slack \u2014 or email?"),
    ("Which\nchannel?", "Which channel?"),
    ("Show it on the dashboard?", "Show it on the dashboard?"),
])
def test_questions_are_plain_one_line_text(raw, clean):
    assert plain(raw) == clean


def test_phrased_question_is_cleaned_before_it_is_asked():
    llm = Recorder({"question": "Which Stripe event \ndash payment or invoice?"})
    req = next_question(new_state(), CATALOG)
    assert phrase(llm, req, new_state(), CATALOG) == "Which Stripe event \u2014 payment or invoice?"
