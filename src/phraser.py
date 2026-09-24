"""LLM question wording. The engine has already chosen what to ask; this only says it."""
from __future__ import annotations

import json
import re

from src.ambiguity import Ambiguity
from src.catalog.loader import Catalog
from src.engine import Requirement
from src.llm.base import LLM
from src.state import WorkflowState

SCHEMA = {
    "title": "question",
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}

SYSTEM = """You word one clarification question for an assistant that builds automation workflows.

The assistant has already decided what to ask. You decide only how to say it.
- Ask about the given requirement and nothing else. One short question, ending with a question mark.
- Plain text on one line: no line breaks, markdown or LaTeX. Use a comma where you might use a dash.
- Use the conversation for context and the user's own words (say "invoices" if they talk about invoices).
- Never suggest or assume a value the user has not given, except when listing the allowed options.
- If `rephrase` is true, the plain question has been asked twice without an answer. Ask it differently: list the options, or give one concrete example of a valid answer.
- If `ambiguity` is set, the user's words fit more than one option. Quote their words and ask which option they meant.
"""


def option_name(req: Requirement, option, catalog: Catalog) -> str:
    if req.param is None:
        node = catalog.nodes[option]
        return node.display or node.label
    if isinstance(option, bool):
        return "yes" if option else "no"
    return str(option)


def _requirement_name(req_id: str) -> str:
    return req_id.partition(".")[2].replace("_", " ") or req_id


def fallback(req: Requirement) -> str:
    return f"Could you tell me {req.hint}?"


# LaTeX dash commands a model sometimes writes; inside JSON "\ndash" arrives as a newline and "dash".
LATEX_DASH = re.compile(r"\s*(?:\\[nm]dash\b|\n[nm]?dash\b)\s*")


def plain(text: str) -> str:
    """One line of plain text: LaTeX dashes become a real dash, any line break becomes a space."""
    return re.sub(r"\s+", " ", LATEX_DASH.sub(" \u2014 ", text)).strip()


def phrase(llm: LLM, req: Requirement, state: WorkflowState, catalog: Catalog,
           ambiguity: Ambiguity | None = None) -> str:
    payload = {
        "requirement": req.id,
        "node": req.node,
        "asks_for": req.hint,
        "options": [option_name(req, o, catalog) for o in req.options],
        "rephrase": req.rephrase,
        "ambiguity": None,
        "conversation": [f"{t.role}: {t.text}" for t in state.transcript],
    }
    if ambiguity:
        names = ([option_name(req, o, catalog) for o in ambiguity.options] if ambiguity.options
                 else [f"the {_requirement_name(r)}" for r in ambiguity.requirements])
        payload["ambiguity"] = {"span": ambiguity.span, "options": names}
    out = llm.complete_json(SYSTEM, json.dumps(payload, ensure_ascii=False), SCHEMA)
    question = plain(str(out.get("question", "")))
    # A malformed wording is not worth failing the turn over; the subject is fixed either way.
    return question if question and len(question) <= 400 else fallback(req)
