"""Model providers, in three tiers.

``OfflineProvider``      free, no network, no account
``OpenModelProvider``    a cheap open-weights model over an OpenAI-compatible API
``AnthropicProvider``    a frontier model, Claude Opus 5, via the official SDK

The tiering is only safe because of how the rest of the system is built. A
cheap model is *more* likely to invent a number than a frontier one - and the
architecture already assumes the model will try. Scores come from the
deterministic scorer, never from the model; the validator rejects any number in
the prose that has no computed source; and advice that fails twice degrades to
the top-ranked play with no prose at all.

So the weak tier cannot produce a wrong score. It can only produce weaker
*judgment*, which is exactly the thing the decision log measures. That is what
makes running this on a 3B model a reasonable thing to do rather than a
liability.

Two SDK rules kept deliberately separate:

- Claude is called through the official ``anthropic`` SDK, never through an
  OpenAI-compatible shim.
- The open tier is a genuinely different provider, so it speaks the
  OpenAI-compatible protocol its servers actually implement, over stdlib
  urllib - no extra dependency, which matters on the old Python this has to run
  on.
"""

from __future__ import annotations

import importlib.util
import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

__all__ = [
    "Provider",
    "AnthropicProvider",
    "OpenModelProvider",
    "OfflineProvider",
    "StubProvider",
    "ProviderError",
    "default_provider",
    "available_providers",
    "TIERS",
    "MODEL",
    "OPEN_MODEL_DEFAULT_BASE_URL",
    "OPEN_MODEL_DEFAULT_MODEL",
]

MODEL = "claude-opus-5"

# Ollama's OpenAI-compatible endpoint. It is the one option that needs no
# account at all - the model runs on your own machine - so it is the default
# for the open tier. Any other OpenAI-compatible service works by pointing
# BALATRO_ADVISOR_BASE_URL at it.
OPEN_MODEL_DEFAULT_BASE_URL = "http://localhost:11434/v1"
OPEN_MODEL_DEFAULT_MODEL = "llama3.2"

TIERS = ("auto", "anthropic", "open", "offline")


class ProviderError(RuntimeError):
    """The model could not be reached, or refused. Recoverable by the caller."""


class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> str: ...


# --------------------------------------------------------------------------
# Tier 3: frontier
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
                "no valid Anthropic credential. Run `ant auth login`, set "
                "ANTHROPIC_API_KEY, or use --provider open / --provider offline."
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

        return "".join(block.text for block in message.content if block.type == "text")


# --------------------------------------------------------------------------
# Tier 2: cheap open-weights model
# --------------------------------------------------------------------------


class OpenModelProvider:
    """An open-weights model behind an OpenAI-compatible chat endpoint.

    Works unchanged against a local Ollama (no account, no key), or against a
    hosted service such as OpenRouter or Groq by setting the base URL, model
    and that service's own key. Note the key is the *service's*, not
    Anthropic's - the point of this tier is that it does not need an Anthropic
    account.

    Deliberately stdlib-only: no `requests`, no `httpx`, nothing to install on
    an old interpreter.
    """

    name = "open"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("BALATRO_ADVISOR_BASE_URL")
            or OPEN_MODEL_DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = (
            model or os.environ.get("BALATRO_ADVISOR_MODEL") or OPEN_MODEL_DEFAULT_MODEL
        )
        self.api_key = api_key or os.environ.get("BALATRO_ADVISOR_API_KEY")
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"OpenModelProvider(model={self.model!r}, base_url={self.base_url!r})"

    def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            if exc.code in (401, 403):
                raise ProviderError(
                    f"{self.base_url} rejected the credential ({exc.code}). Set "
                    f"BALATRO_ADVISOR_API_KEY for that service, or use "
                    f"--provider offline."
                ) from exc
            if exc.code == 404:
                raise ProviderError(
                    f"{self.base_url} has no model {self.model!r} ({exc.code}). "
                    f"Set BALATRO_ADVISOR_MODEL to one it serves. {body}"
                ) from exc
            raise ProviderError(f"{self.base_url} returned {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"could not reach {self.base_url} ({exc.reason}). Start the "
                f"server, point BALATRO_ADVISOR_BASE_URL elsewhere, or use "
                f"--provider offline."
            ) from exc

    def reachable(self, timeout: float = 1.0) -> bool:
        """Cheap liveness probe, used only for automatic tier selection."""
        request = urllib.request.Request(f"{self.base_url}/models", method="GET")
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=timeout):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> str:
        data = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "max_tokens": max_tokens,
                # A small model follows the rigid output format far better when
                # it is not also being creative.
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            self.timeout,
        )
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"unexpected response shape from {self.base_url}: "
                f"{json.dumps(data)[:200]}"
            ) from exc


# --------------------------------------------------------------------------
# Tier 1: offline
# --------------------------------------------------------------------------


class OfflineProvider:
    """No network, no account. The deterministic top-ranked candidate.

    It reads the candidate list back out of the user message rather than being
    handed it separately, so it exercises the same prompt assembly the real
    providers see. Its answer is intentionally bare: the arithmetic is real,
    the judgment is absent, and it says so.
    """

    name = "offline"

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> str:
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
                "REASONING: This is the offline provider. It only ever reports "
                "the top-ranked computed candidate, and there is none for this "
                "state.\n"
                "ALTERNATIVES: none\n"
                "UNCERTAIN: All of it. No model was consulted.\n"
                '```json\n{"kind": "none"}\n```'
            )

        top = candidates[0]
        cards = top["cards"]
        hand = top["hand_type"].replace("_", " ")
        floor = "" if top.get("exact", True) else " (a FLOOR, not a score - "
        if not top.get("exact", True):
            floor += "unmodelled: " + ", ".join(top.get("unmodelled") or []) + ")"

        clears_text = {
            True: " This clears the blind.",
            False: " This does not clear the blind.",
            None: " Whether this clears the blind is not determinable.",
        }[top.get("clears_blind")]

        uncertain = (
            "No model was consulted - this is the offline provider, so there is "
            "no strategic judgment here at all, only the top-ranked arithmetic."
        )
        if not top.get("exact", True):
            uncertain += (
                " The score is a floor: "
                + ", ".join(top.get("unmodelled") or [])
                + " could not be modelled."
            )
        if top.get("stochastic"):
            uncertain += (
                " A random effect is in play, so the score is an expected value."
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
            "REASONING: This is the offline provider, so the plain-language "
            "explanation is not available. The recommended action and its "
            "numbers are unchanged from the expert output above.\n"
            "ALTERNATIVES: none worth noting\n"
            "UNCERTAIN: No model was consulted."
        )


# Kept so existing callers and docs referring to the stub keep working.
StubProvider = OfflineProvider


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def _has_anthropic_credential() -> bool:
    """Whether the Anthropic SDK is installed and something can authenticate it.

    An unset ANTHROPIC_API_KEY does not mean there is no credential - the SDK
    also resolves ANTHROPIC_AUTH_TOKEN and an `ant auth login` profile - so a
    stored profile counts too.
    """
    if importlib.util.find_spec("anthropic") is None:
        return False
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    for candidate in (
        os.path.expanduser("~/.config/anthropic"),
        os.path.expanduser("~/.anthropic"),
    ):
        if os.path.isdir(candidate):
            return True
    return False


def available_providers(probe: bool = True) -> list[dict[str, Any]]:
    """Report each tier and whether it is usable right now.

    Powers `balatro-advisor providers`, which answers "why did it pick that
    one" without anyone having to read this file.
    """
    open_provider = OpenModelProvider()
    rows = [
        {
            "tier": "anthropic",
            "what": f"Frontier model ({MODEL}) via the official Anthropic SDK",
            "cost": "paid, needs an Anthropic account",
            "available": _has_anthropic_credential(),
            "detail": "ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ant auth login",
        },
        {
            "tier": "open",
            "what": f"Open-weights model ({open_provider.model}) over an "
                    f"OpenAI-compatible API",
            "cost": "free on a local Ollama; a hosted service needs that "
                    "service's own key, not Anthropic's",
            "available": open_provider.reachable() if probe else None,
            "detail": open_provider.base_url,
        },
        {
            "tier": "offline",
            "what": "Deterministic top-ranked play, no prose reasoning",
            "cost": "free, no network, no account",
            "available": True,
            "detail": "always works; exact arithmetic, zero judgment",
        },
    ]
    return rows


def default_provider(
    tier: str = "auto",
    *,
    force_stub: bool = False,
) -> Provider:
    """Pick a provider.

    ``auto`` prefers the best tier that is actually usable: a frontier model if
    a credential exists, otherwise a reachable open model, otherwise offline.
    Falling back is never silent about quality - the offline provider says in
    its own output that no model was consulted.
    """
    if force_stub or os.environ.get("BALATRO_ADVISOR_STUB"):
        return OfflineProvider()

    tier = (tier or "auto").lower()
    if tier not in TIERS:
        raise ValueError(f"provider must be one of {TIERS}, got {tier!r}")

    if tier == "offline":
        return OfflineProvider()
    if tier == "anthropic":
        return AnthropicProvider()
    if tier == "open":
        return OpenModelProvider()

    # auto
    if _has_anthropic_credential():
        return AnthropicProvider()
    open_provider = OpenModelProvider()
    if os.environ.get("BALATRO_ADVISOR_BASE_URL") or open_provider.reachable():
        return open_provider
    return OfflineProvider()
