"""Model providers.

Two of them, behind one interface:

``AnthropicProvider``
    The real thing. Claude Opus 5 via the official SDK.

``StubProvider``
    Deterministic, offline, no credential. It answers with the top-ranked
    candidate and nothing more. It exists so the rest of the pipeline - the
    parser, the validator, the cache, the CLI, the whole test suite - can be
    exercised without a network call or an API key, and so a missing credential
    degrades to "here is the arithmetic, ungarnished" rather than a crash.
"""

from __future__ import annotations

import importlib.util
import json
import os
from typing import Any, Protocol

__all__ = [
    "Provider",
    "AnthropicProvider",
    "StubProvider",
    "ProviderError",
    "default_provider",
    "MODEL",
]

MODEL = "claude-opus-5"


class ProviderError(RuntimeError):
    """The model could not be reached, or refused. Recoverable by the caller."""


class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> str: ...


# --------------------------------------------------------------------------


class AnthropicProvider:
    """Claude via the Messages API."""

    name = "anthropic"

    def __init__(self, model: str = MODEL, client: Any = None) -> None:
        self.model = model
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            # Zero-arg construction on purpose: the SDK resolves
            # ANTHROPIC_API_KEY, then ANTHROPIC_AUTH_TOKEN, then an
            # `ant auth login` profile. Passing a key we scraped from the
            # environment ourselves would defeat the profile path.
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> str:
        import anthropic

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"model {self.model!r} not available: {exc}") from exc
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                "no valid Anthropic credential. Run `ant auth login`, or set "
                "ANTHROPIC_API_KEY, or pass --stub to run without a model."
            ) from exc
        except anthropic.RateLimitError as exc:
            retry = exc.response.headers.get("retry-after", "60")
            raise ProviderError(f"rate limited; retry after {retry}s") from exc
        except anthropic.APIStatusError as exc:
            kind = "server error" if exc.status_code >= 500 else "API error"
            raise ProviderError(f"{kind} ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"could not reach the API: {exc}") from exc

        if message.stop_reason == "refusal":
            raise ProviderError("the model declined to answer this request")

        return "".join(
            block.text for block in message.content if block.type == "text"
        )


# --------------------------------------------------------------------------


class StubProvider:
    """Offline provider. Returns the deterministic top-ranked candidate.

    It reads the candidate list back out of the user message rather than being
    handed it separately, so it exercises the same prompt assembly the real
    provider sees. Its answer is intentionally bare: the arithmetic is real,
    the judgment is absent, and it says so.
    """

    name = "stub"

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> str:  # noqa: ARG002
        if "explain it, do not change it" in user or "THE DECISION (fixed" in user:
            return self._render(user)
        return self._decide(user)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _extract_candidates(user: str) -> list[dict[str, Any]]:
        marker = "CANDIDATE PLAYS"
        start = user.find(marker)
        if start == -1:
            return []
        bracket = user.find("[", start)
        if bracket == -1:
            return []
        depth, end = 0, None
        for i in range(bracket, len(user)):
            if user[i] == "[":
                depth += 1
            elif user[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            return []
        try:
            return json.loads(user[bracket:end])
        except json.JSONDecodeError:
            return []

    def _decide(self, user: str) -> str:
        candidates = self._extract_candidates(user)
        if not candidates:
            return (
                "DECISION: No recommendation - no candidate plays were computed.\n"
                "REASONING: This is the offline stub provider. It only ever "
                "reports the top-ranked computed candidate, and there is none "
                "for this state.\n"
                "ALTERNATIVES: none\n"
                "UNCERTAIN: All of it. No model was consulted.\n"
                "```json\n{\"kind\": \"none\"}\n```"
            )

        top = candidates[0]
        cards = top["cards"]
        hand = top["hand_type"].replace("_", " ")
        floor = "" if top.get("exact", True) else " (a FLOOR, not a score - "
        if not top.get("exact", True):
            floor += "unmodelled: " + ", ".join(top.get("unmodelled") or []) + ")"

        clears = top.get("clears_blind")
        clears_text = {
            True: " This clears the blind.",
            False: " This does not clear the blind.",
            None: " Whether this clears the blind is not determinable.",
        }[clears]

        uncertain = (
            "No model was consulted - this is the deterministic fallback, so "
            "there is no strategic judgment here at all, only the top-ranked "
            "arithmetic."
        )
        if not top.get("exact", True):
            uncertain += (
                " The score is a floor: "
                + ", ".join(top.get("unmodelled") or [])
                + " could not be modelled."
            )

        return (
            f"DECISION: Play cards {cards} ({hand}).\n"
            f"REASONING: It is the top-ranked candidate at {top['score']}"
            f"{floor}, from {top['chips']} chips x {top['mult']} mult."
            f"{clears_text}\n"
            f"ALTERNATIVES: {len(candidates) - 1} other plays were enumerated and "
            f"scored lower.\n"
            f"UNCERTAIN: {uncertain}\n"
            f"```json\n{json.dumps({'kind': 'play', 'cards': cards})}\n```"
        )

    @staticmethod
    def _render(user: str) -> str:
        return (
            "REASONING: This is the offline stub, so the plain-language "
            "explanation is not available. The recommended action and its "
            "numbers are unchanged from the expert output above.\n"
            "ALTERNATIVES: none worth noting\n"
            "UNCERTAIN: No model was consulted."
        )


# --------------------------------------------------------------------------


def default_provider(*, force_stub: bool = False) -> Provider:
    """Pick a provider.

    An unset ``ANTHROPIC_API_KEY`` does not mean there is no credential - the
    SDK also resolves ``ANTHROPIC_AUTH_TOKEN`` and an ``ant auth login``
    profile - so this only falls back to the stub when the SDK is absent
    entirely. A credential that turns out to be missing surfaces as a
    ProviderError at call time, with instructions.
    """
    if force_stub or os.environ.get("BALATRO_ADVISOR_STUB"):
        return StubProvider()
    if importlib.util.find_spec("anthropic") is None:
        return StubProvider()
    return AnthropicProvider()
