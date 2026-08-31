"""The joker table's own invariants.

The governing requirement: every vanilla joker must be KNOWN. A joker the
player holds must never fall through to "unrecognised key" - the table has all
150, with the game's own key, name, rarity and cost, whether or not its effect
is modelled yet.

Known and modelled are different claims, and these tests keep them apart:

    known     the joker is in the table with its real key, name and rarity
    modelled  the scorer can compute what it contributes

Regressing `known` is a bug. `modelled` is a coverage number that rises as
effects are translated into the effect grammar, and is reported rather than
asserted.

The reference is data/sources/merged.json - game.lua for keys, rarity, cost and
numeric constants; the wiki table for effect text, type and activation.
data/sources/enumeration.json is kept as an INDEPENDENT third source and is
used only to cross-check the count, not the names: it disagrees on spelling in
a few places (Canio, Riff-Raff, Séance) where the wiki is the display-name
authority.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from balatro_advisor.core import data, schema, scorer

ROOT = Path(__file__).resolve().parent.parent
MERGED = json.loads((ROOT / "data" / "sources" / "merged.json").read_text())
ENUMERATION = json.loads((ROOT / "data" / "sources" / "enumeration.json").read_text())

VANILLA_COUNT = 150


# -- known ------------------------------------------------------------------


def test_every_vanilla_joker_is_known():
    """The requirement, stated once and enforced."""
    table = set(data.jokers())
    missing = [j["key"] for j in MERGED["jokers"] if j["key"] not in table]
    assert not missing, f"{len(missing)} vanilla jokers are not in the table: {missing}"


def test_the_table_holds_exactly_the_vanilla_set():
    assert len(data.jokers()) == VANILLA_COUNT


def test_two_independent_sources_agree_on_the_count():
    """game.lua and an Immolate-derived seed searcher both say 150."""
    assert len(MERGED["jokers"]) == ENUMERATION["count"] == VANILLA_COUNT


def test_no_invented_jokers():
    real = {j["key"] for j in MERGED["jokers"]}
    invented = [k for k in data.jokers() if k not in real]
    assert not invented, f"not real vanilla jokers: {invented}"


def test_keys_names_rarity_and_cost_match_the_game():
    by_key = {j["key"]: j for j in MERGED["jokers"]}
    wrong = []
    for key, joker in data.jokers().items():
        source = by_key[key]
        for field in ("name", "rarity", "cost"):
            if joker.get(field) != source[field]:
                wrong.append((key, field, joker.get(field), source[field]))
    assert not wrong, f"(key, field, ours, game): {wrong}"


def test_keys_are_unique():
    keys = [j["key"] for j in data.jokers().values()]
    assert len(keys) == len(set(keys))


def test_keys_are_not_derivable_from_names():
    """Guards the assumption that burned 36 keys.

    The game shortens many keys and misspells one, so a name-to-key rule is
    wrong. If this ever starts passing, someone has "tidied" the table into
    consistency and broken it against the game.
    """
    def derive(name: str) -> str:
        return "j_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

    mismatched = [
        j["key"] for j in data.jokers().values() if derive(j["name"]) != j["key"]
    ]
    assert mismatched, "keys now all follow from names, which the game does not"
    assert "j_gluttenous_joker" in data.jokers(), (
        "game.lua spells Gluttonous Joker's key 'j_gluttenous_joker'; that typo "
        "is the real key and must not be corrected"
    )


# -- honesty ----------------------------------------------------------------


def test_every_entry_declares_whether_it_is_modelled():
    """No entry may be silently neither modelled nor flagged."""
    for joker in data.jokers().values():
        modelled = joker.get("effects") is not None or joker.get("flag")
        flagged = joker.get("unmodelled") is True
        assert modelled or flagged, (
            f"{joker['name']} has no effects and is not marked unmodelled - it "
            f"would silently score as zero"
        )


def test_every_entry_carries_provenance_and_a_description():
    for joker in data.jokers().values():
        assert joker.get("source"), joker["name"]
        assert joker.get("description"), joker["name"]


def test_every_entry_records_the_games_own_config():
    """The constants live in the table, so a modelled effect can be checked."""
    by_key = {j["key"]: j for j in MERGED["jokers"]}
    for key, joker in data.jokers().items():
        assert joker.get("config") == by_key[key]["config"], key


def test_an_unmodelled_joker_scores_as_non_exact(state_factory):
    """Known is not the same as modelled, and the scorer must not conflate them."""
    pending = next(j for j in data.jokers().values() if j.get("unmodelled"))
    state = schema.load_state(state_factory(
        jokers=[{"position": 0, "key": pending["key"]}]
    ))
    result = scorer.score_play(state, [0, 1])
    assert result.exact is False
    assert pending["key"] in result.unmodelled
    # It is named, not reported as an unknown key.
    assert any(pending["name"] in step for step in result.steps)


def test_no_stale_joker_key_references_anywhere():
    """Every j_* key mentioned in the repo must exist in the table.

    36 keys changed when the table was rebuilt on game.lua. A fixture or test
    still naming an old key would silently exercise the unknown-joker path
    instead of the joker it meant.
    """
    table = set(data.jokers())
    allowed_absent = {"j_definitely_not_real"}  # deliberate unknown-key test
    stale: dict[str, set[str]] = {}

    for path in (
        list((ROOT / "tests").rglob("*.py"))
        + list((ROOT / "tools").rglob("*.py"))
        + list((ROOT / "fixtures").glob("*.json"))
        + list((ROOT / "src").rglob("*.py"))
    ):
        for key in re.findall(r'"(j_[a-z0-9_]+)"', path.read_text()):
            if key not in table and key not in allowed_absent:
                stale.setdefault(key, set()).add(path.name)

    assert not stale, f"stale joker keys: { {k: sorted(v) for k, v in stale.items()} }"


# -- coverage ---------------------------------------------------------------


def test_coverage_is_reported_not_asserted(capsys):
    """Prints the modelling gap so it is visible on every run."""
    table = list(data.jokers().values())
    modelled = [j for j in table if not j.get("unmodelled")]
    with capsys.disabled():
        print(
            f"\n  joker table: {len(table)} known, {len(modelled)} modelled, "
            f"{len(table) - len(modelled)} to model"
        )
    assert len(table) == VANILLA_COUNT
