"""Decision log.

Spec section 5c: this is the only mechanism that distinguishes a wrong
recommendation from a wrong calculation after the fact, and both occurred
during manual testing.

    predicted_score == actual_score, run still fails  ->  the scorer is fine,
                                                          the JUDGMENT needs work
    predicted_score != actual_score                   ->  the SCORER is wrong and
                                                          the advice was built on
                                                          bad arithmetic

Different bugs, different fixes, and without logging they are
indistinguishable. After fifty logged rounds you have a dataset showing where
the advisor is actually wrong, rather than where it feels wrong.

Stored as JSON Lines so appending is atomic per record and a partially written
tail costs one entry rather than the file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["DecisionLog", "ACTION_TAKEN"]

ACTION_TAKEN = ("played_recommended", "played_other", "ignored")


class DecisionLog:
    def __init__(self, path: str | Path = "logs/decisions.jsonl") -> None:
        self.path = Path(path)

    def record(
        self,
        *,
        state: dict[str, Any],
        state_hash: str,
        mode: str,
        advice: dict[str, Any],
        candidates: list[dict[str, Any]],
        cache_hit: bool,
        validation: list[str] | None = None,
        provider: str | None = None,
    ) -> str:
        """Append one interaction. Returns its entry id."""
        entry_id = f"{state.get('seq', 0)}-{state_hash[:12]}"
        record = {
            "id": entry_id,
            "seq": state.get("seq"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "provider": provider,
            "state_hash": state_hash,
            "state": state,
            "candidate_plays": candidates,
            "advice": {
                "decision": advice.get("decision"),
                "reasoning": advice.get("reasoning"),
                "alternatives": advice.get("alternatives"),
                "uncertain": [advice["uncertain"]] if advice.get("uncertain") else [],
                "action": advice.get("action"),
            },
            "validation_findings": validation or [],
            "cache_hit": cache_hit,
            "outcome": None,
        }
        self._append(record)
        return entry_id

    def record_outcome(
        self,
        entry_id: str,
        *,
        action_taken: str,
        actual_score: int | None = None,
        cleared_blind: bool | None = None,
        ante_survived: bool | None = None,
        run_ended_at_ante: int | None = None,
    ) -> bool:
        """Fill in the outcome of a previously logged decision.

        The mod adapter can do this automatically - it observes the hand played
        and the score awarded. In manual and screenshot modes it is prompted
        for, or left null.

        Rewrites the file, which is fine at this scale and keeps the format a
        plain readable JSONL rather than an append-only journal needing
        compaction.
        """
        if action_taken not in ACTION_TAKEN:
            raise ValueError(f"action_taken must be one of {ACTION_TAKEN}")
        if not self.path.exists():
            return False

        entries = list(self.entries())
        found = False
        for entry in entries:
            if entry.get("id") != entry_id:
                continue
            entry["outcome"] = {
                "action_taken": action_taken,
                "actual_score": actual_score,
                "predicted_score": _predicted_score(entry),
                "cleared_blind": cleared_blind,
                "ante_survived": ante_survived,
                "run_ended_at_ante": run_ended_at_ante,
            }
            found = True

        if found:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(e, default=str) + "\n" for e in entries))
            tmp.replace(self.path)
        return found

    def entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn tail costs one entry, not the file

    def divergences(self) -> list[dict[str, Any]]:
        """Entries where predicted and actual score disagree.

        These are the scorer bugs. Everything else that went wrong is judgment.
        """
        out = []
        for entry in self.entries():
            outcome = entry.get("outcome") or {}
            predicted, actual = outcome.get("predicted_score"), outcome.get("actual_score")
            if predicted is None or actual is None or predicted == actual:
                continue
            out.append({
                "id": entry["id"],
                "predicted_score": predicted,
                "actual_score": actual,
                "delta": actual - predicted,
                "error_pct": round(abs(actual - predicted) / actual * 100, 1) if actual else None,
                "decision": (entry.get("advice") or {}).get("decision"),
            })
        return out

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _predicted_score(entry: dict[str, Any]) -> int | None:
    """The score of the play the advice actually recommended."""
    action = (entry.get("advice") or {}).get("action") or {}
    if action.get("kind") != "play":
        return None
    cards = sorted(action.get("cards") or [])
    for candidate in entry.get("candidate_plays") or []:
        if sorted(candidate["cards"]) == cards:
            return candidate["score"]
    return None
