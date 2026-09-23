"""Accuracy harness: drive every scripted conversation and classify the outcome.

    python eval/run_eval.py --provider stub
    python eval/run_eval.py --provider stub --no-grounding   # ablation: guard off

Outcomes:
  exact               final values match what the conversation should produce
  complete_different  a workflow was produced, but not the expected one
  incomplete          never reached the expected end state
  assumed             some value was filled that the user never gave (critical)

The `assumed` check is independent of the grounding guard: it looks at the
final state against the conversation's expected values and the full user
transcript, so switching the guard off shows up here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent import handle_turn  # noqa: E402
from src.catalog.loader import get_catalog  # noqa: E402
from src.engine import requirements  # noqa: E402
from src.grounding import mentions, option_aliases, squash  # noqa: E402
from src.llm.base import make_llm  # noqa: E402
from src.state import WorkflowState  # noqa: E402

CONVERSATIONS = ROOT / "eval" / "conversations.yaml"
RESULTS = ROOT / "eval" / "results.json"


def filled_values(state: WorkflowState) -> dict:
    out = {}
    for slot_id, slot in state.slots.items():
        if slot.choice:
            out[slot_id] = slot.choice.value
        for name, filled in slot.params.items():
            out[f"{slot_id}.{name}"] = filled.value
    return out


def said_somewhere(req_id: str, value, user_text: list[str], state: WorkflowState, catalog) -> bool:
    """Is there any evidence, anywhere in what the user typed, for this value?"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        digits = str(int(value)) if float(value).is_integer() else str(value)
        return any(digits in re.sub(r"(?<=\d)[,\s](?=\d)", "", t) for t in user_text)
    req = next((r for r in requirements(state, catalog, include_optional=True, include_filled=True)
                if r.id == req_id), None)
    if req is not None and req.options:
        aliases = option_aliases(req, catalog).get(value, [str(value)])
        return any(mentions(t, a) for t in user_text for a in aliases)
    return any(squash(str(value)) in squash(t) for t in user_text)


def run_conversation(conv: dict, llm, catalog, guard: bool) -> dict:
    state = WorkflowState.new(conv["id"], catalog)
    completed_at = None
    replies = []
    for i, utterance in enumerate(conv["turns"], start=1):
        result = handle_turn(state, utterance, llm, catalog, guard=guard)
        replies.append(result.reply)
        if result.complete and completed_at is None:
            completed_at = i
    complete = result.complete

    values = filled_values(state)
    expected = conv.get("expected") or {}
    user_text = conv["turns"]
    assumed = sorted(
        k for k, v in values.items()
        if k not in expected or not said_somewhere(k, v, user_text, state, catalog)
    )
    matches = values == expected
    declined_ok = bool(state.declined) == bool(conv.get("declined", False))

    if assumed:
        outcome = "assumed"
    elif complete and conv["complete"] and matches and declined_ok:
        outcome = "exact"
    elif not complete and not conv["complete"] and matches and declined_ok:
        outcome = "exact"
    elif complete:
        outcome = "complete_different"
    else:
        outcome = "incomplete"

    return {
        "id": conv["id"],
        "category": conv["category"],
        "outcome": outcome,
        "turns": len(conv["turns"]),
        "completed_at_turn": completed_at,
        "assumed": assumed,
        "mismatched": sorted(k for k in set(values) | set(expected) if values.get(k) != expected.get(k)),
        "rejected_by_guard": len(state.rejections),
        "replies": replies,
    }


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="stub", choices=["stub", "anthropic"])
    parser.add_argument("--no-grounding", action="store_true", help="ablation: accept every extraction")
    parser.add_argument("--out", default=str(RESULTS))
    args = parser.parse_args(argv)

    catalog = get_catalog()
    llm = make_llm(args.provider)
    conversations = yaml.safe_load(CONVERSATIONS.read_text(encoding="utf-8"))
    runs = [run_conversation(c, llm, catalog, guard=not args.no_grounding) for c in conversations]

    counts = Counter(r["outcome"] for r in runs)
    report = {
        "provider": args.provider,
        "grounding": not args.no_grounding,
        "conversations": len(runs),
        "outcomes": {k: counts.get(k, 0) for k in ("exact", "complete_different", "incomplete", "assumed")},
        "turns_to_complete": dict(sorted(Counter(r["completed_at_turn"] for r in runs
                                                 if r["completed_at_turn"]).items())),
        "guard_rejections": sum(r["rejected_by_guard"] for r in runs),
        "runs": runs,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"provider={args.provider} grounding={'on' if report['grounding'] else 'OFF'} "
          f"conversations={len(runs)}")
    for k, v in report["outcomes"].items():
        print(f"  {k:<20}{v}")
    print(f"  turns to complete   {report['turns_to_complete']}")
    print(f"  guard rejections    {report['guard_rejections']}")
    for r in runs:
        if r["outcome"] != "exact":
            print(f"  {r['id']}: {r['outcome']} assumed={r['assumed']} mismatched={r['mismatched']}")
    return report


if __name__ == "__main__":
    report = main()
    sys.exit(1 if report["outcomes"]["assumed"] and report["grounding"] else 0)
