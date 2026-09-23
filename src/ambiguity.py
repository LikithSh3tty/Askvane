"""Detect when what the user said has more than one reading. No LLM.

Detection means ask, never pick. Two cases:
  - options: the span names more than one option of the same requirement
    ("email" could be the Gmail trigger or the Outlook trigger);
  - slots: one piece of free text was read as the answer to two different
    requirements, and neither is the question the user was just asked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.catalog.loader import Catalog
from src.engine import Requirement
from src.grounding import Grounded, mentions, option_aliases, squash


@dataclass
class Ambiguity:
    requirement: str               # the requirement to ask about
    span: str
    options: list = field(default_factory=list)          # competing option ids
    requirements: list[str] = field(default_factory=list)  # competing requirement ids


def _overlap(a: str, b: str) -> bool:
    a, b = squash(a), squash(b)
    return a in b or b in a


def find(accepted: list[Grounded], offered: list[Requirement], catalog: Catalog,
         last_asked: str | None) -> tuple[list[Grounded], list[Ambiguity]]:
    by_id = {r.id: r for r in offered}
    clear: list[Grounded] = []
    ambiguous: list[Ambiguity] = []

    free_text: list[Grounded] = []
    for g in accepted:
        req = by_id[g.requirement]
        if req.options:
            named = [o for o, aliases in option_aliases(req, catalog).items()
                     if any(mentions(g.span, a) for a in aliases)]
            if len(named) > 1:
                ambiguous.append(Ambiguity(g.requirement, g.span, options=named))
            else:
                clear.append(g)
        else:
            free_text.append(g)

    # One span answering two free-text requirements.
    taken: set[int] = set()
    for i, g in enumerate(free_text):
        if i in taken:
            continue
        rivals = [j for j in range(i + 1, len(free_text))
                  if j not in taken and free_text[j].requirement != g.requirement
                  and _overlap(g.span, free_text[j].span)]
        if not rivals:
            clear.append(g)
            continue
        group = [g] + [free_text[j] for j in rivals]
        taken.update(rivals)
        asked = [x for x in group if x.requirement == last_asked]
        if asked:
            # The user was answering a specific question; that is the reading.
            clear.append(asked[0])
        else:
            ids = [x.requirement for x in group]
            ambiguous.append(Ambiguity(ids[0], g.span, requirements=ids))
    return clear, ambiguous
