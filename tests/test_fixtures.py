"""Run every replay fixture against the scorer.

Spec step 8: "Run the full fixture set on every scorer change. A scorer that
passes twenty real hands is a scorer you can build on."

Each fixture carries its derivation in an ``arithmetic`` field. When one of
these fails, read that field first - the fixture is as likely to be wrong as
the scorer, and the derivation is there so you can tell which.
"""

from __future__ import annotations

import json

import pytest

from balatro_advisor.core import enumerate as enumerate_module
from balatro_advisor.core import schema, scorer

from .conftest import fixture_files


def _fixtures():
    return [(path.stem, json.loads(path.read_text())) for path in fixture_files()]


@pytest.mark.parametrize("name,fixture", _fixtures(), ids=[n for n, _ in _fixtures()])
def test_fixture_state_is_valid(name, fixture):
    assert schema.validate(fixture["state_before"]) == []


@pytest.mark.parametrize("name,fixture", _fixtures(), ids=[n for n, _ in _fixtures()])
def test_fixture_scores_as_derived(name, fixture):
    if fixture.get("cards_played") is None:
        pytest.skip("no-score fixture: exercises ingest and validation only")

    state = schema.load_state(fixture["state_before"])
    result = scorer.score_play(state, fixture["cards_played"])

    derivation = fixture.get("arithmetic") or "(no derivation recorded)"
    assert result.chips == pytest.approx(fixture["expected_chips"]), derivation
    assert result.mult == pytest.approx(fixture["expected_mult"]), derivation
    assert result.score == fixture["expected_score"], derivation

    if "expected_exact" in fixture:
        assert result.exact is fixture["expected_exact"], derivation
    if "expected_stochastic" in fixture:
        assert result.stochastic is fixture["expected_stochastic"], derivation
    if "expected_unmodelled" in fixture:
        assert result.unmodelled == fixture["expected_unmodelled"]
    if "expected_gold_forfeited" in fixture:
        assert result.gold_forfeited == fixture["expected_gold_forfeited"]
    if "also_assert_top_ranked_play" in fixture:
        top = enumerate_module.enumerate_plays(state)[0]
        assert top["cards"] == fixture["also_assert_top_ranked_play"], (
            fixture.get("counterintuitive") or ""
        )


def test_fixture_set_covers_the_required_ground():
    """Spec step 8 names specific coverage the fixture set must have."""
    fixtures = [f for _, f in _fixtures()]
    phases = {f["state_before"]["phase"] for f in fixtures}
    assert {"playing", "shop", "pack_open"} <= phases, (
        "spec step 8: cover shop, playing and pack-open phases"
    )
    assert any(f.get("counterintuitive") for f in fixtures), (
        "spec step 8 requires at least one fixture whose correct answer is "
        "counterintuitive - those are the cases where a plausible-but-wrong "
        "implementation still passes everything else"
    )
    assert any(f.get("expected_exact") is False for f in fixtures), (
        "at least one fixture must exercise the non-exact honesty path"
    )


def test_every_fixture_declares_provenance():
    """A fixture without provenance is a fixture of unknown worth.

    This does not require captured fixtures - there are none yet, and section 0
    explains why. It requires each fixture to say which it is.
    """
    for name, fixture in _fixtures():
        assert fixture.get("provenance") in ("captured", "hand_computed"), name
