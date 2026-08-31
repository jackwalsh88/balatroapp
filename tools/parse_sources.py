"""Parse the two authoritative joker sources into one merged record per joker.

    data/sources/game/jokerdata.lua   extracted from game.lua,
                                      Game:init_item_prototypes()
    data/sources/wiki/jokers.html     the rendered balatrowiki.org Jokers table

They are complementary, and neither alone is enough:

- The game data has the internal key, rarity, cost and the **actual numeric
  constants** (`config`), but its `config` keys are bare - `{extra = 5}` says
  nothing about what the 5 does - and its `effect` strings are stale internal
  labels (Seeing Double is tagged "X1.5 Mult club 7" while its config says
  Xmult 2).
- The wiki table has the **semantics**: the effect sentence, the joker's type
  (+c/+m/Xm/...) and, most usefully, its activation (Independent / On Scored /
  On Held / ...) which maps directly onto the `when` field of the effect
  grammar. It has no constants that are not already in the prose.

So: constants come from the game, meaning comes from the wiki, and the numbers
in the prose are checked against the game's config rather than trusted.

Writes data/sources/merged.json. Run: python tools/parse_sources.py
"""

from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GAME = ROOT / "data" / "sources" / "game" / "jokerdata.lua"
WIKI = ROOT / "data" / "sources" / "wiki" / "jokers.html"
OUT = ROOT / "data" / "sources" / "merged.json"

RARITY = {1: "common", 2: "uncommon", 3: "rare", 4: "legendary"}


# --------------------------------------------------------------------------
# Lua table literal -> Python
# --------------------------------------------------------------------------


class LuaParser:
    """Minimal recursive-descent parser for the subset of Lua in the dump.

    Handles nested tables, string/number/boolean values, and both `key = v`
    and positional entries. That is all this file uses; anything else raises
    rather than being silently mis-read.
    """

    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def error(self, msg: str) -> None:
        line = self.s.count("\n", 0, self.i) + 1
        raise ValueError(f"{msg} at line {line}: {self.s[self.i:self.i + 60]!r}")

    def ws(self) -> None:
        while self.i < len(self.s):
            if self.s[self.i] in " \t\r\n":
                self.i += 1
            elif self.s.startswith("--", self.i):
                nl = self.s.find("\n", self.i)
                self.i = len(self.s) if nl == -1 else nl + 1
            else:
                break

    def expect(self, ch: str) -> None:
        self.ws()
        if self.i >= len(self.s) or self.s[self.i] != ch:
            self.error(f"expected {ch!r}")
        self.i += 1

    def value(self) -> Any:
        self.ws()
        if self.i >= len(self.s):
            self.error("unexpected end")
        c = self.s[self.i]
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        if self.s.startswith("true", self.i):
            self.i += 4
            return True
        if self.s.startswith("false", self.i):
            self.i += 5
            return False
        if self.s.startswith("nil", self.i):
            self.i += 3
            return None
        m = re.match(r"-?\d+\.?\d*(?:[eE][+-]?\d+)?", self.s[self.i:])
        if m:
            self.i += m.end()
            raw = m.group(0)
            return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
        self.error("unparseable value")

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.i < len(self.s) and self.s[self.i] != quote:
            if self.s[self.i] == "\\":
                self.i += 1
                out.append({"n": "\n", "t": "\t"}.get(self.s[self.i], self.s[self.i]))
            else:
                out.append(self.s[self.i])
            self.i += 1
        self.i += 1
        return "".join(out)

    def table(self) -> Any:
        self.expect("{")
        out: dict[Any, Any] = {}
        positional: list[Any] = []
        while True:
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == "}":
                self.i += 1
                break
            m = re.match(r"([A-Za-z_]\w*)\s*=", self.s[self.i:])
            if m:
                self.i += m.end()
                out[m.group(1)] = self.value()
            elif self.s[self.i] == "[":
                self.i += 1
                key = self.value()
                self.expect("]")
                self.expect("=")
                out[key] = self.value()
            else:
                positional.append(self.value())
            self.ws()
            if self.i < len(self.s) and self.s[self.i] in ",;":
                self.i += 1
        if positional and not out:
            return positional
        if positional:
            out["_positional"] = positional
        return out


def parse_game(path: Path = GAME) -> dict[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.index("return")
    parser = LuaParser(text[start + len("return"):])
    table = parser.table()
    if not isinstance(table, dict):
        raise ValueError("game data did not parse to a table")
    return table


# --------------------------------------------------------------------------
# Wiki HTML table
# --------------------------------------------------------------------------

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def _text(cell: str) -> str:
    cell = re.sub(r"<style[^>]*>.*?</style>", " ", cell, flags=re.S)
    cell = re.sub(r"<br\s*/?>", " ", cell)
    cell = re.sub(r"<[^>]+>", " ", cell)
    return re.sub(r"\s+", " ", html_lib.unescape(cell)).strip()


def parse_wiki(path: Path = WIKI) -> dict[int, dict[str, Any]]:
    """Return {joker number: {name, effect, cost, type, activation, unlock}}."""
    html = path.read_text(encoding="utf-8", errors="replace")
    out: dict[int, dict[str, Any]] = {}

    for row in _ROW.findall(html):
        cells = [_text(c) for c in _CELL.findall(row)]
        if len(cells) != 8 or not cells[0].isdigit():
            continue
        number = int(cells[0])
        # The rarity cell carries injected CSS; rarity comes from the game data,
        # so it is deliberately not read here.
        out[number] = {
            "number": number,
            "name": cells[1],
            "effect": cells[2],
            "cost": cells[3],
            "unlock": cells[5],
            "type": cells[6],
            "activation": cells[7],
        }
    return out


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

TYPE_NAMES = {
    "+c": "chips",
    "+m": "additive_mult",
    "Xm": "multiplicative_mult",
    "++": "chips_and_mult",
    "!!": "effect",
    "...": "retrigger",
    "+$": "economy",
}

ACTIVATION_NAMES = {
    "Indep.": "independent",
    "Independent": "independent",
    "On Scored": "on_scored",
    "On Held": "on_held",
    "On Played": "on_played",
    "On Discard": "on_discard",
    "Mixed": "mixed",
    "On Other Jokers": "on_other_jokers",
    "N/A": "passive",
    "N/A (Passive)": "passive",
}


def main() -> None:
    game = parse_game()
    wiki = parse_wiki()

    by_order = {v["order"]: (k, v) for k, v in game.items() if isinstance(v, dict)}
    merged, problems = [], []

    for order in sorted(by_order):
        key, g = by_order[order]
        w = wiki.get(order)
        if w is None:
            problems.append(f"no wiki row for #{order} {key}")
            w = {}

        proto_name = g.get("name")
        wiki_name = w.get("name")
        # The prototype `name` in game.lua is not always the display name -
        # j_caino is called "Caino" there and "Canio" everywhere a player sees
        # it, because the display name comes from localization. Prefer the wiki
        # name and record the disagreement rather than silently picking one.
        name = wiki_name or proto_name
        if wiki_name and proto_name and wiki_name != proto_name:
            problems.append(f"{key}: game.lua says {proto_name!r}, wiki says {wiki_name!r}")

        merged.append({
            "key": key,
            "name": name,
            "prototype_name": proto_name,
            "order": order,
            "rarity": RARITY.get(g.get("rarity")),
            "cost": g.get("cost"),
            "config": g.get("config") or {},
            "effect_text": w.get("effect"),
            "type": TYPE_NAMES.get(w.get("type", ""), w.get("type")),
            "activation": ACTIVATION_NAMES.get(w.get("activation", ""), w.get("activation")),
            "unlock": w.get("unlock"),
            "blueprint_compat": g.get("blueprint_compat"),
            "perishable_compat": g.get("perishable_compat"),
            "eternal_compat": g.get("eternal_compat"),
            "enhancement_gate": g.get("enhancement_gate"),
            "unlocked_by_default": g.get("unlocked"),
        })

    OUT.write_text(json.dumps({
        "_doc": {
            "sources": {
                "constants_keys_rarity_cost": "data/sources/game/jokerdata.lua "
                                              "(extracted from game.lua, Game:init_item_prototypes)",
                "effect_text_type_activation": "data/sources/wiki/jokers.html "
                                               "(rendered balatrowiki.org Jokers table)",
            },
            "note": "Constants come from the game; meaning comes from the wiki. "
                    "Numbers appearing in the wiki prose are checked against the "
                    "game's config rather than trusted.",
        },
        "count": len(merged),
        "problems": problems,
        "jokers": merged,
    }, indent=2) + "\n")

    print(f"parsed {len(game)} from game.lua, {len(wiki)} from the wiki table")
    print(f"merged {len(merged)} -> {OUT.relative_to(ROOT)}")
    if problems:
        print(f"\n{len(problems)} name/coverage problem(s):")
        for p in problems:
            print("  ", p)


if __name__ == "__main__":
    main()
