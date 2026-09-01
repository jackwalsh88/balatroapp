"""Advisor layer: prompts, model providers, parsing, cache, log, pipeline.

The advisor never computes a score. It receives computed scores as input and
reasons about them (spec section 1).
"""

from __future__ import annotations

from .cache import ResponseCache, state_hash
from .client import (
    AnthropicProvider,
    OfflineProvider,
    OpenModelProvider,
    ProviderError,
    StubProvider,
    available_providers,
    default_provider,
)
from .decision_log import DecisionLog
from .glossary import Glossary
from .parse import parse_advice, parse_render
from .prompts import PROMPT_VERSION
from .run import MODES, AdviceResult, Advisor, advise

__all__ = [
    "Advisor",
    "AdviceResult",
    "advise",
    "MODES",
    "ResponseCache",
    "state_hash",
    "DecisionLog",
    "Glossary",
    "AnthropicProvider",
    "OpenModelProvider",
    "OfflineProvider",
    "StubProvider",
    "available_providers",
    "ProviderError",
    "default_provider",
    "parse_advice",
    "parse_render",
    "PROMPT_VERSION",
]
