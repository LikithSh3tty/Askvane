"""LLM slot extraction, restricted to the requirements currently on offer.

The model is shown only the requirements the engine says are open (plus
optional params of nodes already chosen). Anything it returns for a slot it was
not offered is dropped here, before grounding ever sees it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.catalog.loader import Catalog
from src.engine import Requirement
from src.llm.base import LLM

log = logging.getLogger(__name__)

SCHEMA = {
    "title": "extraction",
    "type": "object",
    "properties": {
        "extractions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "value": {"type": "string"},
                    "span": {"type": "string"},
                },
                "required": ["requirement", "value", "span"],
                "additionalProperties": False,
            },
        },
        "unsupported": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "span": {"type": "string"},
                },
                "required": ["requirement", "span"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["extractions", "unsupported"],
    "additionalProperties": False,
}

SYSTEM = """You read values out of one message from a user who is describing an automation workflow.

You are given the message, the question the user was last asked, and a list of requirements. For each requirement the message answers, return:
- requirement: the requirement id, exactly as listed
- value: for requirements with options, one option id exactly as listed; otherwise the value as the user wrote it
- span: the exact words from the message the value comes from, copied character for character

Rules:
- Only use requirement ids from the list. Never invent one.
- Only extract what the message states explicitly. Never infer, guess, or fill in a sensible default. "Notify my finance team" names no platform and no channel.
- If a word could mean more than one option (for example "email" could be Gmail or Outlook), still return the option you think is meant; the caller checks ambiguity itself.
- If the user names something for a requirement that none of its options covers (a platform or service that is not listed), add it to `unsupported` with the requirement id and, as the span, just its name as the user wrote it ("Mattermost", not "post it to Mattermost").
- If the message answers nothing on the list, return empty arrays.
"""


@dataclass
class Extraction:
    requirement: str
    value: str
    span: str


@dataclass
class ExtractionResult:
    extractions: list[Extraction] = field(default_factory=list)
    unsupported: list[Extraction] = field(default_factory=list)
    dropped: list[Extraction] = field(default_factory=list)   # named a requirement not on offer


def describe(req: Requirement, catalog: Catalog) -> dict:
    """What the model is told about one requirement."""
    out: dict = {"id": req.id, "asks_for": req.hint}
    if req.param is None:
        out["options"] = [
            {"id": n, "name": catalog.nodes[n].display or catalog.nodes[n].label,
             "also_called": catalog.nodes[n].aliases}
            for n in req.options
        ]
    elif req.options:
        aliases = catalog.nodes[req.node].params[req.param].aliases or {}
        out["options"] = [{"id": str(o).lower() if isinstance(o, bool) else str(o),
                           "also_called": aliases.get(o, [])} for o in req.options]
    return out


def extract(llm: LLM, utterance: str, offered: list[Requirement], catalog: Catalog,
            last_question: str | None = None) -> ExtractionResult:
    payload = {
        "utterance": utterance,
        "last_question": last_question,
        "requirements": [describe(r, catalog) for r in offered],
    }
    raw = llm.complete_json(SYSTEM, json.dumps(payload, ensure_ascii=False), SCHEMA)
    offered_ids = {r.id for r in offered}
    result = ExtractionResult()
    for item in raw.get("extractions", []):
        ex = Extraction(str(item["requirement"]), str(item["value"]), str(item["span"]))
        if ex.requirement in offered_ids:
            result.extractions.append(ex)
        else:
            log.info("dropped extraction for %s: not on offer this turn", ex.requirement)
            result.dropped.append(ex)
    for item in raw.get("unsupported", []):
        if item.get("requirement") in offered_ids:
            result.unsupported.append(Extraction(item["requirement"], "", str(item["span"])))
    return result
