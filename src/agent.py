"""One conversational turn: read values, guard them, then ask one thing or finish.

This is the only place the pieces meet. The LLM is called for extraction and
for wording; every decision in between (what is on offer, what is accepted,
what is ambiguous, what to ask, whether the workflow is complete) is made by
deterministic code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src import ambiguity
from src.catalog.loader import Catalog, get_catalog
from src.engine import Requirement, is_complete, next_question, requirements
from src.extractor import Extraction, extract
from src.generator import generate, state_table
from src.grounding import Grounded, coerce_option, ground, mentions, option_aliases
from src.llm.base import LLM
from src.phraser import option_name, phrase
from src.state import Turn, WorkflowState

COMPLETE_REPLY = "I have all the required information. Generating your workflow."
# Words that mark a change of mind. Only then are already-filled requirements back on offer.
CORRECTION = re.compile(r"\b(actually|instead|change|rather|switch|no wait|scratch that|make it)\b", re.I)
MAX_PASSES = 3   # a value can open new requirements that the same message already answers


@dataclass
class TurnResult:
    reply: str
    complete: bool
    state_table: list[dict]
    workflow: dict | None = None
    rejected: list[dict] = field(default_factory=list)


def _unguarded(extractions: list[Extraction], offered: list[Requirement]) -> list[Grounded]:
    """What the state would receive with the guard switched off. Used only by the eval ablation."""
    by_id = {r.id: r for r in offered}
    out = []
    for ex in extractions:
        req = by_id[ex.requirement]
        value = coerce_option(ex.value, req.options) if req.options else ex.value
        if value is not None:
            out.append(Grounded(ex.requirement, value, ex.span))
    return out


def _is_really_unsupported(item: Extraction, utterance: str, offered: list[Requirement],
                           catalog: Catalog) -> bool:
    """The model says the user named something we cannot do; check that deterministically."""
    req = next((r for r in offered if r.id == item.requirement), None)
    if req is None or not req.options or item.span.casefold() not in utterance.casefold():
        return False
    return not any(mentions(item.span, a) for aliases in option_aliases(req, catalog).values() for a in aliases)


def _decline(item: Extraction, offered: list[Requirement], catalog: Catalog) -> str:
    req = next(r for r in offered if r.id == item.requirement)
    names = [option_name(req, o, catalog) for o in req.options]
    listed = ", ".join(names[:-1]) + " or " + names[-1]
    return f"I can't build a workflow with {item.span}: it isn't one of the supported options ({listed}). "


def handle_turn(state: WorkflowState, utterance: str, llm: LLM, catalog: Catalog | None = None,
                guard: bool = True) -> TurnResult:
    catalog = catalog or get_catalog()
    last_question = next((t.text for t in reversed(state.transcript) if t.role == "agent"), None)
    state.transcript.append(Turn(role="user", text=utterance))
    turn = state.user_turn

    offered = requirements(state, catalog, include_optional=True,
                           include_filled=bool(CORRECTION.search(utterance)))
    seen: set[str] = set()
    ambiguous: list[ambiguity.Ambiguity] = []
    declines: list[str] = []
    rejected: list[dict] = []

    for _ in range(MAX_PASSES):
        fresh = [r for r in offered if r.id not in seen]
        if not fresh:
            break
        seen |= {r.id for r in fresh}
        result = extract(llm, utterance, fresh, catalog, last_question)
        if guard:
            accepted, rejections = ground(result.extractions, utterance, fresh, catalog)
        else:
            accepted, rejections = _unguarded(result.extractions, fresh), []
        rejected += [{"turn": turn, **vars(r)} for r in rejections]
        clear, found = ambiguity.find(accepted, fresh, catalog, state.last_asked)
        ambiguous += found
        for g in clear:
            state.set(g.requirement, g.value, g.span, turn)
        for item in result.unsupported:
            if _is_really_unsupported(item, utterance, fresh, catalog):
                declines.append(_decline(item, fresh, catalog))
                state.declined.append(item.span)
        offered = requirements(state, catalog, include_optional=True)
    state.rejections += rejected

    workflow = None
    if ambiguous:
        amb = ambiguous[0]
        req = next(r for r in requirements(state, catalog, include_optional=True, include_filled=True)
                   if r.id == amb.requirement)
        reply = phrase(llm, req, state, catalog, ambiguity=amb)
        state.mark_asked(req.id)
    elif is_complete(state, catalog):
        workflow = generate(state, catalog)
        reply = COMPLETE_REPLY
        state.last_asked = None
    else:
        req = next_question(state, catalog)
        reply = "".join(declines) + phrase(llm, req, state, catalog)
        state.mark_asked(req.id)

    state.transcript.append(Turn(role="agent", text=reply))
    return TurnResult(reply=reply, complete=workflow is not None, state_table=state_table(state, catalog),
                      workflow=workflow, rejected=rejected)
