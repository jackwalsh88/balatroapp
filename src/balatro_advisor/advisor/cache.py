"""Disk-backed response cache.

Spec section 5b: build this early, it pays for itself during development. Shop
states repeat constantly - a reroll that changes one irrelevant item, a
screenshot retaken because the first was blurry, a fixture re-run for the
fiftieth time while debugging the scorer. Every one of those is otherwise a
paid API call taking several seconds.

Key construction is the whole design:

  excluded   seq, captured_at, source - none of them change the advice
  included   everything else, plus PROMPT_VERSION, plus the stage and mode
  normalized keys sorted before hashing, so serialization order cannot cause a
             miss on identical state
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .prompts import PROMPT_VERSION

__all__ = ["ResponseCache", "state_hash", "EXCLUDED_FIELDS"]

# Fields that legitimately differ between two states deserving identical advice.
EXCLUDED_FIELDS = ("seq", "captured_at", "source")


def _canonical(state: dict[str, Any]) -> str:
    trimmed = {k: v for k, v in state.items() if k not in EXCLUDED_FIELDS}
    return json.dumps(trimmed, sort_keys=True, separators=(",", ":"), default=str)


def state_hash(state: dict[str, Any]) -> str:
    """Content hash of the advice-relevant part of a state.

    Also used by the decision log, so a log entry can be matched back to the
    cache entry that produced it.
    """
    return hashlib.sha256(_canonical(state).encode()).hexdigest()


class ResponseCache:
    def __init__(self, root: str | Path = "cache", enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled

    def key(
        self,
        state: dict[str, Any],
        stage: str,
        mode: str,
        extra: Any = None,
    ) -> str:
        payload = json.dumps(
            {
                "state": _canonical(state),
                "stage": stage,
                # Included even though the two stages are stored under separate
                # directories: spec 5b requires expert and beginner outputs not
                # to collide, and belt-and-braces here costs nothing.
                "mode": mode,
                "prompt_version": PROMPT_VERSION,
                "extra": extra,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _path(self, stage: str, key: str) -> Path:
        return self.root / stage / f"{key}.json"

    def get(self, key: str, stage: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(stage, key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())["value"]
        except (json.JSONDecodeError, KeyError, OSError):
            # A corrupt entry is a cache miss, never an error. Losing a cached
            # response costs one call; crashing on it costs the session.
            return None

    def put(self, key: str, stage: str, value: Any) -> None:
        if not self.enabled:
            return
        path = self._path(stage, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "prompt_version": PROMPT_VERSION,
            "stored_at": time.time(),
            "value": value,
        }
        # Write-then-rename: a reader must never see a partial file. Same
        # discipline the spec asks of the Lua mod in section 3.1.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, default=str))
        tmp.replace(path)

    def clear(self) -> int:
        """Drop every entry. Returns how many were removed."""
        if not self.root.exists():
            return 0
        removed = 0
        for path in self.root.rglob("*.json"):
            path.unlink()
            removed += 1
        return removed
