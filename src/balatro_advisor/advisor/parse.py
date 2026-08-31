"""Parse the advisor's labelled output into a structured advice dict.

The structured ``action`` block is the load-bearing part. The validator checks
legality against machine-readable intent - ``{"kind": "play", "cards": [0, 1]}``
- rather than trying to recover intent from prose, because a legality check
built on regexing English is a legality check that fails quietly.

Advice whose action block is missing or malformed does not get a lenient
reading. It is reported as unparseable and fails validation.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["parse_advice", "parse_render", "ACTION_KINDS"]

ACTION_KINDS = frozenset(
    {"play", "discard", "buy", "sell", "reroll", "pick", "skip", "none"}
)

_SECTIONS = ("decision", "reasoning", "alternatives", "uncertain")
_LABEL = re.compile(
    r"^\s*(DECISION|REASONING|ALTERNATIVES|UNCERTAIN)\s*:\s*(.*)$",
    re.IGNORECASE,
)
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_OBJECT = re.compile(r"(\{[^{}]*\"kind\"[^{}]*\})", re.DOTALL)


def _sections(text: str) -> dict[str, str]:
    """Split labelled sections, tolerating multi-line bodies.

    A section runs until the next label or the start of a fenced block, so
    prose that happens to contain a colon does not truncate it.
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            current = None
            continue
        match = _LABEL.match(line)
        if match:
            current = match.group(1).lower()
            found[current] = [match.group(2).strip()]
        elif current:
            found[current].append(line.strip())
    return {
        key: "\n".join(part for part in parts if part).strip()
        for key, parts in found.items()
    }


def _action(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Extract the action block. Returns (action, error)."""
    blocks = _FENCE.findall(text) or _BARE_OBJECT.findall(text)
    if not blocks:
        return None, "no action block found in the response"

    # Last one wins: if the model restated the block, the final form is its
    # settled answer.
    raw = blocks[-1]
    try:
        action = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"action block is not valid JSON ({exc.msg})"
    if not isinstance(action, dict):
        return None, "action block is not a JSON object"

    kind = action.get("kind")
    if kind not in ACTION_KINDS:
        return None, (
            f"action kind {kind!r} is not one of {sorted(ACTION_KINDS)}"
        )

    for field in ("cards", "slots", "picks", "sell"):
        if field in action:
            value = action[field]
            if not isinstance(value, list) or not all(
                isinstance(v, int) and not isinstance(v, bool) for v in value
            ):
                return None, f"action field {field!r} must be a list of integers"

    if kind in ("play", "discard") and not action.get("cards"):
        return None, f"a {kind!r} action must name the cards"
    if kind == "pick" and not action.get("picks"):
        return None, "a 'pick' action must name the picks"

    return action, None


def parse_advice(text: str) -> dict[str, Any]:
    """Parse a stage-1 advisor response.

    Always returns a dict. ``parse_errors`` is non-empty when something was
    wrong; the caller must treat that as a validation failure rather than
    proceeding with a partial read.
    """
    sections = _sections(text or "")
    action, action_error = _action(text or "")

    advice: dict[str, Any] = {key: sections.get(key, "") for key in _SECTIONS}
    advice["action"] = action or {"kind": "none"}
    advice["raw"] = text

    errors = []
    if not advice["decision"]:
        errors.append("no DECISION line in the response")
    if action_error:
        errors.append(action_error)
    advice["parse_errors"] = errors

    # "none" is how the prompt asks for an empty UNCERTAIN; normalizing it here
    # means the validator's "did it acknowledge the gap" checks do not have to
    # treat the literal word as an acknowledgement.
    if advice["uncertain"].strip().lower() in ("none", "none.", "n/a", "-"):
        advice["uncertain"] = ""

    return advice


def parse_render(text: str, decision: str) -> dict[str, Any]:
    """Parse a stage-2 beginner response and re-attach the fixed decision.

    ``decision`` is copied in verbatim rather than read from ``text``. This is
    what makes spec 5a's constraint structural: the rendering stage has no
    channel through which it can change the recommended action.
    """
    sections = _sections(text or "")
    return {
        "decision": decision,
        "reasoning": sections.get("reasoning", ""),
        "alternatives": sections.get("alternatives", ""),
        "uncertain": (
            "" if sections.get("uncertain", "").strip().lower() in ("none", "none.", "n/a", "-")
            else sections.get("uncertain", "")
        ),
        "raw": text,
    }
