"""Capturing a played hand as ground truth.

The distinction these tests protect: a captured fixture's expectation comes
from the GAME. Recording what the scorer said instead would produce a fixture
that can never fail, which is worse than having no fixture at all.
"""

from __future__ import annotations

import json

import pytest

from balatro_advisor.advisor import Advisor, DecisionLog, Glossary, OfflineProvider, ResponseCache
from balatro_advisor.capture import CaptureError, build_fixture, suggest_name, write_fixture
from balatro_advisor.core import schema


@pytest.fixture
def entry(tmp_path, state_factory):
    """A real log entry from a real advise run."""
    log = DecisionLog(tmp_path / "l.jsonl")
    Advisor(
        provider=OfflineProvider(), cache=ResponseCache(tmp_path / "c", enabled=False),
        log=log, glossary=Glossary(tmp_path / "g.json"),
    ).advise(schema.load_state(state_factory()))
    return list(log.entries())[-1]


def test_a_matching_score_records_agreement(entry):
    predicted = entry["candidate_plays"][0]["score"]
    fixture = build_fixture(entry, actual_score=predicted)
    assert fixture["provenance"] == "captured"
    assert fixture["expected_score"] == predicted
    assert fixture["agrees_at_capture"] is True
    assert "disagreement" not in fixture


def test_a_disagreement_is_recorded_as_the_games_word(entry):
    """The game wins. The scorer's answer is kept only as a record."""
    predicted = entry["candidate_plays"][0]["score"]
    fixture = build_fixture(entry, actual_score=predicted + 500)

    assert fixture["expected_score"] == predicted + 500, "the game's score is the expectation"
    assert fixture["scorer_said"]["score"] == predicted, "ours is recorded, not expected"
    assert fixture["agrees_at_capture"] is False
    assert "will fail until the scorer is fixed" in fixture["disagreement"]


def test_chips_and_mult_are_optional(entry):
    """Reading the score off the game is easy; the intermediates are fiddly."""
    fixture = build_fixture(entry, actual_score=999)
    assert "expected_chips" not in fixture
    assert "expected_mult" not in fixture

    detailed = build_fixture(entry, actual_score=999, actual_chips=30, actual_mult=33.3)
    assert detailed["expected_chips"] == 30
    assert detailed["expected_mult"] == 33.3


def test_a_non_play_action_cannot_be_captured(entry):
    entry["advice"]["action"] = {"kind": "reroll"}
    with pytest.raises(CaptureError, match="not a play"):
        build_fixture(entry, actual_score=100)


def test_an_invalid_logged_state_is_refused(entry):
    entry["state"]["current_hand"][0]["rank"] = "Z"
    with pytest.raises(CaptureError, match="not valid"):
        build_fixture(entry, actual_score=100)


def test_capturing_never_overwrites_an_earlier_observation(tmp_path, entry):
    """Two captures of the same board are still two observations."""
    fixture = build_fixture(entry, actual_score=120, name="same_name")
    first = write_fixture(fixture, tmp_path)
    second = write_fixture(fixture, tmp_path)
    assert first != second
    assert first.exists() and second.exists()


def test_the_written_fixture_is_valid_and_reloadable(tmp_path, entry):
    path = write_fixture(build_fixture(entry, actual_score=120), tmp_path)
    reloaded = json.loads(path.read_text())
    assert schema.validate(reloaded["state_before"]) == []
    assert reloaded["cards_played"]


def test_the_generated_name_is_readable(state_factory):
    name = suggest_name(state_factory(), "two_pair", 20700)
    assert name.startswith("captured_")
    assert "two_pair" in name and "20700" in name
