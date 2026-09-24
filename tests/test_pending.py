"""A second ambiguity in one message is parked, grounded, and asked when its requirement comes up."""
import pytest

from src.agent import handle_turn
from src.catalog.loader import get_catalog
from src.llm.stub import StubLLM
from src.state import WorkflowState

CATALOG = get_catalog()
OPEN_ACTION = "Where should the notification be sent?"
NARROWED_ACTION = 'When you say "spreadsheet", do you mean Google Sheets or Excel?'


def drive(*utterances):
    state, llm = WorkflowState.new("p", CATALOG), StubLLM()
    return state, [handle_turn(state, u, llm, CATALOG).reply for u in utterances]


def test_second_ambiguity_is_parked_not_asked_this_turn():
    state, replies = drive("Add new form responses to a spreadsheet")
    assert replies == ['When you say "form", do you mean Google Forms or Typeform?']
    parked = state.pending_ambiguities["action"]
    assert (parked.span, set(parked.options)) == ("spreadsheet", {"google_sheets_append", "excel_append"})


def test_narrowed_question_is_asked_instead_of_the_open_one():
    _, replies = drive("Add new form responses to a spreadsheet", "Typeform", "Customer survey", "all")
    assert replies[-1] == NARROWED_ACTION
    assert OPEN_ACTION not in replies


def test_answering_the_narrowed_question_clears_it():
    state, _ = drive("Add new form responses to a spreadsheet", "Typeform", "Customer survey", "all", "Excel")
    assert state.value("action") == "excel_append" and state.pending_ambiguities == {}


def test_one_answer_can_resolve_both_ambiguities():
    state, replies = drive("Put new form answers into a spreadsheet",
                           "Google Forms, and Google Sheets for the spreadsheet")
    assert state.value("action") == "google_sheets_append" and state.pending_ambiguities == {}
    assert replies[-1] == "Which Google Form should I watch for new responses?"


def test_changing_the_first_answer_keeps_the_parked_second():
    state, replies = drive("Send form responses to a spreadsheet", "Google Forms", "Actually, make it Typeform",
                           "Customer survey", "all")
    assert state.value("trigger") == "typeform_trigger"
    assert replies[-1] == NARROWED_ACTION


def test_answer_outside_the_candidates_discards_the_narrowing():
    state, replies = drive("Save new form responses in a spreadsheet", "Typeform", "Feedback form", "all",
                           "Neither, use Airtable")
    assert state.value("action") == "airtable_create_record" and state.pending_ambiguities == {}
    assert replies[-1] == "Which Airtable base should the record go in?"


@pytest.mark.parametrize("span", ["workbook", ""])
def test_ungrounded_parked_ambiguity_is_not_used(span):
    # Tamper with the parked span: the user never said it, so it may not narrow the question.
    state, llm = WorkflowState.new("p", CATALOG), StubLLM()
    for u in ("Add new form responses to a spreadsheet", "Typeform", "Customer survey"):
        handle_turn(state, u, llm, CATALOG)
    state.pending_ambiguities["action"] = state.pending_ambiguities["action"].model_copy(update={"span": span})
    reply = handle_turn(state, "all", llm, CATALOG).reply
    assert reply == OPEN_ACTION and "action" not in state.pending_ambiguities


def test_narrowed_question_gives_way_to_the_rephrase_after_two_asks():
    state, llm = WorkflowState.new("p", CATALOG), StubLLM()
    for u in ("Add new form responses to a spreadsheet", "Typeform", "Customer survey", "all"):
        handle_turn(state, u, llm, CATALOG)
    state.asked["action"] = 2      # the narrowed question has gone unanswered twice
    reply = handle_turn(state, "hmm, whatever you think", llm, CATALOG).reply
    assert reply.startswith("Please pick where the result goes") and "action" not in state.pending_ambiguities
