"""Grounding guard: reject any value the user did not actually say. No LLM.

An instruction in a prompt is a request; a span check is a guarantee. For every
extracted value:
  1. the claimed span must appear in what the user said this turn;
  2. the value must be one of the parameter's options, if it has any;
  3. the span must actually say that value: for an option, one of its aliases;
     for free text, the value itself.
Anything failing a check is rejected and logged, never applied.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.catalog.loader import Catalog
from src.engine import Requirement
from src.extractor import Extraction
from src.state import PendingAmbiguity, WorkflowState

log = logging.getLogger(__name__)

NUMBER = re.compile(r"^\d+(\.\d+)?$")


@dataclass
class Grounded:
    requirement: str
    value: Any
    span: str


@dataclass
class Rejection:
    requirement: str
    value: str
    span: str
    reason: str


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def mentions(text: str, phrase: str) -> bool:
    """True if `phrase` occurs in `text` as whole words (symbols like "₹" match anywhere)."""
    phrase = squash(phrase)
    if not phrase:
        return False
    left = r"(?<!\w)" if re.match(r"\w", phrase[0]) else ""
    right = r"(?!\w)" if re.match(r"\w", phrase[-1]) else ""
    return re.search(left + re.escape(phrase) + right, squash(text)) is not None


def option_aliases(req: Requirement, catalog: Catalog) -> dict[Any, list[str]]:
    """Every option of a closed-set requirement, with the words that name it."""
    if req.param is None:
        return {n: [n.replace("_", " "), *(catalog.nodes[n].aliases),
                    *([catalog.nodes[n].display] if catalog.nodes[n].display else [])]
                for n in req.options}
    aliases = catalog.nodes[req.node].params[req.param].aliases or {}
    return {o: [str(o), *aliases.get(o, [])] for o in req.options}


def coerce_option(value: str, options: tuple) -> Any:
    """Map the model's string back onto the option it names, or None."""
    for option in options:
        if squash(str(option)) == squash(value):
            return option
    return None


def as_number(value: str) -> int | float | None:
    """"10,000" -> 10000; anything that is not a plain number -> None."""
    digits = value.replace(",", "").strip()
    if not NUMBER.match(digits):
        return None
    number = float(digits)
    return int(number) if number.is_integer() else number


def _free_text_said(value: str, span: str) -> Any:
    """Return the typed value if the span says it, else None."""
    number = as_number(value)
    if number is not None:
        digits = value.replace(",", "").strip()
        span_digits = re.sub(r"(?<=\d)[,\s](?=\d)", "", span)
        found = re.search(rf"(?<![\d.]){re.escape(digits)}(?![\d.])", span_digits)
        return number if found else None
    # Verbatim, prefix included: "#finance" is not said by "finance team".
    value = value.strip()
    return value if value and squash(value) in squash(span) else None


def check(ex: Extraction, utterance: str, req: Requirement, catalog: Catalog) -> Grounded | Rejection:
    def reject(reason: str) -> Rejection:
        return Rejection(ex.requirement, ex.value, ex.span, reason)

    if not ex.span.strip() or squash(ex.span) not in squash(utterance):
        return reject("span does not appear in the user's message")

    if req.options:
        option = coerce_option(ex.value, req.options)
        if option is None:
            return reject(f"value is not one of {list(req.options)}")
        if not any(mentions(ex.span, alias) for alias in option_aliases(req, catalog)[option]):
            return reject(f"span does not name {option!r}")
        return Grounded(ex.requirement, option, ex.span)

    typed = _free_text_said(ex.value, ex.span)
    if typed is None:
        return reject("value does not appear in its span")
    return Grounded(ex.requirement, typed, ex.span)


def ground(extractions: list[Extraction], utterance: str, offered: list[Requirement],
           catalog: Catalog) -> tuple[list[Grounded], list[Rejection]]:
    by_id = {r.id: r for r in offered}
    accepted: list[Grounded] = []
    rejected: list[Rejection] = []
    for ex in extractions:
        req = by_id.get(ex.requirement)
        result = check(ex, utterance, req, catalog) if req else \
            Rejection(ex.requirement, ex.value, ex.span, "requirement not on offer")
        if isinstance(result, Rejection):
            log.warning("grounding rejected %s=%r (span %r): %s",
                        result.requirement, result.value, result.span, result.reason)
            rejected.append(result)
        else:
            accepted.append(result)
    return accepted, rejected


def check_pending(pending: PendingAmbiguity, state: WorkflowState, req: Requirement,
                  catalog: Catalog) -> str | None:
    """Why a parked ambiguity may not narrow a question about `req`, or None if it may.

    A parked ambiguity is held to the same standard as a value: its span must be
    words the user said on the turn it claims, and those words must name every
    option it narrows to. It can only narrow a question, never answer one.
    """
    if pending.requirement != req.id:
        return "parked for a different requirement"
    said = [t.text for t in state.transcript if t.role == "user"]
    if not 0 <= pending.turn < len(said) or not pending.span.strip()             or squash(pending.span) not in squash(said[pending.turn]):
        return "span does not appear in the user's message"
    if len(pending.options) < 2 or any(o not in req.options for o in pending.options):
        return f"options are not a choice among {list(req.options)}"
    aliases = option_aliases(req, catalog)
    if not all(any(mentions(pending.span, a) for a in aliases[o]) for o in pending.options):
        return "span does not name every option"
    return None
