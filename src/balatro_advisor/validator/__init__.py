"""Deterministic validation between the advisor and the user.

Spec section 5d::

    advisor (LLM)  ->  validator (pure code)  ->  output
                            | fail
                      regenerate once, then refuse

What this catches: advice that contradicts the game rules or the current state.
What it does not catch: advice that is legal but strategically wrong. Selling
the wrong joker is a valid action and no rule check will flag it - judgment
quality is measured by the decision log, not here.

Never show unvalidated advice. A refusal is recoverable; confident wrong advice
is what loses trust in the product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..core import data

__all__ = ["Finding", "Report", "validate_advice"]

FAIL = "fail"
FLAG = "flag"


@dataclass
class Finding:
    check: str
    severity: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check}: {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == FAIL]

    @property
    def flags(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == FLAG]

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, check: str, message: str) -> None:
        self.findings.append(Finding(check, FAIL, message))

    def flag(self, check: str, message: str) -> None:
        self.findings.append(Finding(check, FLAG, message))

    def constraint_text(self) -> str:
        """The failed checks, phrased for appending to a regeneration prompt."""
        return "\n".join(f"- {f.check}: {f.message}" for f in self.failures)


# --------------------------------------------------------------------------
# Legality - the action must be possible right now
# --------------------------------------------------------------------------


def _check_legality(report: Report, action: dict[str, Any], state: dict[str, Any]) -> None:
    kind = action.get("kind", "none")
    phase = state.get("phase")
    hand = state.get("current_hand") or []
    res = state["resources"]

    if kind in ("play", "discard") and phase != "playing":
        report.fail("legality.phase", f"advice is to {kind} a hand, but the phase is '{phase}'")
        return
    if kind == "buy" and phase != "shop":
        report.fail("legality.phase", f"advice is to buy, but the phase is '{phase}'")
        return
    if kind == "pick" and phase != "pack_open":
        report.fail("legality.phase", f"advice is to pick from a pack, but the phase is '{phase}'")
        return

    if kind in ("play", "discard"):
        cards = action.get("cards")
        if not isinstance(cards, list) or not cards:
            report.fail(f"legality.{kind}", "no cards named")
            return
        if len(set(cards)) != len(cards):
            report.fail(f"legality.{kind}", f"the same card is named twice: {cards}")
        out_of_range = [c for c in cards if not isinstance(c, int) or not 0 <= c < len(hand)]
        if out_of_range:
            report.fail(
                f"legality.{kind}",
                f"cards {out_of_range} are not in current_hand (which holds {len(hand)} cards)",
            )
        if not 1 <= len(cards) <= 5:
            report.fail(f"legality.{kind}", f"{len(cards)} cards named; a {kind} must be 1 to 5 cards")

    if kind == "play":
        if res["hands_remaining"] <= 0:
            report.fail("legality.play", "no hands remaining")
        entry = data.blind((state.get("blind") or {}).get("key"))
        cards = action.get("cards") or []
        if entry and entry.get("constraint") == "min_played_cards_5" and len(cards) != 5:
            report.fail(
                "legality.play",
                f"{entry['name']} requires exactly 5 cards; advice plays {len(cards)}",
            )

    if kind == "discard" and res["discards_remaining"] <= 0:
        report.fail("legality.discard", "no discards remaining")

    sells = action.get("sell") or ([action["sell_slot"]] if "sell_slot" in action else [])
    jokers = state.get("jokers") or []
    by_position = {j["position"]: j for j in jokers}
    for pos in sells:
        target = by_position.get(pos)
        if target is None:
            report.fail("legality.sell", f"no joker in slot {pos} to sell")
            continue
        if "eternal" in (target.get("stickers") or []):
            report.fail(
                "legality.sell",
                f"{data.joker_name(target['key'])} is Eternal and cannot be sold",
            )

    if kind == "buy":
        shop = state.get("shop") or {}
        items = {i["slot"]: i for i in shop.get("items") or []}
        slots = action.get("slots") or ([action["slot"]] if "slot" in action else [])
        total = 0
        joker_buys = consumable_buys = 0
        for slot in slots:
            item = items.get(slot)
            if item is None:
                report.fail("legality.buy", f"shop slot {slot} does not exist")
                continue
            total += item["price"]
            if item["kind"] == "joker":
                joker_buys += 1
            elif item["kind"] == "consumable":
                consumable_buys += 1
        if total > state["run"]["money"]:
            report.fail(
                "legality.buy",
                f"purchase costs ${total} but only ${state['run']['money']} is available",
            )
        held_jokers = len(jokers) - len(sells)
        if joker_buys and held_jokers + joker_buys > res["joker_slots_total"]:
            report.fail(
                "legality.buy",
                f"buying {joker_buys} joker(s) with {held_jokers} held exceeds "
                f"{res['joker_slots_total']} joker slots, and the advice sells nothing to make room",
            )
        held_consumables = len(state.get("consumables") or [])
        if consumable_buys and held_consumables + consumable_buys > res["consumable_slots_total"]:
            report.fail(
                "legality.buy",
                f"buying {consumable_buys} consumable(s) with {held_consumables} held "
                f"exceeds {res['consumable_slots_total']} consumable slots",
            )

    if kind == "reroll":
        cost = (state.get("shop") or {}).get("reroll_cost")
        if cost is not None and cost > state["run"]["money"]:
            report.fail("legality.reroll", f"reroll costs ${cost}, only ${state['run']['money']} available")

    if kind == "pick":
        pack = state.get("pack_open") or {}
        picks = action.get("picks") or []
        allowed = pack.get("choices_allowed")
        if allowed is not None and len(picks) > allowed:
            report.fail(
                "legality.pick",
                f"advice picks {len(picks)} cards but this pack allows {allowed}",
            )


# --------------------------------------------------------------------------
# Arithmetic - no invented numbers
# --------------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)")


def _numbers_in(text: str) -> list[float]:
    out = []
    for raw in _NUMBER.findall(text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def _collect(value: Any, into: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        into.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            _collect(v, into)
    elif isinstance(value, list):
        for v in value:
            _collect(v, into)


def known_numbers(
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    discards: list[dict[str, Any]],
) -> set[float]:
    """Every number the advice is entitled to quote.

    Deliberately generous about *derived* values (differences, ratios,
    percentages between two computed scores) and strict about everything else.
    A number with no source here is the 36,000-against-20,700 failure.
    """
    known: set[float] = set()
    _collect(state, known)
    _collect(candidates, known)
    _collect(discards, known)

    scores = [c["score"] for c in candidates] + [d["floor_score"] for d in discards]
    requirement = (state.get("blind") or {}).get("requirement")
    if requirement is not None:
        scores.append(requirement)

    # Differences and percentage relationships between computed scores are
    # legitimate reasoning, not invention.
    for a in scores:
        for b in scores:
            if a == b:
                continue
            known.add(float(abs(a - b)))
            if b:
                ratio = a / b
                known.add(round(ratio, 2))
                known.add(round(ratio * 100))
                known.add(round(abs(ratio - 1) * 100))
                known.add(round(abs(ratio - 1) * 100, 1))

    # Cardinalities of the things the advice can legitimately count: "11 other
    # plays scored lower", "4 jokers held", "3 cards in hand".
    for collection in (
        candidates, discards, state.get("current_hand") or [],
        state.get("jokers") or [], state.get("consumables") or [],
        (state.get("shop") or {}).get("items") or [],
        (state.get("pack_open") or {}).get("cards") or [],
    ):
        known.add(float(len(collection)))
        known.add(float(max(0, len(collection) - 1)))

    # Small integers are card counts, slot indices and list positions.
    known.update(float(i) for i in range(0, 11))
    return known


def _check_arithmetic(
    report: Report,
    advice: dict[str, Any],
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    discards: list[dict[str, Any]],
) -> None:
    known = known_numbers(state, candidates, discards)
    prose = " ".join(
        str(advice.get(k) or "") for k in ("decision", "reasoning", "alternatives")
    )
    unsourced = []
    for number in _numbers_in(prose):
        if any(abs(number - k) < 0.51 for k in known):
            continue
        unsourced.append(number)
    if unsourced:
        report.fail(
            "arithmetic.unsourced",
            "these numbers appear in the advice but in no computed value: "
            + ", ".join(f"{n:g}" for n in sorted(set(unsourced)))
            + ". Every score quoted must come from candidate_plays or discard_candidates.",
        )

    # A non-exact score is a floor, not a score, and must be flagged as such.
    inexact = {c["score"] for c in candidates if not c.get("exact", True)}
    inexact |= {d["floor_score"] for d in discards if not d.get("exact", True)}
    quoted = set(_numbers_in(str(advice.get("decision") or "") + " " + str(advice.get("reasoning") or "")))
    overlap = inexact & quoted
    if overlap and not (advice.get("uncertain") or "").strip():
        report.fail(
            "arithmetic.inexact_quoted_as_fact",
            f"score(s) {sorted(overlap)} come from a candidate the scorer could not "
            f"compute exactly, but UNCERTAIN is empty. An inexact score is a floor and "
            f"must be presented as one.",
        )


# --------------------------------------------------------------------------
# Consistency - the advice matches its own reasoning
# --------------------------------------------------------------------------

# Claiming the blind is beaten. The opposite of the shortfall check below: that
# one catches silence about a shortfall, this catches asserting the reverse.
_CLAIMS_IT_CLEARS = re.compile(
    r"\b(clears?|beats?|meets?|exceeds?)\s+(the\s+)?(blind|requirement|target)\b"
    r"|\benough to (clear|beat|meet)\b|\bwill clear\b|\bgets? you (there|over)\b",
    re.I,
)

# Language that explains trading score away on purpose. Its presence turns a
# dominated-play finding from an error into a judgment call.
_TRADEOFF = re.compile(
    r"\b(keep|keeps|keeping|hold|holds|holding|preserv\w+|sav\w+|next hand|"
    r"later hand|remaining hand|steel|gold|set ?up|cycle|discard|draw)\b",
    re.I,
)

# Superlatives specifically about SCORE. Deliberately narrow: "the best play"
# can mean best strategically, but "the highest-scoring play" is a factual
# claim the scorer can settle.
_SCORE_SUPERLATIVE = re.compile(
    r"\b(highest[- ]scoring|scores? the most|highest score|best score|"
    r"top[- ]scoring|biggest score|most points|maximi[sz]es the score)\b",
    re.I,
)

_ACKNOWLEDGES_SHORTFALL = re.compile(
    r"\b(not clear|won'?t clear|does not clear|doesn'?t clear|short of|falls short|"
    r"still need|next hand|remaining hand|two hands|chip away)\b",
    re.I,
)


# Negation, checked per sentence. Without it "does not clear the blind" reads
# as a claim that it does - the exact failure this check exists to catch,
# inverted.
_NEGATED = re.compile(
    r"\b(not|never|fails? to|unable|cannot|can'?t|won'?t|doesn'?t|does n'?t|"
    r"short of|falls short|instead of)\b|n't\b",
    re.I,
)


def _asserts(pattern: re.Pattern, text: str) -> bool:
    """True when some sentence makes the claim WITHOUT negating it."""
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        if pattern.search(sentence) and not _NEGATED.search(sentence):
            return True
    return False


def _check_consistency(
    report: Report,
    advice: dict[str, Any],
    action: dict[str, Any],
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if action.get("kind") == "play":
        cards = sorted(action.get("cards") or [])
        match = next((c for c in candidates if sorted(c["cards"]) == cards), None)
        if match is None:
            report.fail(
                "consistency.play_not_enumerated",
                f"the recommended play {cards} is not in candidate_plays",
            )
        elif match.get("clears_blind") is False and state["resources"]["hands_remaining"] > 1:
            text = " ".join(str(advice.get(k) or "") for k in ("decision", "reasoning", "uncertain"))
            if not _ACKNOWLEDGES_SHORTFALL.search(text):
                report.fail(
                    "consistency.unacknowledged_shortfall",
                    f"the recommended play scores {match['score']} against a requirement "
                    f"of {(state.get('blind') or {}).get('requirement')} and does not clear "
                    f"the blind, but the advice does not say so",
                )

    # -- claims the scorer can settle outright --------------------------
    if action.get("kind") == "play":
        cards = sorted(action.get("cards") or [])
        match = next((c for c in candidates if sorted(c["cards"]) == cards), None)
        prose_all = " ".join(
            str(advice.get(k) or "") for k in ("decision", "reasoning", "alternatives")
        )

        if match is not None:
            # 1. Asserting the blind is cleared when the scorer says it is not.
            if match.get("clears_blind") is False and _asserts(_CLAIMS_IT_CLEARS, prose_all):
                report.fail(
                    "consistency.false_clear_claim",
                    f"the advice says this clears the blind, but {match['score']} is "
                    f"short of the {(state.get('blind') or {}).get('requirement')} "
                    f"required",
                )

            # 2. Calling it the highest-scoring play when it is not.
            if _asserts(_SCORE_SUPERLATIVE, prose_all):
                best = max(candidates, key=lambda c: c["score"])
                if best["score"] > match["score"]:
                    report.fail(
                        "consistency.false_superlative",
                        f"the advice calls this the highest-scoring play, but "
                        f"{best['cards']} scores {best['score']} against its "
                        f"{match['score']}",
                    )

            # 3. A strictly better play exists and no tradeoff is offered.
            #    Domination is judged only on axes where "better" is
            #    unambiguous - score, and the gold/steel given up. Card count
            #    is deliberately excluded: playing fewer cards preserves
            #    held-in-hand effects but draws fewer replacements, and which
            #    of those matters is a judgment the scorer cannot make.
            dominators = [
                c for c in candidates
                if c["score"] > match["score"]
                and c.get("gold_forfeited", 0) <= match.get("gold_forfeited", 0)
                and c.get("steel_forfeited", 0) <= match.get("steel_forfeited", 0)
                and not (match.get("clears_blind") and not c.get("clears_blind"))
            ]
            #    FLAGGED, not failed. Spec 5d draws the validator's boundary at
            #    rules rather than judgment - "selling the wrong joker is a
            #    valid action and no rule check will flag it" - and taking a
            #    lower score to set up a later hand is a legitimate choice the
            #    scorer cannot evaluate. So this surfaces the arithmetic loudly
            #    and lets the human decide, rather than blocking a play that
            #    may well be right.
            if dominators and not _TRADEOFF.search(prose_all):
                best = max(dominators, key=lambda c: c["score"])
                report.flag(
                    "consistency.dominated_play",
                    f"{best['cards']} scores {best['score']} against this play's "
                    f"{match['score']} while giving up no more gold or steel, and "
                    f"the advice gives no reason for taking the lower score",
                )

    # A joker's claimed contribution must not contradict what the state reports.
    prose = " ".join(str(advice.get(k) or "") for k in ("decision", "reasoning", "alternatives"))
    for joker in state.get("jokers") or []:
        contribution = joker.get("current_contribution")
        if not contribution:
            continue
        name = joker.get("name") or data.joker_name(joker["key"])
        for claim in re.finditer(
            rf"{re.escape(name)}[^.;]{{0,60}}?[xX]\s?(\d+(?:\.\d+)?)", prose
        ):
            claimed = float(claim.group(1))
            actual = float(contribution.get("xmult", 1))
            if abs(claimed - actual) > 1e-6:
                report.fail(
                    "consistency.joker_contribution",
                    f"the advice says {name} gives X{claimed:g}, but current_contribution "
                    f"reports X{actual:g} right now",
                )


# --------------------------------------------------------------------------
# Mechanics - no asserted rules outside the known set
# --------------------------------------------------------------------------

_HEDGED = re.compile(
    r"\b(unknown|unverified|not verified|unclear|cannot confirm|can'?t confirm|"
    r"uncertain|not sure|unresolved|may or may not|not established)\b",
    re.I,
)

_RULE_ASSERTION = re.compile(r"\b(always|never|cannot|can'?t|must|guaranteed|will not)\b", re.I)

# Errors already made by hand, stated as patterns so they cannot recur silently.
_KNOWN_ERRORS = [
    (
        re.compile(r"jumbo[^.]{0,60}?\btwo\b|jumbo[^.]{0,40}?\b2\b(?!\d)", re.I),
        "Jumbo packs allow ONE pick, not two. This exact error was made during "
        "manual testing (see data/mechanics.json: pack_picks_jumbo_mega).",
    ),
    (
        re.compile(r"mega[^.]{0,60}?\bone\b|mega[^.]{0,40}?\b1\b(?!\d)", re.I),
        "Mega packs allow TWO picks, not one. This exact error was made during "
        "manual testing (see data/mechanics.json: pack_picks_jumbo_mega).",
    ),
]


def _check_mechanics(report: Report, advice: dict[str, Any]) -> None:
    prose = " ".join(
        str(advice.get(k) or "")
        for k in ("decision", "reasoning", "alternatives", "uncertain")
    )
    if not prose.strip():
        return

    for pattern, message in _KNOWN_ERRORS:
        if pattern.search(prose):
            report.fail("mechanics.known_error", message)

    for mechanic in data.mechanics():
        if mechanic["status"] != "unresolved":
            continue
        hits = [k for k in mechanic["keywords"] if re.search(rf"\b{re.escape(k)}\b", prose, re.I)]
        if len(hits) < 2:
            continue  # one keyword is a passing mention, not an assertion
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", prose) if any(
            re.search(rf"\b{re.escape(k)}\b", s, re.I) for k in hits
        )]
        if any(not _HEDGED.search(s) for s in sentences):
            report.fail(
                "mechanics.unresolved_asserted",
                f"'{mechanic['id']}' is unresolved ({mechanic['statement']}) and must not "
                f"be asserted in either direction. Say it is unknown, or leave it out.",
            )

    verified_words = {
        word
        for m in data.mechanics()
        if m["status"] == "verified"
        for word in m["keywords"]
    }
    for sentence in re.split(r"(?<=[.!?])\s+", prose):
        if not _RULE_ASSERTION.search(sentence):
            continue
        if any(re.search(rf"\b{re.escape(w)}\b", sentence, re.I) for w in verified_words):
            continue
        if _HEDGED.search(sentence):
            continue
        report.flag(
            "mechanics.unknown_rule_asserted",
            f"states a rule not backed by data/mechanics.json, worth a human look: "
            f"{sentence.strip()[:160]}",
        )


# --------------------------------------------------------------------------


def validate_advice(
    advice: dict[str, Any],
    state: dict[str, Any],
    candidates: list[dict[str, Any]] | None = None,
    discards: list[dict[str, Any]] | None = None,
) -> Report:
    """Run every check. Advice with any failure must never reach the user.

    ``advice`` is the parsed advisor output: ``decision``, ``reasoning``,
    ``alternatives``, ``uncertain`` and a structured ``action``.
    """
    candidates = candidates or []
    discards = discards or []
    report = Report()
    action = advice.get("action") or {"kind": "none"}

    if not (advice.get("decision") or "").strip():
        report.fail("format.decision", "no DECISION line")

    _check_legality(report, action, state)
    _check_arithmetic(report, advice, state, candidates, discards)
    _check_consistency(report, advice, action, state, candidates)
    _check_mechanics(report, advice)
    return report
