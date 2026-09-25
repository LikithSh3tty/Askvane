"""Detect when what the user said has more than one reading. No LLM.

Detection means ask, never pick. Every ambiguity in a message is returned, not
just the first: the agent asks about one and parks the rest. Two cases:
  - options: the span names more than one option of the same requirement
    ("email" could be the Gmail trigger or the Outlook trigger). Specific words
    win over generic ones: "Google Forms" names Google Forms, not also Typeform
    through "forms", and "a Gmail email" names Gmail, not also Outlook;
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

    @property
    def slot(self) -> str:
        return self.requirement.partition(".")[0]


def named_options(span: str, aliases: dict) -> list:
    """Options the span names, once generic words have given way to specific ones.

    Two steps. A word inside a longer name the user said belongs to that name
    ("Google Forms" is not also Typeform's "forms"). Then, if some option is named
    by a word of its own, options named only by words they share with it drop out:
    "a Gmail email" is Gmail, because "email" is shared with Outlook but "Gmail" is
    not. A bare "an email" names no option by a word of its own, so it stays ambiguous.
    """
    hits = {o: [a for a in names if mentions(span, a)] for o, names in aliases.items()}
    hits = {o: found for o, found in hits.items() if found}

    def inside_another(option, alias) -> bool:
        return any(squash(alias) != squash(longer) and mentions(longer, alias)
                   for other, found in hits.items() if other != option for longer in found)

    named = {o: found for o, found in hits.items() if not all(inside_another(o, a) for a in found)}

    def own_word(option) -> bool:
        others = {squash(a) for other, found in named.items() if other != option for a in found}
        return any(squash(a) not in others for a in named[option])

    specific = [o for o in named if own_word(o)]
    return specific if specific else list(named)


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
            named = named_options(g.span, option_aliases(req, catalog))
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
