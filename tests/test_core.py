"""Schema, card parsing, hand classification, scoring and enumeration."""

from __future__ import annotations

import pytest

from balatro_advisor.core import cards, enumerate as enumerate_module, schema, scorer
from balatro_advisor.core.handtype import HandFlags, classify


# --------------------------------------------------------------------------
# Schema: refuse to advise on invalid state rather than guessing
# --------------------------------------------------------------------------


def test_valid_state_loads(state_factory):
    assert schema.load_state(state_factory())["phase"] == "playing"


def test_invalid_rank_is_refused(state_factory, card):
    state = state_factory(current_hand=[card("Z", "hearts")])
    with pytest.raises(schema.StateInvalid):
        schema.load_state(state)


def test_all_errors_are_reported_not_just_the_first(state_factory):
    state = state_factory()
    state["run"]["ante"] = -1
    state["resources"]["hands_remaining"] = -5
    assert len(schema.validate(state)) >= 2


def test_duplicate_joker_positions_rejected(state_factory):
    state = state_factory(jokers=[
        {"position": 0, "key": "j_joker"},
        {"position": 0, "key": "j_sly"},
    ])
    assert any("duplicate position" in e for e in schema.validate(state))


def test_more_jokers_than_slots_rejected(state_factory):
    state = state_factory(
        resources={"hands_remaining": 1, "discards_remaining": 0, "joker_slots_total": 1},
        jokers=[{"position": 0, "key": "j_joker"}, {"position": 1, "key": "j_sly"}],
    )
    assert any("joker_slots_total" in e for e in schema.validate(state))


def test_stone_card_must_have_no_rank(state_factory, card):
    state = state_factory(current_hand=[card("K", "hearts", enhancement="stone")])
    assert any("stone" in e for e in schema.validate(state))


def test_wild_cards_do_not_trip_validation(state_factory, card):
    """Spec section 2: suit totals may exceed the ranked card count. Legal."""
    state = state_factory(current_hand=[
        card("K", "hearts", enhancement="wild"),
        card("K", "diamonds", enhancement="wild"),
        card("4", "clubs"),
    ])
    assert schema.validate(state) == []


def test_shop_phase_requires_a_shop(state_factory):
    state = state_factory(phase="shop", blind=None, current_hand=[])
    assert any("shop" in e for e in schema.validate(state))


def test_normalize_is_idempotent(state_factory):
    once = schema.normalize(state_factory())
    assert schema.normalize(once) == once


# --------------------------------------------------------------------------
# Card shorthand
# --------------------------------------------------------------------------


def test_shorthand_hand():
    parsed = cards.parse_hand("KH QD 10S 4D")
    assert [(c["rank"], c["suit"]) for c in parsed] == [
        ("K", "hearts"), ("Q", "diamonds"), ("10", "spades"), ("4", "diamonds"),
    ]


def test_shorthand_modifiers():
    card = cards.parse_card("KH:gold:polychrome:redseal")
    assert card["enhancement"] == "gold"
    assert card["edition"] == "polychrome"
    assert card["seal"] == "red"


def test_t_is_accepted_for_ten():
    assert cards.parse_card("TS")["rank"] == "10"


def test_stone_shorthand_has_no_rank_or_suit():
    card = cards.parse_card("stone")
    assert card["enhancement"] == "stone"
    assert card["rank"] is None and card["suit"] is None


def test_bad_shorthand_names_the_token():
    with pytest.raises(cards.CardSyntaxError) as exc:
        cards.parse_card("XZ")
    assert "XZ" in str(exc.value)


def test_unknown_modifier_lists_the_valid_ones():
    with pytest.raises(cards.CardSyntaxError) as exc:
        cards.parse_card("KH:sparkly")
    assert "gold" in str(exc.value)


def test_shorthand_round_trips():
    for token in ("KH", "10S", "stone", "AH:steel:foil"):
        assert cards.format_card(cards.parse_card(token), verbose=True) == token


# --------------------------------------------------------------------------
# Hand classification
# --------------------------------------------------------------------------


def _cards(*specs):
    return [cards.parse_card(s) for s in specs]


@pytest.mark.parametrize("hand,expected", [
    (("KH", "KD"), "pair"),
    (("KH", "KD", "JS", "JH"), "two_pair"),
    (("KH", "KD", "KS"), "three_of_a_kind"),
    (("KH", "KD", "KS", "KC"), "four_of_a_kind"),
    (("KH", "KD", "KS", "JH", "JD"), "full_house"),
    (("2H", "3H", "4H", "5H", "6H"), "straight_flush"),
    (("2H", "3D", "4H", "5H", "6H"), "straight"),
    (("2H", "9H", "4H", "5H", "KH"), "flush"),
    (("AH", "2D", "3H", "4H", "5H"), "straight"),
    (("10H", "JD", "QH", "KH", "AH"), "straight"),
    (("KH",), "high_card"),
])
def test_classification(hand, expected):
    assert classify(_cards(*hand)).hand_type == expected


def test_four_fingers_allows_a_four_card_flush():
    hand = _cards("2H", "9H", "4H", "5H", "KD")
    assert classify(hand).hand_type == "high_card"
    assert classify(hand, HandFlags(four_fingers=True)).hand_type == "flush"


def test_shortcut_allows_gapped_straights():
    hand = _cards("10H", "8D", "6H", "5S", "3C")
    assert classify(hand).hand_type == "high_card"
    assert classify(hand, HandFlags(shortcut=True)).hand_type == "straight"


def test_smeared_makes_red_suits_one_suit():
    hand = _cards("2H", "9D", "4H", "5D", "KH")
    assert classify(hand).hand_type == "high_card"
    assert classify(hand, HandFlags(smeared=True)).hand_type == "flush"


def test_wild_card_completes_a_flush():
    hand = _cards("2H", "9H", "4H", "5H", "KS:wild")
    assert classify(hand).hand_type == "flush"


def test_stone_card_always_scores_but_makes_no_hand():
    result = classify(_cards("KH", "KD", "stone"))
    assert result.hand_type == "pair"
    assert result.scoring == [0, 1, 2]


def test_splash_scores_every_played_card():
    result = classify(_cards("KH", "KD", "4C"), HandFlags(splash=True))
    assert result.scoring == [0, 1, 2]


def test_contains_reports_every_hand_present():
    """Jolly Joker asks 'contains a pair', which a full house does."""
    contains = classify(_cards("KH", "KD", "KS", "JH", "JD")).contains
    assert {"pair", "three_of_a_kind", "full_house", "two_pair"} <= contains


# --------------------------------------------------------------------------
# Scorer honesty
# --------------------------------------------------------------------------


def test_unknown_joker_is_not_scored_as_zero(state_factory):
    state = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": "j_definitely_not_real"}]
    ))
    result = scorer.score_play(state, [0, 1])
    assert result.exact is False
    assert "j_definitely_not_real" in result.unmodelled


def test_lucky_card_is_not_given_an_expected_value(state_factory, card):
    state = schema.load_state(state_factory(current_hand=[
        card("K", "hearts", enhancement="lucky"),
        card("K", "diamonds"),
        card("4", "clubs"),
    ]))
    result = scorer.score_play(state, [0, 1])
    assert result.exact is False
    assert "lucky_card" in result.unmodelled


def test_joker_order_changes_the_result(state_factory):
    """Left-to-right evaluation: x2 before +4 differs from +4 before x2."""
    def score(keys):
        state = schema.load_state(state_factory(jokers=[
            {"position": i, "key": k} for i, k in enumerate(keys)
        ]))
        return scorer.score_play(state, [0, 1]).mult

    # The Duo is x2 (the hand contains a pair); Joker is +4.
    assert score(["j_duo", "j_joker"]) != score(["j_joker", "j_duo"])


def test_polychrome_joker_edition_applies(state_factory):
    plain = schema.load_state(state_factory(jokers=[{"position": 0, "key": "j_joker"}]))
    shiny = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": "j_joker", "edition": "polychrome"}]
    ))
    assert scorer.score_play(shiny, [0, 1]).mult == pytest.approx(
        scorer.score_play(plain, [0, 1]).mult * 1.5
    )


def test_red_seal_retriggers_a_card(state_factory, card):
    plain = schema.load_state(state_factory())
    sealed = schema.load_state(state_factory(current_hand=[
        card("K", "hearts", seal="red"),
        card("K", "diamonds"),
        card("4", "clubs"),
    ]))
    # The King scores twice: +10 chips more than the unsealed board.
    assert scorer.score_play(sealed, [0, 1]).chips == (
        scorer.score_play(plain, [0, 1]).chips + 10
    )


def test_final_score_is_floored(state_factory):
    state = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": "j_obelisk", "internal_state": {"counter": 1.33}}]
    ))
    result = scorer.score_play(state, [0, 1])
    assert result.score == int(result.chips * result.mult // 1)


# --------------------------------------------------------------------------
# Enumeration
# --------------------------------------------------------------------------


def test_eight_cards_give_218_subsets(state_factory, card):
    """Spec section 4 states this count explicitly."""
    hand = [card(r, "hearts") for r in ("2", "3", "4", "5", "6", "7", "8", "9")]
    state = schema.load_state(state_factory(current_hand=hand))
    assert len(enumerate_module.enumerate_plays(state)) == 218


def test_clearing_the_blind_dominates_score(state_factory, card):
    state = schema.load_state(state_factory(
        current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
        blind={"type": "small", "key": "bl_small", "requirement": 100},
    ))
    plays = enumerate_module.enumerate_plays(state)
    clearing = [p for p in plays if p["clears_blind"]]
    if clearing:
        assert plays[0]["clears_blind"] is True


def test_steel_forfeited_breaks_ties(state_factory, card):
    """Spec section 4: 'Gold/Steel value forfeited, ascending (tiebreak)'."""
    state = schema.load_state(state_factory(current_hand=[
        card("K", "hearts"), card("K", "diamonds"),
        card("4", "clubs", enhancement="steel"),
    ]))
    plays = enumerate_module.enumerate_plays(state)
    burns_steel = next(p for p in plays if 2 in p["cards"])
    assert burns_steel["steel_forfeited"] == 1
    # The play that keeps the Steel card in hand outranks the one that burns it.
    assert plays[0]["steel_forfeited"] == 0


def test_psychic_only_permits_five_card_plays(state_factory, card):
    hand = [card(r, "hearts") for r in ("2", "3", "4", "5", "6", "7")]
    state = schema.load_state(state_factory(
        current_hand=hand,
        blind={"type": "boss", "key": "bl_psychic", "requirement": 1000},
    ))
    plays = enumerate_module.enumerate_plays(state)
    assert plays and all(len(p["cards"]) == 5 for p in plays)


def test_discards_report_a_floor_but_no_expected_score(state_factory):
    """Spec section 4: draw evaluation is deferred, not guessed at."""
    state = schema.load_state(state_factory())
    discards = enumerate_module.enumerate_discards(state)
    assert discards
    for candidate in discards:
        assert candidate["expected_score"] is None
        assert candidate["p_clears_blind"] is None
        assert candidate["floor_score"] >= 0


def test_no_discards_remaining_means_no_discard_candidates(state_factory):
    state = schema.load_state(state_factory(
        resources={"hands_remaining": 1, "discards_remaining": 0}
    ))
    assert enumerate_module.enumerate_discards(state) == []


def test_inexact_candidate_never_claims_it_misses_the_blind(state_factory):
    """A floor below the requirement proves nothing; it must report None."""
    state = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": "j_blueprint"}],
        blind={"type": "small", "key": "bl_small", "requirement": 999999},
    ))
    for play in enumerate_module.enumerate_plays(state):
        assert play["clears_blind"] is None


# --------------------------------------------------------------------------
# Effects sourced from game.lua: random expectation, compounding, new sources
# --------------------------------------------------------------------------


def test_random_effects_report_an_expected_value(state_factory):
    """The user's call: approximation is fine for randomness, so report the mean.

    It is still an exact computation - of the expectation, from the game's own
    range - and it must be flagged so nothing presents it as a certainty.
    """
    state = schema.load_state(state_factory(jokers=[{"position": 0, "key": "j_misprint"}]))
    result = scorer.score_play(state, [0, 1])
    assert result.stochastic is True
    assert result.exact is True, "an expectation is computed, not unknown"
    assert result.mult == pytest.approx(2 + 11.5), "uniform 0..23 has mean 11.5"


def test_a_deterministic_hand_is_not_marked_stochastic(state_factory):
    state = schema.load_state(state_factory(jokers=[{"position": 0, "key": "j_joker"}]))
    assert scorer.score_play(state, [0, 1]).stochastic is False


def test_odds_based_randomness_weights_the_multiplier(state_factory, card):
    """Bloodstone is 1 in 2 for X1.5, so the expectation is X1.25, not X1.5."""
    state = schema.load_state(state_factory(
        current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
        jokers=[{"position": 0, "key": "j_bloodstone"}],
    ))
    result = scorer.score_play(state, [0, 1])
    assert result.mult == pytest.approx(2 * 1.25)


def test_baseball_card_compounds_rather_than_adds(state_factory):
    """X1.5 per Uncommon is 1.5**n. Two Uncommons is X2.25, not X2."""
    state = schema.load_state(state_factory(jokers=[
        {"position": 0, "key": "j_baseball"},
        {"position": 1, "key": "j_burglar"},
        {"position": 2, "key": "j_turtle_bean"},
    ]))
    assert scorer.score_play(state, [0, 1]).mult == pytest.approx(2 * 2.25)


def test_economy_jokers_score_exactly_and_contribute_nothing(state_factory):
    """A board of economy jokers is fully computable, not a floor."""
    bare = schema.load_state(state_factory())
    loaded = schema.load_state(state_factory(jokers=[
        {"position": 0, "key": "j_golden"},
        {"position": 1, "key": "j_rocket"},
        {"position": 2, "key": "j_cloud_9"},
    ]))
    plain, with_economy = scorer.score_play(bare, [0, 1]), scorer.score_play(loaded, [0, 1])
    assert with_economy.exact is True
    assert with_economy.score == plain.score


def test_chicot_disables_a_scoring_boss_blind(state_factory, card):
    hand = [card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")]
    flint = {"type": "boss", "key": "bl_flint", "requirement": 9999}
    without = schema.load_state(state_factory(current_hand=hand, blind=flint))
    with_chicot = schema.load_state(state_factory(
        current_hand=hand, blind=flint, jokers=[{"position": 0, "key": "j_chicot"}]
    ))
    assert scorer.score_play(with_chicot, [0, 1]).score > scorer.score_play(without, [0, 1]).score


def test_bootstraps_floors_the_division(state_factory):
    """+2 Mult per $5: $9 must give the same as $5, not 1.8x as much."""
    def mult(money):
        state = schema.load_state(state_factory(
            run={"ante": 1, "money": money},
            jokers=[{"position": 0, "key": "j_bootstraps"}],
        ))
        return scorer.score_play(state, [0, 1]).mult

    assert mult(5) == mult(9)
    assert mult(10) > mult(9)


def test_swashbuckler_excludes_its_own_sell_value(state_factory):
    state = schema.load_state(state_factory(jokers=[
        {"position": 0, "key": "j_swashbuckler", "sell_value": 2},
        {"position": 1, "key": "j_juggler", "sell_value": 3},
        {"position": 2, "key": "j_drunkard", "sell_value": 4},
    ]))
    assert scorer.score_play(state, [0, 1]).mult == pytest.approx(2 + 7)


def test_a_missing_sell_value_makes_swashbuckler_unknown(state_factory):
    """One unknown sell value makes the total unknowable, not merely smaller."""
    state = schema.load_state(state_factory(jokers=[
        {"position": 0, "key": "j_swashbuckler", "sell_value": 2},
        {"position": 1, "key": "j_juggler"},
    ]))
    assert scorer.score_play(state, [0, 1]).exact is False


def test_erosion_needs_the_decks_starting_size(state_factory, card):
    """It cannot be assumed to be 52 - some decks start smaller."""
    deck = {"total": 48, "cards": [card("2", "hearts")]}
    without = schema.load_state(state_factory(
        deck=deck, jokers=[{"position": 0, "key": "j_erosion"}]
    ))
    assert scorer.score_play(without, [0, 1]).exact is False

    with_size = schema.load_state(state_factory(
        deck={**deck, "starting_total": 52},
        jokers=[{"position": 0, "key": "j_erosion"}],
    ))
    result = scorer.score_play(with_size, [0, 1])
    assert result.exact is True
    assert result.mult == pytest.approx(2 + 4 * 4), "4 cards removed at +4 Mult each"
