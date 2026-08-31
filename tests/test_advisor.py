"""Advisor pipeline: parsing, cache, log, regeneration, fallback, modes."""

from __future__ import annotations

import json

import pytest

from balatro_advisor.advisor import (
    Advisor,
    DecisionLog,
    Glossary,
    ResponseCache,
    StubProvider,
    parse_advice,
    parse_render,
    state_hash,
)
from balatro_advisor.advisor.client import ProviderError
from balatro_advisor.core import schema


@pytest.fixture
def state(state_factory):
    return schema.load_state(state_factory())


@pytest.fixture
def advisor(tmp_path):
    return Advisor(
        provider=StubProvider(),
        cache=ResponseCache(tmp_path / "cache"),
        log=DecisionLog(tmp_path / "log.jsonl"),
        glossary=Glossary(tmp_path / "glossary.json"),
    )


class ScriptedProvider:
    """Returns queued responses in order, recording what it was asked."""

    name = "scripted"

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, *, max_tokens=4000):
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("ScriptedProvider ran out of responses")
        return self.responses.pop(0)


class FailingProvider:
    name = "failing"

    def complete(self, system, user, *, max_tokens=4000):
        raise ProviderError("no credential configured")


def response(decision="Play cards [0, 1] (pair).", cards=(0, 1), **sections):
    body = {
        "REASONING": sections.get("reasoning", "It is the top-ranked play."),
        "ALTERNATIVES": sections.get("alternatives", "Nothing close."),
        "UNCERTAIN": sections.get("uncertain", "none"),
    }
    return (
        f"DECISION: {decision}\n"
        + "\n".join(f"{k}: {v}" for k, v in body.items())
        + "\n```json\n"
        + json.dumps({"kind": "play", "cards": list(cards)})
        + "\n```"
    )


# -- parsing ----------------------------------------------------------------


def test_parses_all_four_sections_and_the_action():
    advice = parse_advice(response())
    assert advice["decision"].startswith("Play cards")
    assert advice["reasoning"] == "It is the top-ranked play."
    assert advice["action"] == {"kind": "play", "cards": [0, 1]}
    assert advice["parse_errors"] == []


def test_multi_line_sections_are_kept_whole():
    advice = parse_advice(
        "DECISION: Sell it.\n"
        "REASONING: First line.\nSecond line.\n"
        "UNCERTAIN: none\n"
        '```json\n{"kind": "sell", "sell": [0]}\n```'
    )
    assert advice["reasoning"] == "First line.\nSecond line."


def test_uncertain_none_normalizes_to_empty():
    assert parse_advice(response())["uncertain"] == ""


def test_missing_action_block_is_a_parse_error():
    advice = parse_advice("DECISION: Play something.\nREASONING: Because.")
    assert any("no action block" in e for e in advice["parse_errors"])


def test_malformed_action_json_is_a_parse_error():
    advice = parse_advice("DECISION: x\n```json\n{not json}\n```")
    assert any("not valid JSON" in e for e in advice["parse_errors"])


def test_unknown_action_kind_is_a_parse_error():
    advice = parse_advice('DECISION: x\n```json\n{"kind": "teleport"}\n```')
    assert any("teleport" in e for e in advice["parse_errors"])


def test_play_without_cards_is_a_parse_error():
    advice = parse_advice('DECISION: x\n```json\n{"kind": "play"}\n```')
    assert any("must name the cards" in e for e in advice["parse_errors"])


def test_missing_decision_line_is_a_parse_error():
    advice = parse_advice('REASONING: hi\n```json\n{"kind": "skip"}\n```')
    assert any("no DECISION" in e for e in advice["parse_errors"])


# -- the state hash and cache key -------------------------------------------


def test_seq_does_not_change_the_hash(state):
    other = {**state, "seq": state["seq"] + 500}
    assert state_hash(state) == state_hash(other)


def test_captured_at_and_source_do_not_change_the_hash(state):
    other = {**state, "captured_at": "2026-01-01T00:00:00Z", "source": "vision"}
    assert state_hash(state) == state_hash(other)


def test_money_does_change_the_hash(state):
    other = {**state, "run": {**state["run"], "money": state["run"]["money"] + 1}}
    assert state_hash(state) != state_hash(other)


def test_key_order_does_not_change_the_hash(state):
    shuffled = dict(reversed(list(state.items())))
    assert state_hash(state) == state_hash(shuffled)


def test_expert_and_beginner_render_keys_never_collide(tmp_path, state):
    cache = ResponseCache(tmp_path)
    expert = cache.key(state, stage="render", mode="expert")
    beginner = cache.key(state, stage="render", mode="beginner")
    assert expert != beginner


def test_prompt_version_participates_in_the_key(tmp_path, state, monkeypatch):
    cache = ResponseCache(tmp_path)
    before = cache.key(state, stage="decision", mode="any")
    monkeypatch.setattr("balatro_advisor.advisor.cache.PROMPT_VERSION", 999)
    assert cache.key(state, stage="decision", mode="any") != before


def test_cache_round_trips(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("abc", "decision", "hello")
    assert cache.get("abc", "decision") == "hello"


def test_a_corrupt_entry_is_a_miss_not_a_crash(tmp_path):
    cache = ResponseCache(tmp_path)
    cache.put("abc", "decision", "hello")
    (tmp_path / "decision" / "abc.json").write_text("{ truncated")
    assert cache.get("abc", "decision") is None


def test_disabled_cache_never_returns_anything(tmp_path):
    cache = ResponseCache(tmp_path, enabled=False)
    cache.put("abc", "decision", "hello")
    assert cache.get("abc", "decision") is None


# -- the pipeline -----------------------------------------------------------


def test_valid_advice_is_returned_and_cached(tmp_path, state):
    provider = ScriptedProvider(response(), response())
    advisor = Advisor(
        provider=provider, cache=ResponseCache(tmp_path / "c"),
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    first = advisor.advise(state)
    assert not first.fell_back and not first.cache_hit

    second = advisor.advise(state)
    assert second.cache_hit
    assert len(provider.calls) == 1, "a cache hit must not call the model"


def test_invalid_advice_triggers_exactly_one_regeneration(tmp_path, state):
    bad = response(decision="Play cards [0, 99].", cards=(0, 99))
    provider = ScriptedProvider(bad, response())
    advisor = Advisor(
        provider=provider, cache=ResponseCache(tmp_path / "c"),
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    result = advisor.advise(state)
    assert result.regenerated and not result.fell_back
    assert len(provider.calls) == 2

    retry_prompt = provider.calls[1][1]
    assert "FAILED VALIDATION" in retry_prompt
    assert "not in current_hand" in retry_prompt


def test_failing_twice_falls_back_and_never_shows_the_advice(tmp_path, state):
    bad = response(decision="Play cards [0, 99].", cards=(0, 99))
    provider = ScriptedProvider(bad, bad)
    advisor = Advisor(
        provider=provider, cache=ResponseCache(tmp_path / "c"),
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    result = advisor.advise(state)
    assert result.fell_back
    assert result.action["cards"] == result.candidates[0]["cards"]
    assert result.reasoning == "", "the fallback carries no prose reasoning"
    assert "failed validation" in result.render().lower()
    assert "99" not in result.decision


def test_invalid_advice_is_never_cached(tmp_path, state):
    bad = response(decision="Play cards [0, 99].", cards=(0, 99))
    cache = ResponseCache(tmp_path / "c")
    advisor = Advisor(
        provider=ScriptedProvider(bad, bad), cache=cache,
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    advisor.advise(state)
    key = cache.key(state, stage="decision", mode="any", extra=None)
    assert cache.get(key, "decision") is None


def test_an_unreachable_model_falls_back_rather_than_inventing(tmp_path, state):
    advisor = Advisor(
        provider=FailingProvider(), cache=ResponseCache(tmp_path / "c"),
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    result = advisor.advise(state)
    assert result.fell_back
    assert result.action["kind"] == "play"


def test_cached_advice_is_revalidated(tmp_path, state):
    """Spec 5d: a cached response valid under an older ruleset may not be valid now."""
    cache = ResponseCache(tmp_path / "c")
    key = cache.key(state, stage="decision", mode="any", extra=None)
    cache.put(key, "decision", response(decision="Play cards [0, 99].", cards=(0, 99)))

    advisor = Advisor(
        provider=ScriptedProvider(response()), cache=cache,
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    result = advisor.advise(state)
    assert result.cache_hit and result.regenerated
    assert result.action["cards"] == [0, 1]


def test_stub_provider_produces_valid_advice(advisor, state):
    result = advisor.advise(state)
    assert not result.fell_back
    assert result.action["kind"] == "play"


# -- verbosity modes (spec 5a) ----------------------------------------------


def test_expert_and_beginner_agree_on_the_decision(tmp_path, state):
    """Spec 5a's hard constraint, and the regression test it names."""
    provider = ScriptedProvider(
        response(),
        "REASONING: Play the two Kings; a pair of Kings is your strongest.\n"
        "ALTERNATIVES: none worth noting\nUNCERTAIN: none",
    )
    advisor = Advisor(
        provider=provider, cache=ResponseCache(tmp_path / "c"),
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    expert = advisor.advise(state, mode="expert")
    beginner = advisor.advise(state, mode="beginner")

    assert expert.decision == beginner.decision
    assert expert.action == beginner.action
    assert expert.reasoning != beginner.reasoning, "only the explanation changes"


def test_the_render_stage_cannot_change_the_decision(state):
    """Structural, not merely instructed: the decision is copied in by code."""
    rendered = parse_render(
        "DECISION: Actually, discard everything instead.\n"
        "REASONING: I disagree with the above.\n"
        "ALTERNATIVES: none\nUNCERTAIN: none",
        decision="Play cards [0, 1] (pair).",
    )
    assert rendered["decision"] == "Play cards [0, 1] (pair)."


def test_beginner_mode_reuses_the_cached_decision(tmp_path, state):
    """Stage 1 is mode-independent, so switching mode costs one render call."""
    provider = ScriptedProvider(
        response(),
        "REASONING: Plain words.\nALTERNATIVES: none\nUNCERTAIN: none",
    )
    advisor = Advisor(
        provider=provider, cache=ResponseCache(tmp_path / "c"),
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=Glossary(tmp_path / "g.json"),
    )
    advisor.advise(state, mode="expert")
    advisor.advise(state, mode="beginner")
    assert len(provider.calls) == 2, "the decision call should not be repeated"


def test_an_unknown_mode_is_rejected(advisor, state):
    with pytest.raises(ValueError):
        advisor.advise(state, mode="intermediate")


def test_glossary_does_not_re_explain_a_term(tmp_path, state):
    glossary = Glossary(tmp_path / "g.json")
    provider = ScriptedProvider(
        response(),
        "REASONING: Your xmult is doubled here.\nALTERNATIVES: none\nUNCERTAIN: none",
        response(decision="Play cards [1, 2]."),
        "REASONING: Same again.\nALTERNATIVES: none\nUNCERTAIN: none",
    )
    advisor = Advisor(
        provider=provider, cache=ResponseCache(tmp_path / "c", enabled=False),
        log=DecisionLog(tmp_path / "l.jsonl"), glossary=glossary,
    )
    first = advisor.advise(state, mode="beginner")
    assert "xmult" in first.glossed_this_turn

    advisor.advise(state, mode="beginner")
    assert "xmult" in provider.calls[-1][1], "the prompt must list it as already glossed"
    assert "do not re-explain" in provider.calls[-1][1]


# -- decision log (spec 5c) -------------------------------------------------


def test_every_call_is_logged(tmp_path, state):
    log = DecisionLog(tmp_path / "l.jsonl")
    advisor = Advisor(
        provider=StubProvider(), cache=ResponseCache(tmp_path / "c", enabled=False),
        log=log, glossary=Glossary(tmp_path / "g.json"),
    )
    advisor.advise(state)
    advisor.advise(state)
    assert len(list(log.entries())) == 2


def test_outcome_records_predicted_against_actual(tmp_path, state):
    log = DecisionLog(tmp_path / "l.jsonl")
    advisor = Advisor(
        provider=StubProvider(), cache=ResponseCache(tmp_path / "c"),
        log=log, glossary=Glossary(tmp_path / "g.json"),
    )
    result = advisor.advise(state)
    predicted = result.candidates[0]["score"]

    assert log.record_outcome(
        result.log_id, action_taken="played_recommended", actual_score=predicted
    )
    entry = next(e for e in log.entries() if e["id"] == result.log_id)
    assert entry["outcome"]["predicted_score"] == predicted
    assert log.divergences() == []


def test_a_scorer_divergence_is_surfaced(tmp_path, state):
    """The field that separates a scorer bug from a judgment bug."""
    log = DecisionLog(tmp_path / "l.jsonl")
    advisor = Advisor(
        provider=StubProvider(), cache=ResponseCache(tmp_path / "c"),
        log=log, glossary=Glossary(tmp_path / "g.json"),
    )
    result = advisor.advise(state)
    log.record_outcome(
        result.log_id, action_taken="played_recommended", actual_score=20700
    )
    divergences = log.divergences()
    assert len(divergences) == 1
    assert divergences[0]["actual_score"] == 20700
    assert divergences[0]["predicted_score"] == result.candidates[0]["score"]


def test_an_invalid_action_taken_value_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        DecisionLog(tmp_path / "l.jsonl").record_outcome("x", action_taken="maybe")


def test_a_torn_log_line_costs_one_entry_not_the_file(tmp_path, state):
    log = DecisionLog(tmp_path / "l.jsonl")
    advisor = Advisor(
        provider=StubProvider(), cache=ResponseCache(tmp_path / "c", enabled=False),
        log=log, glossary=Glossary(tmp_path / "g.json"),
    )
    advisor.advise(state)
    advisor.advise(state)
    with open(log.path, "a") as handle:
        handle.write('{"id": "torn", "sta\n')
    assert len(list(log.entries())) == 2
