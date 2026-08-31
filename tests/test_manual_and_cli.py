"""Manual entry adapter and the CLI."""

from __future__ import annotations

import json
import re

import pytest

from balatro_advisor import cli
from balatro_advisor.adapters.manual import ManualSession, match_joker
from balatro_advisor.core import schema


# -- fuzzy joker matching ---------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("j_blue_joker", "j_blue_joker"),
    ("Blue Joker", "j_blue_joker"),
    ("blue joker", "j_blue_joker"),
    ("blue", "j_blue_joker"),
    ("stencil", "j_stencil"),
    ("photograph", "j_photograph"),
    ("gros michel", "j_gros_michel"),
    ("photogrph", "j_photograph"),
])
def test_joker_matching(text, expected):
    matches = match_joker(text)
    assert matches and matches[0]["key"] == expected


def test_nonsense_matches_nothing():
    assert match_joker("qqqzzzxxx") == []


def test_an_exact_name_wins_outright():
    """"joker" names the Joker exactly, so there is nothing to disambiguate."""
    matches = match_joker("joker")
    assert len(matches) == 1 and matches[0]["key"] == "j_joker"


def test_ambiguous_input_returns_several_options():
    """Better to ask than to guess - a misidentified joker is a wrong score."""
    assert len(match_joker("the")) > 1


# -- manual session ---------------------------------------------------------


class Script:
    """Feeds canned answers to the session's prompts."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.answers.pop(0) if self.answers else ""


def test_a_full_round_collects_valid_state(tmp_path):
    script = Script(
        "playing",   # phase
        "5",         # ante
        "45",        # money
        "3",         # hands remaining
        "2",         # discards remaining
        "boss",      # blind type
        "flint",     # boss name
        "22000",     # requirement
        "0",         # score so far
        "KH KD JS JH 3C",  # hand
        "n",         # set hand levels?
        "y",         # update jokers?
        "blue joker", "", "",   # joker 0: name, edition, stickers
        "",          # finish jokers
    )
    session = ManualSession(tmp_path / "session.json", ask=script, out=lambda _: None)
    state = session.collect()

    assert schema.validate(state) == []
    assert state["run"]["money"] == 45
    assert state["blind"]["key"] == "bl_flint"
    assert len(state["current_hand"]) == 5
    assert state["jokers"][0]["key"] == "j_blue_joker"


def test_blank_answers_keep_the_remembered_value(tmp_path):
    path = tmp_path / "session.json"
    first = Script(
        "playing", "5", "45", "3", "2", "small", "1000", "0",
        "KH KD 4C", "n", "n",
    )
    ManualSession(path, ask=first, out=lambda _: None).collect()

    # Second round: everything blank except the hand.
    second = Script(
        "", "", "", "", "", "", "", "",
        "AH AS 9D", "n", "n",
    )
    state = ManualSession(path, ask=second, out=lambda _: None).collect()

    assert state["run"]["money"] == 45, "money should have been remembered"
    assert state["run"]["ante"] == 5
    assert len(state["current_hand"]) == 3
    assert state["current_hand"][0]["rank"] == "A"


def test_seq_increments_across_rounds(tmp_path):
    path = tmp_path / "session.json"
    answers = ["playing", "1", "4", "4", "3", "small", "300", "0", "KH KD 4C", "n", "n"]
    first = ManualSession(path, ask=Script(*answers), out=lambda _: None).collect()
    second = ManualSession(path, ask=Script(*answers), out=lambda _: None).collect()
    assert second["seq"] == first["seq"] + 1


def test_a_scaling_joker_prompts_for_its_counter(tmp_path):
    script = Script(
        "playing", "1", "4", "4", "3", "small", "300", "0",
        "KH KD 4C", "n",
        "y",                      # update jokers
        "obelisk", "", "", "2.4",  # name, edition, stickers, counter
        "",
    )
    session = ManualSession(tmp_path / "s.json", ask=script, out=lambda _: None)
    state = session.collect()
    assert state["jokers"][0]["internal_state"]["counter"] == 2.4
    assert any("Current value" in q for q in script.asked)


def test_a_blank_counter_leaves_it_unknown_rather_than_zero(tmp_path):
    script = Script(
        "playing", "1", "4", "4", "3", "small", "300", "0",
        "KH KD 4C", "n", "y",
        "obelisk", "", "", "",
        "",
    )
    session = ManualSession(tmp_path / "s.json", ask=script, out=lambda _: None)
    state = session.collect()
    assert state["jokers"][0]["internal_state"]["counter"] is None


def test_invalid_input_is_refused_rather_than_guessed_at(tmp_path):
    script = Script(
        "playing", "1", "4", "4", "3", "small", "300", "0",
        "KH KD 4C", "n", "n",
    )
    session = ManualSession(tmp_path / "s.json", ask=script, out=lambda _: None)
    session.state["resources"]["hand_size"] = 1  # hand of 3 will not fit
    with pytest.raises(schema.StateInvalid):
        session.collect()


def test_bad_card_shorthand_reprompts(tmp_path):
    messages: list[str] = []
    script = Script(
        "playing", "1", "4", "4", "3", "small", "300", "0",
        "KH ZZ 4C",   # rejected
        "KH KD 4C",   # accepted
        "n", "n",
    )
    session = ManualSession(tmp_path / "s.json", ask=script, out=messages.append)
    state = session.collect()
    assert len(state["current_hand"]) == 3
    assert any("ZZ" in m for m in messages)


# -- CLI --------------------------------------------------------------------


def test_fixtures_command_passes(capsys):
    assert cli.main(["fixtures"]) == 0
    out = capsys.readouterr().out
    passed, total = re.search(r"(\d+)/(\d+) passed", out).groups()
    assert passed == total and int(total) > 0
    assert "hand_computed" in out


def test_validate_accepts_a_good_state(capsys):
    assert cli.main(["validate", "fixtures/steel_card_held_in_hand.json"]) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_rejects_a_bad_state(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "schema_version": 1, "seq": 1, "source": "manual", "phase": "playing",
        "run": {"ante": 1, "money": 5},
        "resources": {"hands_remaining": 1, "discards_remaining": 0},
        "jokers": [], "current_hand": [{"rank": "Z", "suit": "hearts"}],
    }))
    assert cli.main(["validate", str(bad)]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_advise_runs_offline(tmp_path, capsys):
    code = cli.main([
        "--log", str(tmp_path / "l.jsonl"),
        "--cache-dir", str(tmp_path / "c"),
        "--glossary", str(tmp_path / "g.json"),
        "advise", "fixtures/blue_joker_and_photograph.json", "--stub", "--explain",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "DECISION:" in out and "UNCERTAIN:" in out
    assert "230 x 14" in out, "the --explain chain should show the multiplication"
    assert "= floor(230 x 14) = 3220" in out


def test_advise_refuses_invalid_state(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "schema_version": 1, "seq": 1, "source": "manual", "phase": "playing",
        "run": {"ante": 1, "money": 5},
        "resources": {"hands_remaining": 1, "discards_remaining": 0},
        "jokers": [], "current_hand": [{"rank": "Z", "suit": "hearts"}],
    }))
    assert cli.main(["advise", str(bad), "--stub"]) == 2
    assert "Refusing to advise" in capsys.readouterr().err


def test_explain_ranks_plays(capsys):
    assert cli.main(["explain", "fixtures/lower_hand_outscores_higher.json", "--top", "3"]) == 0
    out = capsys.readouterr().out
    assert "1233" in out
    assert out.index("1233") < out.index("Blind requires") + len(out)


def test_outcome_and_log_commands(tmp_path, capsys):
    args = [
        "--log", str(tmp_path / "l.jsonl"),
        "--cache-dir", str(tmp_path / "c"),
        "--glossary", str(tmp_path / "g.json"),
    ]
    cli.main(args + ["advise", "fixtures/blue_joker_and_photograph.json", "--stub"])
    log_id = [
        line.split("log-id=")[1].rstrip("]")
        for line in capsys.readouterr().out.splitlines() if "log-id=" in line
    ][0]

    assert cli.main(args + [
        "outcome", log_id, "--action", "played_recommended", "--score", "20700"
    ]) == 0
    assert "SCORER DIVERGENCE" in capsys.readouterr().out

    assert cli.main(args + ["log"]) == 0
    assert "DIVERGENCE" in capsys.readouterr().out
