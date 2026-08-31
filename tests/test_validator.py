"""Validator checks, one per rule in spec section 5d.

The validator catches advice that contradicts the rules or the state. It does
not catch advice that is legal but strategically wrong - selling the wrong
joker is a valid action and no rule check will flag it. That distinction is
tested here explicitly, so nobody later mistakes a passing validator for a
correctness guarantee.
"""

from __future__ import annotations

import pytest

from balatro_advisor import validator
from balatro_advisor.core import enumerate as enumerate_module
from balatro_advisor.core import schema


@pytest.fixture
def playing(state_factory):
    return schema.load_state(state_factory())


@pytest.fixture
def candidates(playing):
    return enumerate_module.enumerate_plays(playing)


def advice(**kw):
    base = {
        "decision": "Play cards [0, 1] (pair).",
        "reasoning": "",
        "alternatives": "",
        "uncertain": "",
        "action": {"kind": "play", "cards": [0, 1]},
    }
    base.update(kw)
    return base


def check(adv, state, candidates=None, discards=None):
    return validator.validate_advice(adv, state, candidates or [], discards or [])


# -- legality ---------------------------------------------------------------


def test_clean_advice_passes(playing, candidates):
    assert check(advice(), playing, candidates).ok


def test_card_not_in_hand_is_rejected(playing, candidates):
    report = check(advice(action={"kind": "play", "cards": [0, 99]}), playing, candidates)
    assert not report.ok
    assert any("not in current_hand" in f.message for f in report.failures)


def test_duplicate_card_is_rejected(playing, candidates):
    report = check(advice(action={"kind": "play", "cards": [0, 0]}), playing, candidates)
    assert any("twice" in f.message for f in report.failures)


def test_six_card_play_is_rejected(state_factory, card):
    state = schema.load_state(state_factory(
        current_hand=[card(r, "hearts") for r in ("2", "3", "4", "5", "6", "7")]
    ))
    report = check(advice(action={"kind": "play", "cards": [0, 1, 2, 3, 4, 5]}), state)
    assert any("1 to 5 cards" in f.message for f in report.failures)


def test_playing_a_hand_while_in_the_shop_is_rejected(state_factory):
    state = schema.load_state(state_factory(
        phase="shop", blind=None, current_hand=[],
        shop={"reroll_cost": 5, "items": []},
    ))
    report = check(advice(), state)
    assert any("phase is 'shop'" in f.message for f in report.failures)


def test_discarding_with_no_discards_left_is_rejected(state_factory):
    state = schema.load_state(state_factory(
        resources={"hands_remaining": 1, "discards_remaining": 0}
    ))
    report = check(
        advice(decision="Discard.", action={"kind": "discard", "cards": [2]}), state
    )
    assert any("no discards remaining" in f.message for f in report.failures)


def test_selling_an_eternal_joker_is_rejected(state_factory):
    state = schema.load_state(state_factory(jokers=[
        {"position": 0, "key": "j_gros_michel", "stickers": ["eternal"]}
    ]))
    report = check(
        advice(decision="Sell it.", action={"kind": "sell", "sell": [0]}), state
    )
    assert any("Eternal" in f.message for f in report.failures)


def test_buying_beyond_your_money_is_rejected(state_factory):
    state = schema.load_state(state_factory(
        phase="shop", blind=None, current_hand=[], run={"ante": 1, "money": 3},
        shop={"reroll_cost": 5, "items": [
            {"slot": 0, "kind": "joker", "key": "j_cavendish", "price": 9}
        ]},
    ))
    report = check(
        advice(decision="Buy Cavendish.", action={"kind": "buy", "slots": [0]}), state
    )
    assert any("only $3" in f.message for f in report.failures)


def test_buying_past_full_joker_slots_without_selling_is_rejected(state_factory):
    state = schema.load_state(state_factory(
        phase="shop", blind=None, current_hand=[], run={"ante": 1, "money": 50},
        resources={"hands_remaining": 0, "discards_remaining": 0, "joker_slots_total": 1},
        jokers=[{"position": 0, "key": "j_joker"}],
        shop={"reroll_cost": 5, "items": [
            {"slot": 0, "kind": "joker", "key": "j_sly", "price": 3}
        ]},
    ))
    report = check(advice(decision="Buy it.", action={"kind": "buy", "slots": [0]}), state)
    assert any("joker slots" in f.message for f in report.failures)


def test_buying_with_a_sale_in_the_same_advice_is_allowed(state_factory):
    state = schema.load_state(state_factory(
        phase="shop", blind=None, current_hand=[], run={"ante": 1, "money": 50},
        resources={"hands_remaining": 0, "discards_remaining": 0, "joker_slots_total": 1},
        jokers=[{"position": 0, "key": "j_joker"}],
        shop={"reroll_cost": 5, "items": [
            {"slot": 0, "kind": "joker", "key": "j_sly", "price": 3}
        ]},
    ))
    report = check(
        advice(
            decision="Sell Joker, buy Sly Joker.",
            action={"kind": "buy", "slots": [0], "sell": [0]},
        ),
        state,
    )
    assert report.ok


def test_picking_more_than_the_pack_allows_is_rejected(state_factory):
    state = schema.load_state(state_factory(
        phase="pack_open", blind=None, current_hand=[],
        resources={"hands_remaining": 0, "discards_remaining": 0},
        pack_open={
            "key": "p_celestial_jumbo", "choices_allowed": 1,
            "cards": [{"key": "c_mars"}, {"key": "c_sun"}],
        },
    ))
    report = check(
        advice(decision="Take both.", action={"kind": "pick", "picks": [0, 1]}), state
    )
    assert any("allows 1" in f.message for f in report.failures)


def test_psychic_rejects_a_short_play(state_factory, card):
    state = schema.load_state(state_factory(
        current_hand=[card(r, "hearts") for r in ("2", "3", "4", "5", "6", "7")],
        blind={"type": "boss", "key": "bl_psychic", "requirement": 1000},
    ))
    report = check(advice(action={"kind": "play", "cards": [0, 1]}), state)
    assert any("exactly 5 cards" in f.message for f in report.failures)


# -- arithmetic -------------------------------------------------------------


def test_the_36000_case_is_caught(playing, candidates):
    """The check that would have caught 36,000 quoted against an actual 20,700."""
    report = check(
        advice(reasoning="This scores about 36,000, easily clearing the blind."),
        playing, candidates,
    )
    assert not report.ok
    assert any("36000" in f.message for f in report.failures)


def test_a_computed_score_may_be_quoted(playing, candidates):
    top = candidates[0]
    report = check(
        advice(reasoning=f"It scores {top['score']}, the best available."),
        playing, candidates,
    )
    assert report.ok


def test_a_difference_between_two_computed_scores_may_be_quoted(playing, candidates):
    delta = candidates[0]["score"] - candidates[1]["score"]
    report = check(
        advice(reasoning=f"That is {delta} more than the next best play."),
        playing, candidates,
    )
    assert report.ok


def test_an_inexact_score_quoted_without_hedging_is_rejected(state_factory):
    state = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": "j_blueprint"}]
    ))
    cands = enumerate_module.enumerate_plays(state)
    top = cands[0]
    assert top["exact"] is False
    report = check(
        advice(reasoning=f"This scores {top['score']}.", uncertain=""),
        state, cands,
    )
    assert any("inexact" in f.check for f in report.failures)


def test_an_inexact_score_is_fine_when_acknowledged(state_factory):
    state = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": "j_blueprint"}]
    ))
    cands = enumerate_module.enumerate_plays(state)
    report = check(
        advice(
            reasoning=f"The floor is {cands[0]['score']}.",
            uncertain="Blueprint's contribution is not modelled, so that is a floor.",
        ),
        state, cands,
    )
    assert not any("inexact" in f.check for f in report.failures)


# -- consistency ------------------------------------------------------------


def test_a_play_that_was_never_enumerated_is_rejected(playing, candidates):
    trimmed = [c for c in candidates if sorted(c["cards"]) != [0, 1]]
    report = check(advice(), playing, trimmed)
    assert any("not in candidate_plays" in f.message for f in report.failures)


def test_contradicting_a_jokers_reported_contribution_is_rejected(state_factory):
    state = schema.load_state(state_factory(jokers=[{
        "position": 0, "key": "j_stencil", "name": "Joker Stencil",
        "current_contribution": {"chips": 0, "mult": 0, "xmult": 1},
    }]))
    cands = enumerate_module.enumerate_plays(state)
    report = check(
        advice(reasoning="Joker Stencil is giving X2 right now, so keep the slot free."),
        state, cands,
    )
    assert any("current_contribution" in f.message for f in report.failures)


def test_a_shortfall_must_be_acknowledged(state_factory):
    state = schema.load_state(state_factory(
        blind={"type": "small", "key": "bl_small", "requirement": 999999}
    ))
    cands = enumerate_module.enumerate_plays(state)
    report = check(advice(reasoning="This is the best play."), state, cands)
    assert any("does not clear" in f.message for f in report.failures)


def test_an_acknowledged_shortfall_passes(state_factory):
    state = schema.load_state(state_factory(
        blind={"type": "small", "key": "bl_small", "requirement": 999999}
    ))
    cands = enumerate_module.enumerate_plays(state)
    report = check(
        advice(reasoning="This does not clear the blind, but it is the best start."),
        state, cands,
    )
    assert not any("shortfall" in f.check for f in report.failures)


# -- mechanics --------------------------------------------------------------


def test_the_jumbo_mega_error_cannot_recur(playing, candidates):
    report = check(
        advice(reasoning="It is a Jumbo pack, so you get to pick two cards."),
        playing, candidates,
    )
    assert any(f.check == "mechanics.known_error" for f in report.failures)


def test_asserting_an_unresolved_mechanic_is_rejected(playing, candidates):
    report = check(
        advice(reasoning=(
            "Holding the consumable excludes it from the shop pool, so the pool "
            "will not offer it again."
        )),
        playing, candidates,
    )
    assert any(f.check == "mechanics.unresolved_asserted" for f in report.failures)


def test_hedging_an_unresolved_mechanic_is_allowed(playing, candidates):
    report = check(
        advice(reasoning=(
            "Whether holding a consumable excludes it from the shop pool is "
            "unverified, so I will not lean on it either way."
        )),
        playing, candidates,
    )
    assert not any(f.check == "mechanics.unresolved_asserted" for f in report.failures)


def test_an_unbacked_rule_claim_is_flagged_not_blocked(playing, candidates):
    """Spec 5d: flag for review rather than blocking."""
    report = check(
        advice(reasoning="Rerolling always improves the shop on the third try."),
        playing, candidates,
    )
    assert report.ok
    assert any(f.check == "mechanics.unknown_rule_asserted" for f in report.flags)


# -- the documented limit ---------------------------------------------------


def test_legal_but_strategically_terrible_advice_still_passes(playing, candidates):
    """Spec 5d says so outright, and it matters that this is not a bug.

    The single worst legal play validates clean. Judgment quality is measured
    by the decision log, not here.
    """
    worst = min(candidates, key=lambda c: c["score"])
    report = check(
        advice(
            decision=f"Play cards {worst['cards']}.",
            reasoning=(
                f"It scores {worst['score']} and does not clear the blind, but "
                f"I like the shape of it."
            ),
            action={"kind": "play", "cards": worst["cards"]},
        ),
        playing, candidates,
    )
    # Throwing the hand away is a catastrophic recommendation and entirely
    # legal. Nothing here objects to it, and nothing here should.
    assert report.ok
