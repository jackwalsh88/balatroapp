from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures"


def fixture_files() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


@pytest.fixture
def state_factory():
    """Build a minimal valid canonical state, overridable per test."""

    def build(**overrides: Any) -> dict[str, Any]:
        state: dict[str, Any] = {
            "schema_version": 1,
            "seq": 1,
            "source": "manual",
            "captured_at": None,
            "phase": "playing",
            "run": {"ante": 1, "money": 10},
            "resources": {"hands_remaining": 3, "discards_remaining": 2, "hand_size": 8},
            "jokers": [],
            "consumables": [],
            "hand_levels": {},
            "current_hand": [
                {"rank": "K", "suit": "hearts"},
                {"rank": "K", "suit": "diamonds"},
                {"rank": "4", "suit": "clubs"},
            ],
            "blind": {"type": "small", "key": "bl_small", "requirement": 50},
        }
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                state[key] = {**state[key], **value}
            else:
                state[key] = value
        return state

    return build


@pytest.fixture
def card():
    def build(rank: str | None, suit: str | None, **kw: Any) -> dict[str, Any]:
        return {"rank": rank, "suit": suit, **kw}

    return build
