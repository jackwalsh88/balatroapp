"""Write the hand-computed fixture set.

Each fixture's `arithmetic` field is the derivation, written out by hand. The
test suite then asserts the scorer reproduces it. If the two disagree, exactly
one of them is wrong and both are worth looking at - which is the entire point
of keeping the derivation in the file rather than only in the code.

Run: python tools/build_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def card(rank, suit, **kw):
    return {"rank": rank, "suit": suit, **kw}


def stone():
    return {"rank": None, "suit": None, "enhancement": "stone"}


def state(**kw):
    base = {
        "schema_version": 1,
        "seq": 1,
        "source": "manual",
        "captured_at": None,
        "phase": "playing",
        "run": {"ante": 5, "ante_max": 8, "round": 13, "money": 45,
                "deck_name": "Blue Deck", "stake": "white", "vouchers_redeemed": []},
        "resources": {"hands_remaining": 3, "discards_remaining": 2, "hand_size": 8,
                      "joker_slots_total": 5, "consumable_slots_total": 2},
        "jokers": [],
        "consumables": [],
        "hand_levels": {},
        "current_hand": [],
        "blind": {"type": "small", "key": "bl_small", "name": "Small Blind",
                  "requirement": 3000, "current_score": 0},
    }
    for key, value in kw.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def joker(pos, key, **kw):
    return {"position": pos, "key": key, "sell_value": 3, **kw}


KINGS_AND_JACKS = [
    card("K", "hearts"), card("K", "diamonds"), card("J", "spades"),
    card("J", "hearts"), card("3", "clubs"), card("7", "spades"),
    card("9", "diamonds"), card("2", "hearts"),
]

L6_TWO_PAIR = {"two_pair": {"level": 6, "chips": 120, "mult": 7, "played": 9}}


fixtures = [
    {
        "name": "two_pair_level6_no_jokers",
        "provenance": "hand_computed",
        "arithmetic": (
            "Two Pair at level 6 is 120 chips x 7 mult (data/hand_levels.json: "
            "20 + 5*20 = 120, 2 + 5*1 = 7; this reproduces the spec's own section 2 "
            "example exactly). Four scoring cards K,K,J,J at 10 chips each add 40. "
            "160 x 7 = 1120."
        ),
        "state_before": state(hand_levels=L6_TWO_PAIR, current_hand=KINGS_AND_JACKS),
        "cards_played": [0, 1, 2, 3],
        "expected_chips": 160, "expected_mult": 7, "expected_score": 1120,
    },
    {
        "name": "two_pair_level1_fallback",
        "provenance": "hand_computed",
        "arithmetic": (
            "hand_levels is empty, so the scorer falls back to the static table at "
            "level 1: 20 chips x 2 mult. Plus 40 from K,K,J,J. 60 x 2 = 120."
        ),
        "state_before": state(current_hand=KINGS_AND_JACKS),
        "cards_played": [0, 1, 2, 3],
        "expected_chips": 60, "expected_mult": 2, "expected_score": 120,
    },
    {
        "name": "blue_joker_and_photograph",
        "provenance": "hand_computed",
        "arithmetic": (
            "Base Two Pair L6 = 120 x 7. Cards: K(10) triggers Photograph (first face "
            "card, x2 mult) -> 130 x 14; K(10), J(10), J(10) -> 160 x 14. Blue Joker "
            "gives +2 chips per card remaining in deck, 35 remaining -> +70 chips -> "
            "230 x 14. 230 * 14 = 3220."
        ),
        "state_before": state(
            hand_levels=L6_TWO_PAIR,
            current_hand=KINGS_AND_JACKS,
            deck={"total": 52, "remaining": 35, "cards": []},
            jokers=[joker(0, "j_blue_joker"), joker(1, "j_photograph")],
        ),
        "cards_played": [0, 1, 2, 3],
        "expected_chips": 230, "expected_mult": 14, "expected_score": 3220,
    },
    {
        "name": "lower_hand_outscores_higher",
        "provenance": "hand_computed",
        "counterintuitive": (
            "The Flush is the better poker hand and the worse play. A level 8 Pair "
            "(115 x 9) beats a level 1 Flush (35 x 4) by nearly 5x, because hand "
            "LEVEL dominates hand RANK once one hand has been levelled. An "
            "implementation that ranks by poker-hand strength instead of by computed "
            "score passes every other fixture and fails this one."
        ),
        "arithmetic": (
            "Pair at level 8 = 10 + 7*15 = 115 chips, 2 + 7*1 = 9 mult. Scoring cards "
            "AH(11) + AS(11) = 22. 137 x 9 = 1233. "
            "Flush at level 1 = 35 chips, 4 mult. Scoring cards AH(11) 5H(5) 8H(8) "
            "9H(9) 2H(2) = 35. 70 x 4 = 280. The Pair wins 1233 to 280."
        ),
        "state_before": state(
            hand_levels={"pair": {"level": 8, "chips": 115, "mult": 9, "played": 22}},
            current_hand=[
                card("A", "hearts"), card("A", "spades"), card("5", "hearts"),
                card("8", "hearts"), card("9", "hearts"), card("2", "hearts"),
            ],
        ),
        "cards_played": [0, 1],
        "expected_chips": 137, "expected_mult": 9, "expected_score": 1233,
        "also_assert_top_ranked_play": [0, 1],
    },
    {
        "name": "joker_stencil_slot_open",
        "provenance": "hand_computed",
        "arithmetic": (
            "Pair L1 = 10 x 2. Scoring cards KH(10) + KD(10) -> 30 chips. Smiley Face "
            "gives +5 mult per scored face card: 2 + 5 + 5 = 12. Then jokers left to "
            "right: Joker Stencil is X1 per empty slot with its own slot counted, "
            "4 jokers in 5 slots -> 1 empty + itself = X2 -> 24; Joker +4 -> 28; "
            "Gros Michel +15 -> 43. 30 x 43 = 1290."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[
                joker(0, "j_stencil"), joker(1, "j_joker"),
                joker(2, "j_gros_michel"), joker(3, "j_smiley"),
            ],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 43, "expected_score": 1290,
    },
    {
        "name": "joker_stencil_slot_filled",
        "provenance": "hand_computed",
        "counterintuitive": (
            "The same board after buying one more +4 Mult Joker. Filling the last "
            "slot converts Stencil from X2 to X1 and the score DROPS from 1290 to "
            "1050 - a purchase that is strictly negative. This is the spec's section 5 "
            "'opportunity cost of joker slots' case; pair it with "
            "joker_stencil_slot_open."
        ),
        "arithmetic": (
            "Identical to joker_stencil_slot_open except a fifth joker fills the last "
            "slot. Stencil now sees 0 empty slots + itself = X1. "
            "2 + 5 + 5 = 12 mult; X1 -> 12; Joker +4 -> 16; Gros Michel +15 -> 31; "
            "the new Joker +4 -> 35. 30 x 35 = 1050, which is 240 LESS than before "
            "the purchase."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[
                joker(0, "j_stencil"), joker(1, "j_joker"),
                joker(2, "j_gros_michel"), joker(3, "j_smiley"),
                joker(4, "j_joker"),
            ],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 35, "expected_score": 1050,
    },
    {
        "name": "steel_card_held_in_hand",
        "provenance": "hand_computed",
        "arithmetic": (
            "Pair L1 = 10 x 2. Scoring KH(10) + KD(10) -> 30 chips. The Steel 5S is "
            "NOT played, so it triggers its held-in-hand X1.5: 2 * 1.5 = 3. "
            "30 x 3 = 90. Playing the Steel card instead would forfeit that entirely."
        ),
        "state_before": state(
            current_hand=[
                card("K", "hearts"), card("K", "diamonds"),
                card("5", "spades", enhancement="steel"),
            ],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 3, "expected_score": 90,
    },
    {
        "name": "stone_card_always_scores",
        "provenance": "hand_computed",
        "arithmetic": (
            "The played cards are KH, KD and a Stone card. Stone has no rank so it "
            "cannot be part of the Pair, but it always scores. Pair L1 = 10 x 2. "
            "KH(10) + KD(10) + Stone(50) = 70 chips added. 80 x 2 = 160."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), stone()],
        ),
        "cards_played": [0, 1, 2],
        "expected_chips": 80, "expected_mult": 2, "expected_score": 160,
    },
    {
        "name": "gold_card_played_forfeits_payout",
        "provenance": "hand_computed",
        "arithmetic": (
            "Pair L1 = 10 x 2. Gold KH(10) + KD(10) -> 30 chips, mult 2. 30 x 2 = 60. "
            "Gold adds no chips or mult; its value is $3 paid only if the card is "
            "still held at the end of the round, so playing it forfeits $3. That is "
            "reported as gold_forfeited, not folded into the score."
        ),
        "state_before": state(
            current_hand=[
                card("K", "hearts", enhancement="gold"), card("K", "diamonds"),
                card("4", "clubs"),
            ],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 2, "expected_score": 60,
        "expected_gold_forfeited": 3,
    },
    {
        "name": "boss_flint_halves_base",
        "provenance": "hand_computed",
        "arithmetic": (
            "The Flint halves the hand's BASE chips and mult before anything else. "
            "Pair L1 = 10 x 2 becomes 5 x 1. Cards KH(10) + KD(10) add 20 chips, "
            "which are not halved. 25 x 1 = 25."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            blind={"type": "boss", "key": "bl_flint", "name": "The Flint",
                   "requirement": 22000, "current_score": 0,
                   "effect_description": "Base Chips and Mult are halved"},
        ),
        "cards_played": [0, 1],
        "expected_chips": 25, "expected_mult": 1, "expected_score": 25,
    },
    {
        "name": "boss_goad_debuffs_spades",
        "provenance": "hand_computed",
        "arithmetic": (
            "The Goad debuffs all Spades: they score nothing at all. The played hand "
            "is KS KH, still a Pair (debuff removes scoring, not the hand type). "
            "Pair L1 = 10 x 2. KS contributes 0, KH contributes 10. 20 x 2 = 40."
        ),
        "state_before": state(
            current_hand=[card("K", "spades"), card("K", "hearts"), card("4", "clubs")],
            blind={"type": "boss", "key": "bl_goad", "name": "The Goad",
                   "requirement": 22000, "current_score": 0,
                   "effect_description": "All Spade cards are debuffed"},
        ),
        "cards_played": [0, 1],
        "expected_chips": 20, "expected_mult": 2, "expected_score": 40,
    },
    {
        "name": "unmodellable_joker_is_not_scored_as_zero",
        "provenance": "hand_computed",
        "counterintuitive": (
            "The assertion here is not a number, it is the honesty flag. Blueprint's "
            "copy semantics are listed as unverified in spec section 9, so the scorer "
            "must mark the whole candidate non-exact rather than quietly treating "
            "Blueprint as contributing nothing. A scorer that returns a confident "
            "1760 here is wrong in exactly the way section 1 describes."
        ),
        "arithmetic": (
            "The modelled part is Two Pair L6 = 120 x 7, plus 40 chips from K,K,J,J, "
            "plus the +4 Mult from the plain Joker sitting beside Blueprint: "
            "160 x 11 = 1760. Blueprint itself is unmodelled, so exact is false, "
            "unmodelled lists j_blueprint, and 1760 is a FLOOR rather than a score - "
            "Blueprint is copying the Joker to its right and is certainly adding "
            "something, but this project does not claim to know how much."
        ),
        "state_before": state(
            hand_levels=L6_TWO_PAIR,
            current_hand=KINGS_AND_JACKS,
            jokers=[joker(0, "j_blueprint"), joker(1, "j_joker")],
        ),
        "cards_played": [0, 1, 2, 3],
        "expected_chips": 160, "expected_mult": 11, "expected_score": 1760,
        "expected_exact": False,
        "expected_unmodelled": ["j_blueprint"],
    },
    {
        "name": "scaling_joker_without_counter_is_unmodelled",
        "provenance": "hand_computed",
        "counterintuitive": (
            "Obelisk is a modelled joker, but its value lives in a counter the "
            "adapter did not read. Missing input must degrade to 'unknown', never to "
            "a default of X1. This is the manual-entry failure mode: a player who "
            "does not type in the counter should get a refusal, not a wrong number."
        ),
        "arithmetic": (
            "Two Pair L6 = 120 x 7 plus 40 from the four scoring cards = 160 x 7 = "
            "1120 for everything except Obelisk. Obelisk's internal_state.counter is "
            "null, so its Xmult cannot be resolved and the candidate is non-exact."
        ),
        "state_before": state(
            hand_levels=L6_TWO_PAIR,
            current_hand=KINGS_AND_JACKS,
            jokers=[joker(0, "j_obelisk", internal_state={"counter": None})],
        ),
        "cards_played": [0, 1, 2, 3],
        "expected_chips": 160, "expected_mult": 7, "expected_score": 1120,
        "expected_exact": False,
        "expected_unmodelled": ["j_obelisk"],
    },
    {
        "name": "scaling_joker_with_counter_is_exact",
        "provenance": "hand_computed",
        "arithmetic": (
            "The same board with the counter actually read: Obelisk currently shows "
            "X2.4. Two Pair L6 = 120 x 7, +40 chips from K,K,J,J -> 160 x 7, then "
            "Obelisk X2.4 -> 16.8 mult. 160 * 16.8 = 2688."
        ),
        "state_before": state(
            hand_levels=L6_TWO_PAIR,
            current_hand=KINGS_AND_JACKS,
            jokers=[joker(0, "j_obelisk", internal_state={"counter": 2.4})],
        ),
        "cards_played": [0, 1, 2, 3],
        "expected_chips": 160, "expected_mult": 16.8, "expected_score": 2688,
    },
    {
        "name": "shop_with_eternal_and_perishable",
        "provenance": "hand_computed",
        "arithmetic": None,
        "note": (
            "Shop phase. Exercises ingest, validation and the advisor rather than the "
            "scorer: an Eternal joker cannot be sold, a Perishable one expires, and "
            "the $9 Cavendish is unaffordable at $6. There is no hand to score."
        ),
        "state_before": state(
            phase="shop",
            run={"money": 6},
            resources={"hands_remaining": 0, "discards_remaining": 0},
            current_hand=[],
            blind=None,
            jokers=[
                joker(0, "j_gros_michel", stickers=["eternal"]),
                joker(1, "j_joker"),
            ],
            shop={
                "reroll_cost": 5,
                "items": [
                    {"slot": 0, "kind": "joker", "key": "j_cavendish",
                     "name": "Cavendish", "price": 9, "edition": "base", "stickers": []},
                    {"slot": 1, "kind": "joker", "key": "j_blue_joker",
                     "name": "Blue Joker", "price": 5, "edition": "base",
                     "stickers": ["perishable"]},
                    {"slot": 2, "kind": "consumable", "key": "c_sun",
                     "name": "The Sun", "price": 3},
                ],
                "voucher": {"key": "v_blank", "name": "Blank", "price": 10},
                "packs": [{"key": "p_celestial_jumbo", "name": "Jumbo Celestial Pack",
                           "price": 6}],
            },
        ),
        "cards_played": None,
        "expected_chips": None, "expected_mult": None, "expected_score": None,
    },
    {
        "name": "pack_open_jumbo_allows_one_pick",
        "provenance": "hand_computed",
        "arithmetic": None,
        "note": (
            "Pack-open phase. The point of this fixture is choices_allowed = 1 on a "
            "Jumbo pack. Spec sections 2 and 5d record that Jumbo/Mega pick counts "
            "were stated backwards during manual testing; advice recommending two "
            "picks here must fail validation."
        ),
        "state_before": state(
            phase="pack_open",
            current_hand=[],
            blind=None,
            jokers=[joker(0, "j_blue_joker")],
            pack_open={
                "key": "p_celestial_jumbo",
                "name": "Jumbo Celestial Pack",
                "choices_allowed": 1,
                "cards": [
                    {"key": "c_uranus", "name": "Uranus"},
                    {"key": "c_mars", "name": "Mars"},
                    {"key": "c_jupiter", "name": "Jupiter"},
                    {"key": "c_saturn", "name": "Saturn"},
                ],
            },
        ),
        "cards_played": None,
        "expected_chips": None, "expected_mult": None, "expected_score": None,
    },
    {
        "name": "misprint_expected_value",
        "provenance": "hand_computed",
        "arithmetic": (
            "Misprint gives a uniformly random +0 to +23 Mult (game.lua config "
            "extra = {min = 0, max = 23}), so its expected contribution is "
            "(0+23)/2 = 11.5. Pair L1 = 10 x 2; KH(10) + KD(10) -> 30 chips; "
            "mult 2 + 11.5 = 13.5. floor(30 * 13.5) = 405. The score is an "
            "EXPECTATION, exactly computed from the game's own range, and the "
            "candidate is flagged stochastic."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[joker(0, "j_misprint")],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 13.5, "expected_score": 405,
        "expected_stochastic": True,
    },
    {
        "name": "bloodstone_expected_value",
        "provenance": "hand_computed",
        "arithmetic": (
            "Bloodstone is a 1 in 2 chance of X1.5 Mult per scored Heart "
            "(config extra = {odds = 2, Xmult = 1.5}). Expected multiplier per "
            "Heart = 0.5*1.5 + 0.5*1 = 1.25. Only KH is a Heart. "
            "Pair L1 = 10 x 2; +20 chips -> 30 chips; mult 2 * 1.25 = 2.5. "
            "floor(30 * 2.5) = 75."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[joker(0, "j_bloodstone")],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 2.5, "expected_score": 75,
        "expected_stochastic": True,
    },
    {
        "name": "baseball_card_multiplies_per_uncommon",
        "provenance": "hand_computed",
        "counterintuitive": (
            "X1.5 for EACH Uncommon joker compounds - it is 1.5^n, not "
            "1 + 0.5n. With two Uncommons that is X2.25, not X2. An "
            "implementation that adds rather than compounds passes with one "
            "Uncommon on the board and fails here."
        ),
        "arithmetic": (
            "Baseball Card is Rare so it does not count itself. Burglar and "
            "Turtle Bean are both Uncommon and neither has a scoring effect, "
            "so they contribute only to the count: 1.5^2 = 2.25. "
            "Pair L1 = 10 x 2; +20 chips -> 30 chips; mult 2 * 2.25 = 4.5. "
            "floor(30 * 4.5) = 135."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[joker(0, "j_baseball"), joker(1, "j_burglar"), joker(2, "j_turtle_bean")],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 4.5, "expected_score": 135,
    },
    {
        "name": "flower_pot_needs_all_four_suits",
        "provenance": "hand_computed",
        "arithmetic": (
            "Flower Pot is X3 if the SCORING cards include a Diamond, Club, "
            "Heart and Spade. A Straight scores all five cards, and "
            "2H 3D 4C 5S 6H covers all four suits. Straight L1 = 30 x 4; card "
            "chips 2+3+4+5+6 = 20 -> 50 chips; mult 4 * 3 = 12. "
            "floor(50 * 12) = 600."
        ),
        "state_before": state(
            current_hand=[
                card("2", "hearts"), card("3", "diamonds"), card("4", "clubs"),
                card("5", "spades"), card("6", "hearts"),
            ],
            jokers=[joker(0, "j_flower_pot")],
        ),
        "cards_played": [0, 1, 2, 3, 4],
        "expected_chips": 50, "expected_mult": 12, "expected_score": 600,
    },
    {
        "name": "seeing_double_club_plus_other",
        "provenance": "hand_computed",
        "arithmetic": (
            "Seeing Double is X2 if the scoring cards include a Club AND a card "
            "of any other suit. KC and KH satisfy both halves. "
            "Pair L1 = 10 x 2; +20 chips -> 30 chips; mult 2 * 2 = 4. "
            "floor(30 * 4) = 120."
        ),
        "state_before": state(
            current_hand=[card("K", "clubs"), card("K", "hearts"), card("4", "diamonds")],
            jokers=[joker(0, "j_seeing_double")],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 4, "expected_score": 120,
    },
    {
        "name": "chicot_disables_the_boss_effect",
        "provenance": "hand_computed",
        "counterintuitive": (
            "Chicot adds no chips and no mult of its own, yet more than doubles "
            "the score - by switching off The Flint's halving of the base. Pair "
            "it with boss_flint_halves_base, which is the same board without "
            "Chicot and scores 25."
        ),
        "arithmetic": (
            "The Flint would halve Pair L1 from 10 x 2 to 5 x 1. Chicot "
            "disables the Boss Blind effect entirely, so the base stays 10 x 2. "
            "KH(10) + KD(10) -> 30 chips. floor(30 * 2) = 60, against 25 for "
            "the same hand without Chicot."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[joker(0, "j_chicot")],
            blind={"type": "boss", "key": "bl_flint", "name": "The Flint",
                   "requirement": 22000, "current_score": 0,
                   "effect_description": "Base Chips and Mult are halved"},
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 2, "expected_score": 60,
    },
    {
        "name": "shoot_the_moon_counts_held_queens",
        "provenance": "hand_computed",
        "arithmetic": (
            "Shoot the Moon gives +13 Mult per Queen HELD IN HAND (config "
            "extra = 13). Two Queens are held, not played: 2 * 13 = +26. "
            "Pair L1 = 10 x 2; KH(10) + KD(10) -> 30 chips; mult 2 + 26 = 28. "
            "floor(30 * 28) = 840."
        ),
        "state_before": state(
            current_hand=[
                card("K", "hearts"), card("K", "diamonds"),
                card("Q", "clubs"), card("Q", "spades"),
            ],
            jokers=[joker(0, "j_shoot_the_moon")],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 28, "expected_score": 840,
    },
    {
        "name": "swashbuckler_excludes_its_own_sell_value",
        "provenance": "hand_computed",
        "counterintuitive": (
            "Swashbuckler adds the sell value of all OTHER jokers. Including "
            "its own is an easy off-by-one that inflates every hand by its own "
            "price, and no other fixture would catch it."
        ),
        "arithmetic": (
            "Juggler ($3) and Drunkard ($4) are worth $7 between them; "
            "Swashbuckler's own $2 is excluded. Neither of the others has a "
            "scoring effect. Pair L1 = 10 x 2; +20 chips -> 30 chips; "
            "mult 2 + 7 = 9. floor(30 * 9) = 270."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[
                {"position": 0, "key": "j_swashbuckler", "sell_value": 2},
                {"position": 1, "key": "j_juggler", "sell_value": 3},
                {"position": 2, "key": "j_drunkard", "sell_value": 4},
            ],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 9, "expected_score": 270,
    },
    {
        "name": "bootstraps_floors_the_money_division",
        "provenance": "hand_computed",
        "arithmetic": (
            "Bootstraps is +2 Mult for every $5 (config extra = {mult = 2, "
            "dollars = 5}). $49 gives floor(49/5) = 9 lots, not 9.8: "
            "9 * 2 = +18 Mult. Pair L1 = 10 x 2; +20 chips -> 30 chips; "
            "mult 2 + 18 = 20. floor(30 * 20) = 600."
        ),
        "state_before": state(
            run={"money": 49},
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[joker(0, "j_bootstraps")],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 20, "expected_score": 600,
    },
    {
        "name": "economy_jokers_contribute_nothing",
        "provenance": "hand_computed",
        "arithmetic": (
            "Golden Joker, Rocket and Cloud 9 are all economy jokers: they earn "
            "money at end of round and add no chips or mult. The hand scores "
            "exactly as it would with no jokers at all. Pair L1 = 10 x 2; "
            "+20 chips -> 30 chips. floor(30 * 2) = 60. "
            "Crucially this is EXACT, not a floor - a board full of economy "
            "jokers is fully computable."
        ),
        "state_before": state(
            current_hand=[card("K", "hearts"), card("K", "diamonds"), card("4", "clubs")],
            jokers=[joker(0, "j_golden"), joker(1, "j_rocket"), joker(2, "j_cloud_9")],
        ),
        "cards_played": [0, 1],
        "expected_chips": 30, "expected_mult": 2, "expected_score": 60,
        "expected_exact": True,
    },
]


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    for fixture in fixtures:
        path = FIXTURES / f"{fixture['name']}.json"
        path.write_text(json.dumps(fixture, indent=2) + "\n")
        print(f"wrote {path.relative_to(FIXTURES.parent)}")
    print(f"\n{len(fixtures)} fixtures")


if __name__ == "__main__":
    main()
