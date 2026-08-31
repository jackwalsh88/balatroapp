"""System prompts and prompt assembly.

Spec section 5 fixes the shape of these. The required advisor behaviours are
not style preferences - each one is drawn from a specific failure observed
while doing this by hand, and the comments say which.

``PROMPT_VERSION`` participates in the cache key. Bump it on any change here.
Spec section 5b: stale advice from an old prompt is worse than no cache.
"""

from __future__ import annotations

import json
from typing import Any

from ..core import data

__all__ = [
    "PROMPT_VERSION",
    "DECISION_SYSTEM",
    "BEGINNER_RENDER_SYSTEM",
    "build_decision_user",
    "build_render_user",
]

PROMPT_VERSION = 3

# --------------------------------------------------------------------------
# Stage 1: the decision. Mode-independent by construction - nothing in this
# prompt mentions the reader's experience level, so the verbosity setting
# cannot reach the choice of action.
# --------------------------------------------------------------------------

DECISION_SYSTEM = """\
You advise on Balatro decisions. All scores below are computed exactly by a
deterministic engine. Never recompute or estimate a score. If you need a number
that is not provided, say so rather than guessing.

Report uncertainty explicitly. A wrong confident answer is worse than an
acknowledged gap.

RULES, each of which exists because of a specific error made without them:

1. Never estimate a score. Quote only numbers that appear in the state or in
   the computed candidates. If CANDIDATE PLAYS is absent, say so instead of
   working one out. An estimate that read 36,000 against an actual 20,700 is
   why this system separates arithmetic from judgment.

2. A candidate marked "exact: false" has an UNMODELLED effect in it. Its score
   is a FLOOR, not a score. Say so, name what is unmodelled, and put it in
   UNCERTAIN. Never present a floor as a result.

3. Check current_contribution on every joker before advising anything else. A
   joker producing X1 or +0 in the current configuration is a dead slot, and is
   the highest-value finding available. It is also easy to miss.

4. Joker slots have opportunity cost. With Joker Stencil in play an empty slot
   has a concrete multiplier value, so buying a joker can be strictly negative.
   Compute the delta from the numbers given. Do not assume filling slots is good.

5. Respect stickers. Eternal means the purchase is irreversible and the joker
   can never be sold. Perishable means it expires. Either can turn a marginal
   buy into a bad one.

6. Flag the economy effect of a play. Gold enhancements pay only if the card is
   still held in hand at the end of the round, so playing one forfeits the
   payout; gold_forfeited reports the amount.

7. Do not assert a mechanic you are not sure of. If you are unsure whether
   something works a particular way, say that it is unverified. Two mechanics
   are listed below as explicitly UNRESOLVED: you may reason about them as
   unknowns, and you may not assert them true or false.

OUTPUT FORMAT. Emit exactly these four labelled sections, then the action block.

DECISION: <one line - the action, nothing else>
REASONING: <2-4 sentences, referencing exact numbers from the data given>
ALTERNATIVES: <what else was considered and why it lost>
UNCERTAIN: <anything you could not determine, or the word "none">

Then a fenced JSON block giving the same decision in machine-readable form, so
it can be rule-checked before it reaches the player:

```json
{"kind": "play", "cards": [0, 1]}
```

Valid action shapes:
  {"kind": "play",    "cards": [<indices into current_hand>]}
  {"kind": "discard", "cards": [<indices into current_hand>]}
  {"kind": "buy",     "slots": [<shop slot numbers>], "sell": [<joker positions to sell first>]}
  {"kind": "sell",    "sell": [<joker positions>]}
  {"kind": "reroll"}
  {"kind": "pick",    "picks": [<indices into pack_open.cards>]}
  {"kind": "skip"}
  {"kind": "none"}

The JSON block is mandatory and must match the DECISION line. Advice whose
action block is missing, malformed, or illegal in the current state is
discarded and never shown."""


# --------------------------------------------------------------------------
# Stage 2: beginner rendering. Runs ONLY after a decision exists, is given that
# decision, and is forbidden from changing it. The DECISION line is copied
# through in code, so this prompt cannot alter the recommendation even if it
# tries - spec 5a's hard constraint is structural, not merely instructed.
# --------------------------------------------------------------------------

BEGINNER_RENDER_SYSTEM = """\
You are re-explaining a Balatro decision that has ALREADY BEEN MADE, for
someone still learning the game.

You may not change the decision. It is fixed. Your job is only to explain it
more plainly. If the decision looks wrong to you, explain it anyway and put
your concern in UNCERTAIN.

HOW TO EXPLAIN:

- Lead with the action, then the why.
- Give ONE reason, the dominant one. The expert version may list four competing
  considerations; pick the one that actually decides it and say so.
- Gloss every piece of jargon on first use, e.g. "Xmult (a multiplier that
  multiplies your score rather than adding to it)". Terms already glossed
  earlier this session are listed below - do not re-explain those.
- Say what each joker involved actually does. The player may not remember. The
  descriptions are given to you; use them.
- Show the arithmetic in words, not just symbols: "right now your multiplier is
  doubled once; after selling, it gets doubled twice - roughly 35% more score."
- Drop marginal considerations entirely. A $3 Gold forfeit or a small
  interest-cap effect is noise to someone learning. Omit it unless it changes
  the decision.
- If the recommendation is counterintuitive, say so and say why the obvious
  move is wrong: "Filling an empty joker slot usually helps. Not here, and this
  is why."

You are not running a tutorial. Do not teach Balatro from scratch, quiz the
player, or pad the answer. Answer the question that was asked, in plainer words.

Every number you use must already appear in the expert explanation or the data
below. Do not compute new ones.

Emit exactly:

REASONING: <plain-language explanation, one dominant reason>
ALTERNATIVES: <the main other option and why it loses, or "none worth noting">
UNCERTAIN: <anything genuinely unknown, or the word "none">

Do not emit a DECISION line - it is carried over unchanged."""


# --------------------------------------------------------------------------
# User-message assembly
# --------------------------------------------------------------------------


def _joker_brief(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Jokers with their static descriptions and live contribution attached.

    The description comes from the static table so the model does not have to
    recall what a joker does, and current_contribution is surfaced right beside
    it so rule 3 (dead slots) is checkable rather than inferable.
    """
    out = []
    for joker in state.get("jokers") or []:
        entry = data.joker(joker["key"]) or {}
        if not entry:
            does = "NOT IN THE JOKER TABLE AT ALL - treat its effect as wholly unknown."
        elif entry.get("description"):
            does = entry["description"]
        else:
            # Enumerated but not yet modelled: we know it is a real joker and
            # what it is called, and nothing about what it does. Saying so beats
            # a null, which reads as "does nothing".
            does = (
                "EFFECT NOT YET SOURCED. This is a real joker and its name is "
                "correct, but this project has no description or numbers for it. "
                "Do not infer its effect from its name."
            )
        out.append({
            "position": joker["position"],
            "name": joker.get("name") or entry.get("name") or joker["key"],
            "key": joker["key"],
            "does": does,
            "edition": joker.get("edition", "base"),
            "stickers": joker.get("stickers") or [],
            "sell_value": joker.get("sell_value"),
            "counter": (joker.get("internal_state") or {}).get("counter"),
            "current_contribution": joker.get("current_contribution"),
            "modelled_by_scorer": bool(entry) and not entry.get("unmodelled"),
            "rarity": entry.get("rarity"),
        })
    return out


def _unresolved_mechanics() -> list[dict[str, str]]:
    return [
        {"id": m["id"], "statement": m["statement"]}
        for m in data.mechanics()
        if m["status"] == "unresolved"
    ]


def _shop_brief(state: dict[str, Any]) -> dict[str, Any] | None:
    shop = state.get("shop")
    if not shop:
        return None
    items = []
    for item in shop.get("items") or []:
        entry = data.joker(item["key"]) if item["kind"] == "joker" else None
        items.append({
            **{k: v for k, v in item.items() if k != "name"},
            "name": item.get("name") or (entry or {}).get("name") or item["key"],
            "does": (entry or {}).get("description"),
            "affordable": item["price"] <= state["run"]["money"],
        })
    return {**shop, "items": items}


def build_decision_user(
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    discards: list[dict[str, Any]],
    question: str | None = None,
    *,
    extra_constraints: str | None = None,
) -> str:
    """Assemble the stage-1 user message.

    Volatile content (the question, any regeneration constraint) goes last so a
    cached prefix stays intact across retries.
    """
    blocks: list[str] = []

    presented = dict(state)
    presented["jokers"] = _joker_brief(state)
    if presented.get("shop"):
        presented["shop"] = _shop_brief(state)
    blocks.append("CANONICAL STATE\n" + json.dumps(presented, indent=2, sort_keys=True))

    if candidates:
        blocks.append(
            "CANDIDATE PLAYS (computed exactly; ranked: clears blind, then score "
            "descending, then gold forfeited ascending)\n"
            + json.dumps(candidates, indent=2)
        )
    else:
        blocks.append(
            "CANDIDATE PLAYS: none were computed for this state. You must not "
            "produce a score of your own. If the question needs one, say that it "
            "is unavailable."
        )

    if discards:
        blocks.append(
            "DISCARD CANDIDATES. floor_score is the guaranteed outcome from the "
            "kept cards if every draw misses. expected_score and p_clears_blind "
            "are null because draw evaluation is not implemented - reason about "
            "the draw qualitatively from the deck contents, and do not invent a "
            "probability.\n" + json.dumps(discards, indent=2)
        )

    unresolved = _unresolved_mechanics()
    if unresolved:
        blocks.append(
            "UNRESOLVED MECHANICS - reason about these as unknowns; asserting "
            "either direction is a validation failure\n"
            + json.dumps(unresolved, indent=2)
        )

    blocks.append(f"QUESTION\n{question or 'What should I do?'}")

    if extra_constraints:
        blocks.append(
            "YOUR PREVIOUS ANSWER FAILED VALIDATION. Fix these and answer "
            "again:\n" + extra_constraints
        )

    return "\n\n".join(blocks)


def build_render_user(
    state: dict[str, Any],
    expert: dict[str, Any],
    glossed: list[str],
) -> str:
    """Assemble the stage-2 beginner user message."""
    jokers = _joker_brief(state)
    blocks = [
        "THE DECISION (fixed - explain it, do not change it)\n" + (expert.get("decision") or ""),
        "EXPERT EXPLANATION (your source for every number)\n"
        + "\n".join(
            f"{label}: {expert.get(key) or 'none'}"
            for label, key in (
                ("REASONING", "reasoning"),
                ("ALTERNATIVES", "alternatives"),
                ("UNCERTAIN", "uncertain"),
            )
        ),
        "JOKERS IN PLAY AND WHAT THEY DO\n" + json.dumps(jokers, indent=2),
    ]
    if glossed:
        blocks.append(
            "ALREADY GLOSSED THIS SESSION - do not re-explain these terms: "
            + ", ".join(sorted(glossed))
        )
    return "\n\n".join(blocks)
