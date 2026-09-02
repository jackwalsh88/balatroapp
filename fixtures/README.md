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

**Every fixture in this directory is currently `hand_computed`.** This is a
real gap, not a formality: they prove the scorer is self-consistent and prove
it does not regress, and they cannot prove it matches the game — both could be
wrong the same way.

Capturing one no longer needs the modded install that spec section 0 blocks.
It needs a hand played anywhere you have Balatro, typed in and recorded.

## Filling the gap

`balatro-advisor capture` turns a hand you actually played into ground truth:

```bash
balatro-advisor advise                    # type the hand, note the log-id
# ...play it, read the score off the game...
balatro-advisor capture <log-id> --score 20700 --recommended
```

Optionally add `--chips` and `--mult` if you can read them off the scoring
animation. They are not required — the score alone catches an error, and the
intermediates only narrow down where it is.

**A captured fixture that disagrees makes the suite fail, and that failure is
correct.** It means a real scoring bug has been found. The runner marks it
`FAIL*` and says so, because the instinct on a red test is to edit the fixture,
and here the fixture is the one thing that is right.

What is recorded, and why:

| Field | Meaning |
|---|---|
| `expected_score` | What the **game** awarded. The only authority. |
| `expected_chips` / `expected_mult` | What the game displayed, if read off. Optional. |
| `scorer_said` | What we predicted at capture time. A **record**, never an expectation — so a later fix can be seen to have changed the disagreement. |
| `agrees_at_capture` | Whether they matched when captured. |

The trap this design avoids: recording the scorer's own answer as the
expectation produces a fixture that can never fail, and therefore never tells
you anything. There is a test asserting captured fixtures never do that.

Spec section 8: once the mod exists it emits these automatically on every hand
played, so an hour of normal play generates a full regression suite at no
effort. Until then, `capture` does the same job one hand at a time.

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
