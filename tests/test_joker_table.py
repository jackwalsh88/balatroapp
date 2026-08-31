"""The joker table's own invariants.

The governing requirement: every vanilla joker must be KNOWN. A joker the
player holds must never fall through to "unrecognised key" - the table has all
150, with the right name and rarity, whether or not its effect is modelled yet.

Known and modelled are different claims, and these tests keep them apart:

    known     the joker is in the table with its real name and rarity
    modelled  the scorer can compute what it contributes

Regressing `known` is a bug. `modelled` is a coverage number that goes up as
effect data is sourced, and is reported rather than asserted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from balatro_advisor.core import data, schema, scorer

ROOT = Path(__file__).resolve().parent.parent
ENUMERATION = json.loads((ROOT / "data" / "sources" / "enumeration.json").read_text())

VANILLA_COUNT = 150


def test_every_vanilla_joker_is_known():
    """The requirement, stated once and enforced."""
    table_names = {j["name"] for j in data.jokers().values()}
    missing = [j["name"] for j in ENUMERATION["jokers"] if j["name"] not in table_names]
    assert not missing, f"{len(missing)} vanilla jokers are not in the table: {missing}"


def test_the_table_holds_exactly_the_vanilla_set():
    assert len(data.jokers()) == VANILLA_COUNT


def test_no_invented_jokers():
    """Nothing in the table that is not a real vanilla joker."""
    real = {j["name"] for j in ENUMERATION["jokers"]}
    invented = [j["name"] for j in data.jokers().values() if j["name"] not in real]
    assert not invented, f"not real vanilla jokers: {invented}"


def test_rarities_match_the_enumeration():
    by_name = {j["name"]: j["rarity"] for j in ENUMERATION["jokers"]}
    wrong = [
        (j["name"], j.get("rarity"), by_name[j["name"]])
        for j in data.jokers().values()
        if j["name"] in by_name and j.get("rarity") != by_name[j["name"]]
    ]
    assert not wrong, f"rarity disagreements (name, ours, source): {wrong}"


def test_keys_are_unique():
    keys = [j["key"] for j in data.jokers().values()]
    assert len(keys) == len(set(keys))


def test_every_entry_declares_whether_it_is_modelled():
    """No entry may be silently neither modelled nor flagged."""
    for joker in data.jokers().values():
        modelled = joker.get("effects") is not None or joker.get("flag")
        flagged = joker.get("unmodelled") is True
        assert modelled or flagged, (
            f"{joker['name']} has no effects and is not marked unmodelled - it "
            f"would silently score as zero"
        )


def test_unsourced_entries_carry_provenance():
    """An entry with no description must say where it came from and that it needs work."""
    for joker in data.jokers().values():
        if joker.get("description"):
            continue
        assert joker.get("needs_verification") is True, joker["name"]
        assert joker.get("source"), joker["name"]


def test_an_enumerated_but_unmodelled_joker_scores_as_non_exact(state_factory):
    """Known is not the same as modelled, and the scorer must not conflate them."""
    pending = next(
        j for j in data.jokers().values() if j.get("needs_verification")
    )
    state = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": pending["key"]}]
    ))
    result = scorer.score_play(state, [0, 1])
    assert result.exact is False
    assert pending["key"] in result.unmodelled
    # It is named, not reported as an unknown key.
    assert any(pending["name"] in step for step in result.steps)


def test_coverage_is_reported_not_asserted(capsys):
    """Prints the modelling gap so it is visible on every run."""
    table = list(data.jokers().values())
    modelled = [j for j in table if not j.get("unmodelled")]
    pending = [j for j in table if j.get("needs_verification")]
    with capsys.disabled():
        print(
            f"\n  joker table: {len(table)} known, {len(modelled)} modelled, "
            f"{len(pending)} awaiting effect data"
        )
    assert len(table) == VANILLA_COUNT
