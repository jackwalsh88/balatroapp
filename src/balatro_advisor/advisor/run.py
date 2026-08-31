"""The advisory pipeline.

    enumerate -> cache -> stage 1 (decision) -> parse -> VALIDATE
                                                           | fail
                                                    regenerate once
                                                           | fail again
                                                    deterministic fallback
                              -> stage 2 (beginner render, optional) -> output

Two rules from spec 5d that are easy to get wrong and are enforced here:

1. Validation runs on cache hits too. A cached response that was valid under an
   older ruleset may not be valid now.
2. Advice that fails twice is never shown. It degrades to the top-ranked
   candidate with no prose and a note that advisory generation failed. A
   refusal is recoverable; confident wrong advice is what loses trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import validator
from ..core import enumerate as enumerate_module
from . import parse, prompts
from .cache import ResponseCache, state_hash
from .client import Provider, ProviderError, default_provider
from .decision_log import DecisionLog
from .glossary import Glossary

__all__ = ["Advisor", "AdviceResult", "MODES"]

MODES = ("expert", "beginner")

_MAX_CANDIDATES = 12
_MAX_DISCARDS = 8


@dataclass
class AdviceResult:
    decision: str
    reasoning: str = ""
    alternatives: str = ""
    uncertain: str = ""
    mode: str = "expert"
    action: dict[str, Any] = field(default_factory=lambda: {"kind": "none"})
    candidates: list[dict[str, Any]] = field(default_factory=list)
    discards: list[dict[str, Any]] = field(default_factory=list)
    cache_hit: bool = False
    regenerated: bool = False
    fell_back: bool = False
    findings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    glossed_this_turn: list[str] = field(default_factory=list)
    log_id: str | None = None
    provider: str = ""

    def render(self) -> str:
        """The spec section 5 output format."""
        lines = [
            f"DECISION: {self.decision}",
            f"REASONING: {self.reasoning}",
            f"ALTERNATIVES: {self.alternatives or 'none'}",
            f"UNCERTAIN: {self.uncertain or 'none'}",
        ]
        if self.fell_back:
            lines.append(
                "\nNOTE: advisory generation failed validation twice. The above is "
                "the deterministic top-ranked play, with no model reasoning behind it."
            )
        if self.flags:
            lines.append("\nFLAGGED FOR REVIEW (shown, not blocked):")
            lines.extend(f"  - {f}" for f in self.flags)
        return "\n".join(lines)


class Advisor:
    def __init__(
        self,
        provider: Provider | None = None,
        cache: ResponseCache | None = None,
        log: DecisionLog | None = None,
        glossary: Glossary | None = None,
        *,
        force_stub: bool = False,
    ) -> None:
        self.provider = provider or default_provider(force_stub=force_stub)
        self.cache = cache if cache is not None else ResponseCache()
        self.log = log if log is not None else DecisionLog()
        self.glossary = glossary if glossary is not None else Glossary()

    # ----------------------------------------------------------------------

    def advise(
        self,
        state: dict[str, Any],
        *,
        mode: str = "expert",
        question: str | None = None,
    ) -> AdviceResult:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

        candidates = enumerate_module.enumerate_plays(state, limit=_MAX_CANDIDATES)
        discards = enumerate_module.enumerate_discards(state, limit=_MAX_DISCARDS)

        advice, cache_hit, regenerated, fell_back, report = self._decide(
            state, candidates, discards, question
        )

        result = AdviceResult(
            decision=advice.get("decision", ""),
            reasoning=advice.get("reasoning", ""),
            alternatives=advice.get("alternatives", ""),
            uncertain=advice.get("uncertain", ""),
            mode=mode,
            action=advice.get("action") or {"kind": "none"},
            candidates=candidates,
            discards=discards,
            cache_hit=cache_hit,
            regenerated=regenerated,
            fell_back=fell_back,
            findings=[str(f) for f in report.failures],
            flags=[f.message for f in report.flags],
            provider=self.provider.name,
        )

        if mode == "beginner" and not fell_back:
            self._render_beginner(state, advice, result)

        result.log_id = self.log.record(
            state=state,
            state_hash=state_hash(state),
            mode=mode,
            advice={**advice, **{
                "reasoning": result.reasoning,
                "alternatives": result.alternatives,
                "uncertain": result.uncertain,
            }},
            candidates=candidates,
            cache_hit=cache_hit,
            validation=result.findings + result.flags,
            provider=self.provider.name,
        )
        return result

    # ----------------------------------------------------------------------

    def _decide(
        self,
        state: dict[str, Any],
        candidates: list[dict[str, Any]],
        discards: list[dict[str, Any]],
        question: str | None,
    ) -> tuple[dict[str, Any], bool, bool, bool, validator.Report]:
        """Stage 1, with one revalidated retry and a deterministic fallback."""
        # Stage 1 is mode-independent, so its cache entry is shared across
        # modes: switching to beginner costs the render call only.
        key = self.cache.key(state, stage="decision", mode="any", extra=question)

        cached = self.cache.get(key, "decision")
        cache_hit = cached is not None

        if cache_hit:
            advice = parse.parse_advice(cached)
        else:
            advice = self._call_stage1(state, candidates, discards, question)

        report = self._validate(advice, state, candidates, discards)

        if report.ok:
            if not cache_hit:
                self.cache.put(key, "decision", advice["raw"])
            return advice, cache_hit, False, False, report

        # Regenerate once, with the failed checks appended as constraints.
        retry = self._call_stage1(
            state, candidates, discards, question,
            constraints=report.constraint_text(),
        )
        retry_report = self._validate(retry, state, candidates, discards)
        if retry_report.ok:
            self.cache.put(key, "decision", retry["raw"])
            return retry, cache_hit, True, False, retry_report

        # Failed twice. Never show it.
        return self._fallback(candidates), cache_hit, True, True, retry_report

    def _call_stage1(
        self,
        state: dict[str, Any],
        candidates: list[dict[str, Any]],
        discards: list[dict[str, Any]],
        question: str | None,
        constraints: str | None = None,
    ) -> dict[str, Any]:
        user = prompts.build_decision_user(
            state, candidates, discards, question, extra_constraints=constraints
        )
        try:
            text = self.provider.complete(prompts.DECISION_SYSTEM, user)
        except ProviderError as exc:
            # An unreachable model is not a reason to invent advice. Fall
            # through with an empty response; validation rejects it and the
            # deterministic fallback takes over.
            return {
                **parse.parse_advice(""),
                "provider_error": str(exc),
            }
        return parse.parse_advice(text)

    def _validate(
        self,
        advice: dict[str, Any],
        state: dict[str, Any],
        candidates: list[dict[str, Any]],
        discards: list[dict[str, Any]],
    ) -> validator.Report:
        report = validator.validate_advice(advice, state, candidates, discards)
        for message in advice.get("parse_errors") or []:
            report.fail("format.parse", message)
        if advice.get("provider_error"):
            report.fail("provider", advice["provider_error"])
        return report

    @staticmethod
    def _fallback(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Spec 5d: the top-ranked candidate, with no prose reasoning."""
        if not candidates:
            return {
                "decision": "No advice available.",
                "reasoning": "",
                "alternatives": "",
                "uncertain": (
                    "Advisory generation failed validation and no candidate plays "
                    "were computed, so there is nothing to fall back to."
                ),
                "action": {"kind": "none"},
                "raw": "",
            }
        top = candidates[0]
        floor = "" if top.get("exact", True) else (
            " This is a FLOOR, not a score - "
            + ", ".join(top.get("unmodelled") or [])
            + " could not be modelled."
        )
        return {
            "decision": (
                f"Play cards {top['cards']} "
                f"({top['hand_type'].replace('_', ' ')}, {top['score']})."
            ),
            "reasoning": "",
            "alternatives": "",
            "uncertain": (
                "Advisory generation failed validation twice. This is the "
                "top-ranked computed play with no strategic reasoning behind it."
                + floor
            ),
            "action": {"kind": "play", "cards": top["cards"]},
            "raw": "",
        }

    # ----------------------------------------------------------------------

    def _render_beginner(
        self,
        state: dict[str, Any],
        expert: dict[str, Any],
        result: AdviceResult,
    ) -> None:
        """Stage 2. Rewrites the explanation; cannot touch the decision.

        ``result.decision`` is never reassigned here. That is what makes spec
        5a's constraint structural rather than merely instructed - there is no
        code path by which the rendering stage can change the recommendation.
        """
        key = self.cache.key(
            state, stage="render", mode="beginner", extra=expert.get("raw", "")
        )
        cached = self.cache.get(key, "render")

        if cached is None:
            user = prompts.build_render_user(state, expert, self.glossary.glossed)
            try:
                cached = self.provider.complete(prompts.BEGINNER_RENDER_SYSTEM, user)
            except ProviderError:
                # Keep the expert explanation rather than showing nothing. The
                # decision is identical either way, which is the property that
                # matters.
                result.uncertain = (
                    (result.uncertain + " ").strip()
                    + " Plain-language rendering was unavailable, so the expert "
                    "explanation is shown instead."
                ).strip()
                return
            self.cache.put(key, "render", cached)

        rendered = parse.parse_render(cached, result.decision)
        if not rendered["reasoning"]:
            return  # unusable render; keep the expert prose

        result.reasoning = rendered["reasoning"]
        result.alternatives = rendered["alternatives"]
        result.uncertain = rendered["uncertain"]
        result.glossed_this_turn = self.glossary.note_used(
            " ".join([rendered["reasoning"], rendered["alternatives"]])
        )


def advise(
    state: dict[str, Any],
    *,
    mode: str = "expert",
    question: str | None = None,
    force_stub: bool = False,
    no_cache: bool = False,
) -> AdviceResult:
    """Convenience entry point for one-shot use."""
    return Advisor(
        cache=ResponseCache(enabled=not no_cache),
        force_stub=force_stub,
    ).advise(state, mode=mode, question=question)
