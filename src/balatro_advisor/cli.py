"""Command line interface.

    balatro-advisor advise [STATE.json]   get advice (or enter state by hand)
    balatro-advisor explain STATE.json    the scorer's full multiplication chain
    balatro-advisor validate STATE.json   check a state document against the schema
    balatro-advisor fixtures              run every fixture against the scorer
    balatro-advisor outcome ID --score N  record what actually happened
    balatro-advisor log                   show scorer-vs-reality divergences
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .adapters.manual import DEFAULT_SESSION_PATH, ManualSession
from .advisor import Advisor, DecisionLog, Glossary, ResponseCache
from .core import cards as card_utils
from .core import enumerate as enumerate_module
from .core import schema, scorer

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _load(path: str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text())
    # Accept a fixture file directly - it is the most common thing to hand this.
    if "state_before" in raw:
        raw = raw["state_before"]
    return schema.load_state(raw)


# --------------------------------------------------------------------------


def cmd_advise(args: argparse.Namespace) -> int:
    if args.state:
        try:
            state = _load(args.state)
        except schema.StateInvalid as exc:
            print("Refusing to advise on invalid state:", file=sys.stderr)
            for error in exc.errors:
                print(f"  - {error}", file=sys.stderr)
            return 2
    else:
        session = ManualSession(None if args.no_session else DEFAULT_SESSION_PATH)
        try:
            state = session.collect()
        except schema.StateInvalid:
            return 2
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 130

    advisor = Advisor(
        cache=ResponseCache(args.cache_dir, enabled=not args.no_cache),
        log=DecisionLog(args.log),
        glossary=Glossary(args.glossary),
        force_stub=args.stub,
    )
    result = advisor.advise(state, mode=args.mode, question=args.question)

    if args.explain:
        _print_chain(state, result)

    print(result.render())

    trailer = [f"mode={result.mode}", f"provider={result.provider}"]
    if result.cache_hit:
        trailer.append("cached")
    if result.regenerated:
        trailer.append("regenerated-after-validation-failure")
    if result.log_id:
        trailer.append(f"log-id={result.log_id}")
    print(f"\n[{' | '.join(trailer)}]")

    if result.glossed_this_turn:
        print(f"[glossed this session: {', '.join(result.glossed_this_turn)}]")
    return 0


def _print_chain(state: dict[str, Any], result: Any) -> None:
    """Show the arithmetic behind the recommendation, step by step."""
    action = result.action
    if action.get("kind") != "play":
        print("(no play to explain - the recommendation is not a hand)\n")
        return
    detail = scorer.score_play(state, action["cards"])
    played = card_utils.format_hand(
        [state["current_hand"][i] for i in action["cards"]], verbose=True
    )
    print(f"SCORING CHAIN for {played} ({detail.hand_type.replace('_', ' ')})")
    for step in detail.steps:
        print(f"  {step}")
    joiner = "~" if detail.stochastic else "="
    print(f"  {joiner} floor({detail.chips:g} x {detail.mult:g}) {joiner} {detail.score}")
    if detail.stochastic:
        print(
            "  ~ EXPECTED value: a random effect is in play, so this is the mean "
            "computed from the game's own odds, not a certainty."
        )
    if not detail.exact:
        print(
            f"  ! NOT EXACT. Unmodelled: {', '.join(detail.unmodelled)}. "
            f"Treat {detail.score} as a floor, not a score."
        )
    print()


def cmd_explain(args: argparse.Namespace) -> int:
    state = _load(args.state)
    plays = enumerate_module.enumerate_plays(state, limit=args.top)
    if not plays:
        print("No candidate plays (is this a playing-phase state with cards in hand?)")
        return 1

    print(f"Hand: {card_utils.format_hand(state['current_hand'], verbose=True, index=True)}\n")
    requirement = (state.get("blind") or {}).get("requirement")
    if requirement:
        print(f"Blind requires {requirement}\n")

    for rank, play in enumerate(plays):
        marker = {True: "CLEARS", False: "short", None: "?"}[play.get("clears_blind")]
        exact = "" if play.get("exact", True) else "  [FLOOR - " + ", ".join(play["unmodelled"]) + "]"
        cards = card_utils.format_hand([state["current_hand"][i] for i in play["cards"]])
        print(
            f"{rank + 1:2}. {play['score']:>10}  {marker:<6} "
            f"{play['hand_type'].replace('_', ' '):<16} {cards}{exact}"
        )
        if rank == 0 and args.chain:
            detail = scorer.score_play(state, play["cards"])
            for step in detail.steps:
                print(f"      {step}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.state).read_text())
    if "state_before" in raw:
        raw = raw["state_before"]
    errors = schema.validate(raw)
    if errors:
        print(f"INVALID ({len(errors)} problem(s)):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("valid")
    return 0


def cmd_fixtures(args: argparse.Namespace) -> int:
    """Run every fixture against the scorer. Spec step 8's regression gate."""
    paths = sorted(FIXTURES.glob("*.json"))
    if not paths:
        print(f"no fixtures found in {FIXTURES}", file=sys.stderr)
        return 1

    failures = 0
    for path in paths:
        fixture = json.loads(path.read_text())
        name = fixture["name"]
        try:
            state = schema.load_state(fixture["state_before"])
        except schema.StateInvalid as exc:
            print(f"FAIL {name}: state invalid: {exc.errors}")
            failures += 1
            continue

        if fixture.get("cards_played") is None:
            print(f"ok   {name}  (no-score fixture: ingest and validation only)")
            continue

        result = scorer.score_play(state, fixture["cards_played"])
        problems = []
        checks = (
            ("chips", result.chips, fixture["expected_chips"]),
            ("mult", result.mult, fixture["expected_mult"]),
            ("score", result.score, fixture["expected_score"]),
        )
        for label, got, want in checks:
            if abs(got - want) > 1e-6:
                problems.append(f"{label} {got:g} != {want:g}")
        if "expected_stochastic" in fixture and result.stochastic != fixture["expected_stochastic"]:
            problems.append(f"stochastic {result.stochastic} != {fixture['expected_stochastic']}")
        if "expected_exact" in fixture and result.exact != fixture["expected_exact"]:
            problems.append(f"exact {result.exact} != {fixture['expected_exact']}")
        if "expected_unmodelled" in fixture and result.unmodelled != fixture["expected_unmodelled"]:
            problems.append(f"unmodelled {result.unmodelled} != {fixture['expected_unmodelled']}")
        if "expected_gold_forfeited" in fixture and result.gold_forfeited != fixture["expected_gold_forfeited"]:
            problems.append(f"gold_forfeited {result.gold_forfeited} != {fixture['expected_gold_forfeited']}")
        if "also_assert_top_ranked_play" in fixture:
            top = enumerate_module.enumerate_plays(state)[0]
            if top["cards"] != fixture["also_assert_top_ranked_play"]:
                problems.append(f"top-ranked play {top['cards']} != {fixture['also_assert_top_ranked_play']}")

        if problems:
            print(f"FAIL {name}: {'; '.join(problems)}")
            if args.verbose:
                for step in result.steps:
                    print(f"       {step}")
            failures += 1
        else:
            print(f"ok   {name}  {result.chips:g} x {result.mult:g} = {result.score}")

    captured = sum(
        1 for p in paths if json.loads(p.read_text()).get("provenance") == "captured"
    )
    print(f"\n{len(paths) - failures}/{len(paths)} passed")
    print(f"provenance: {captured} captured, {len(paths) - captured} hand_computed")
    if captured == 0:
        print(
            "  NOTE: no captured fixtures yet. Hand-computed fixtures prove the "
            "scorer is\n  self-consistent; only captured ones prove it matches "
            "the game. See fixtures/README.md."
        )
    return 1 if failures else 0


def cmd_outcome(args: argparse.Namespace) -> int:
    log = DecisionLog(args.log)
    ok = log.record_outcome(
        args.id,
        action_taken=args.action,
        actual_score=args.score,
        cleared_blind=args.cleared,
        ante_survived=args.ante_survived,
    )
    if not ok:
        print(f"no log entry with id {args.id!r}", file=sys.stderr)
        return 1
    print(f"recorded outcome for {args.id}")
    for divergence in log.divergences():
        if divergence["id"] == args.id:
            print(
                f"  SCORER DIVERGENCE: predicted {divergence['predicted_score']}, "
                f"actual {divergence['actual_score']} "
                f"({divergence['error_pct']}% off). The arithmetic is wrong, not "
                f"the judgment."
            )
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    log = DecisionLog(args.log)
    entries = list(log.entries())
    if not entries:
        print("no decisions logged yet")
        return 0
    with_outcome = [e for e in entries if e.get("outcome")]
    divergences = log.divergences()

    print(f"{len(entries)} decisions logged, {len(with_outcome)} with a recorded outcome")
    if len(with_outcome) < 50:
        print(
            f"  ({50 - len(with_outcome)} more outcomes before the log starts "
            f"showing where the advisor is actually wrong.)"
        )
    if not divergences:
        print("\nNo predicted-vs-actual divergences. The scorer matches reality so far.")
        return 0

    print(f"\n{len(divergences)} SCORER DIVERGENCE(S) - these are arithmetic bugs:")
    for divergence in divergences:
        print(
            f"  {divergence['id']}: predicted {divergence['predicted_score']}, "
            f"actual {divergence['actual_score']} ({divergence['error_pct']}% off)"
        )
        print(f"    {divergence['decision']}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    session = ManualSession(DEFAULT_SESSION_PATH)
    session.reset()
    Glossary(args.glossary).reset()
    removed = ResponseCache(args.cache_dir).clear()
    print(f"session and glossary reset; {removed} cache entries removed")
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="balatro-advisor",
        description="Read Balatro state, compute scores exactly, advise on them.",
    )
    parser.add_argument("--log", default="logs/decisions.jsonl", help="decision log path")
    parser.add_argument("--cache-dir", default="cache", help="response cache directory")
    parser.add_argument("--glossary", default="logs/glossary.json", help="beginner-mode glossary path")
    sub = parser.add_subparsers(dest="command", required=True)

    advise = sub.add_parser("advise", help="get advice on a state")
    advise.add_argument("state", nargs="?", help="state JSON (omit for manual entry)")
    advise.add_argument("--mode", choices=("expert", "beginner"), default="expert")
    advise.add_argument("--question", help="a specific question instead of 'what should I do?'")
    advise.add_argument("--explain", action="store_true", help="show the scoring chain")
    advise.add_argument("--no-cache", action="store_true", help="bypass the response cache")
    advise.add_argument("--stub", action="store_true", help="offline: no model call")
    advise.add_argument("--no-session", action="store_true", help="do not persist manual entry")
    advise.set_defaults(func=cmd_advise)

    explain = sub.add_parser("explain", help="rank every legal play, with arithmetic")
    explain.add_argument("state")
    explain.add_argument("--top", type=int, default=10)
    explain.add_argument("--chain", action="store_true", help="show the top play's full chain")
    explain.set_defaults(func=cmd_explain)

    validate = sub.add_parser("validate", help="check a state document")
    validate.add_argument("state")
    validate.set_defaults(func=cmd_validate)

    fixtures = sub.add_parser("fixtures", help="run the fixture set against the scorer")
    fixtures.add_argument("--verbose", action="store_true")
    fixtures.set_defaults(func=cmd_fixtures)

    outcome = sub.add_parser("outcome", help="record what actually happened")
    outcome.add_argument("id", help="log id from an earlier advise run")
    outcome.add_argument(
        "--action", required=True,
        choices=("played_recommended", "played_other", "ignored"),
    )
    outcome.add_argument("--score", type=int, help="the score the game actually awarded")
    outcome.add_argument("--cleared", action="store_true", help="the blind was cleared")
    outcome.add_argument("--ante-survived", action="store_true")
    outcome.set_defaults(func=cmd_outcome)

    log_cmd = sub.add_parser("log", help="summarize the decision log")
    log_cmd.set_defaults(func=cmd_log)

    reset = sub.add_parser("reset", help="clear session, glossary and cache")
    reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except schema.StateInvalid as exc:
        print("Invalid state:", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
