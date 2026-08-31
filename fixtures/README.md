# Replay fixtures

Spec step 3: fixtures come **before** the scorer, so the scorer has ground
truth to be validated against from the first commit. Writing the scorer first
and testing it later means testing it against your own assumptions rather than
against the game.

## Provenance is a first-class field, and right now it is the weak spot

Every fixture carries `provenance`:

| value | meaning |
|---|---|
| `captured` | The state and the score were read out of a running game. This is what the spec asks for and the only kind that is genuinely independent ground truth. |
| `hand_computed` | The arithmetic was worked out by hand from the rules and the data tables, then checked against the scorer. Catches regressions and internal inconsistency. Does **not** catch a rule this project has wrong in the same way in both places. |

**Every fixture in this directory is currently `hand_computed`.** No captured
fixture exists yet, because capturing one needs a running modded Balatro, which
is the blocking prerequisite in spec section 0. This is a real gap, not a
formality: `hand_computed` fixtures prove the scorer is self-consistent and
prove it does not regress, and they cannot prove it matches the game.

## Filling the gap

Spec section 8: the mod emits these automatically. On every hand played it
writes the pre-play state and the resolved score as a fixture file, so playing
normally for an hour generates a full regression suite at no effort. Those
arrive with `provenance: "captured"` and take precedence wherever they disagree
with a hand-computed fixture — the game is the authority, this repo is not.

Twenty captured fixtures is the bar the spec sets for a trustworthy scorer.

## Format

```json
{
  "name": "ante5_house_two_pair_kings_jacks",
  "provenance": "hand_computed",
  "arithmetic": "Human-readable derivation. The point of this field is that a reader can check the number without running the code.",
  "state_before": { },
  "cards_played": [0, 1, 3, 4],
  "expected_chips": 230,
  "expected_mult": 90,
  "expected_score": 20700
}
```

`cards_played` and the `expected_*` fields are `null` for shop and pack-open
fixtures, which exercise ingest, validation and the advisor rather than the
scorer.

Optional fields:

- `expected_exact` — assert the scorer's honesty flag. A fixture holding an
  unmodellable joker sets this `false`; that path matters as much as the
  arithmetic.
- `expected_unmodelled` — the keys expected to be reported as unmodelled.
- `counterintuitive` — a note explaining why the correct answer is not the
  obvious one. The spec requires at least one such fixture, since those are the
  cases where a plausible-but-wrong implementation still passes everything else.
