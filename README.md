# Balatro Advisor

Reads Balatro game state, computes scores **exactly**, and reasons about them.

Built to [`docs/spec.md`](docs/spec.md). The governing constraint is that
document's section 1:

> Arithmetic and judgment must be separate stages. A language model estimating
> a Balatro score produced 36,000 against an actual 20,700 — a 75% error —
> because it guessed at two joker values it could not read. The same model's
> structural advice was correct throughout.

So the pipeline splits them, and a pure-code validator stands between the model
and the player:

```
input adapter → canonical state → deterministic scorer → advisor → validator → output
   (3 modes)       one schema        pure code, no LLM      LLM      pure code
```

The advisor never computes a score. It is handed computed scores and reasons
about them.

## Quick start

```bash
pip install -e ".[dev]"

balatro-advisor fixtures                     # scorer vs. every recorded derivation
balatro-advisor explain fixtures/lower_hand_outscores_higher.json --chain
balatro-advisor advise  fixtures/blue_joker_and_photograph.json --explain --stub
balatro-advisor advise                       # manual entry, no state file needed
```

Nothing above needs an API key. `--stub` forces the offline provider, and the
advisor falls back to it automatically when no model is reachable — a missing
credential degrades to "here is the arithmetic, unadorned", never to a guess.

For real advice, the SDK resolves `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
or an `ant auth login` profile. Model: `claude-opus-5`.

## What the scorer actually does

`balatro-advisor explain --chain` prints the multiplication chain, which is the
fastest way to see whether it is right:

```
two_pair (level 6) base: 120 x 7
card KH: +10 chips -> 130 x 7
Photograph on KH: x2 mult -> 130 x 14
card KD: +10 chips -> 140 x 14
card JS: +10 chips -> 150 x 14
card JH: +10 chips -> 160 x 14
Blue Joker: +70 chips -> 230 x 14
= floor(230 x 14) = 3220
```

It models hand levels, per-card chips, enhancements, editions, seals and
retriggers, held-in-hand effects, left-to-right joker evaluation, and the boss
blinds that alter scoring (debuffed suits, debuffed faces, halved base, level
down).

### The honesty flag is the important part

All **150** vanilla jokers are in the table by name and rarity, so a joker is
never an unrecognised key. **77** have modelled effects; the remaining 67 are
awaiting effect data from `balatrowiki.org` (see
[`data/sources/wiki/README.md`](data/sources/wiki/README.md)). Anything not
modelled is **not silently scored as zero.** Anything the scorer cannot compute — an unknown
joker, a Blueprint copy chain (ordering unverified, spec §9), a random trigger
like Misprint or a Lucky card, or a scaling joker whose counter the adapter did
not read — sets `exact: false` and lands in `unmodelled`:

```
= floor(160 x 11) = 1760
! NOT EXACT. Unmodelled: j_blueprint. Treat 1760 as a floor, not a score.
```

Every downstream stage respects this. `clears_blind` reports `null` rather than
`false` when a floor falls short, since a floor proves nothing. The advisor is
told it may not quote the number as fact. The validator rejects advice that
quotes an inexact score with an empty `UNCERTAIN`.

Scaling jokers read `internal_state.counter` — **the number the game displays
on the joker right now**. For an Xmult joker that number *is* the current
multiplier. This deliberately keeps per-joker growth constants out of the data
table: a growth rate transcribed from memory is an invented number, which is
the failure this whole design exists to prevent.

## Verbosity modes

Spec §5a makes it a hard constraint that beginner mode never recommends a
different action than expert mode. Rather than instruct that and test for it,
the implementation makes it structural:

- **Stage 1 (decision)** runs a mode-independent prompt. Expert mode *is* this
  stage — one call, no rendering pass.
- **Stage 2 (render)** runs only for beginner mode, is handed the finished
  decision, and may rewrite the explanation only. The `DECISION` line is copied
  through **in code**, so the rendering stage has no channel by which it could
  change the recommendation.

Stage 1's cache entry is shared across modes, so switching to beginner costs
one short call rather than two. The §5a regression test still ships, now
guarding future refactors instead of carrying the guarantee by itself.

## Validation

Deterministic, microseconds, runs on every response **including cache hits** —
a cached response valid under an older ruleset may not be valid now.

| Check | Catches |
|---|---|
| Legality | Cards not in hand, 6-card plays, selling an Eternal joker, buying past your money or your slots, picking more than a pack allows, playing a hand while in the shop |
| Arithmetic | Any number in the prose with no source in the computed set. This is the check that catches 36,000 against 20,700. Differences and ratios between computed scores are allowed; inventions are not |
| Consistency | A recommended play that was never enumerated; a claim about a joker that contradicts its reported `current_contribution`; an unacknowledged failure to clear the blind |
| Mechanics | Assertions about the two mechanics recorded as genuinely unresolved, and the specific Jumbo/Mega pick-count error made during manual testing. Unbacked rule claims are *flagged for review*, not blocked |

Advice that fails is regenerated once with the failed check appended as a
constraint. If it fails again it is **never shown** — the output degrades to
the top-ranked computed play with no prose and a note saying so.

The validator cannot catch advice that is legal but strategically wrong.
Selling the wrong joker is a valid action and no rule check will flag it. There
is a test asserting exactly that, so nobody mistakes a passing validator for a
correctness guarantee. Judgment quality is measured by the decision log.

## Decision log

One record per interaction, with an outcome filled in afterwards:

```bash
balatro-advisor outcome 1-d867cf57c03e --action played_recommended --score 20700
balatro-advisor log
```

`predicted_score` vs `actual_score` is the field that matters. They diverge →
the **scorer** is wrong and the advice was built on bad arithmetic. They match
but the run still failed → the scorer is fine and the **judgment** needs work.
Different bugs, different fixes, and without this they are indistinguishable.

## Layout

| Path | What |
|---|---|
| `schema/state.schema.json` | The canonical state definition. Single source of truth — §2 of the spec now points here rather than carrying a second copy |
| `src/balatro_advisor/core/` | Schema validation, static data, card shorthand, hand classification, scorer, enumerator |
| `src/balatro_advisor/advisor/` | Prompts, providers, parsing, cache, decision log, glossary, pipeline |
| `src/balatro_advisor/validator/` | The §5d checks |
| `src/balatro_advisor/adapters/` | `manual.py` — CLI entry with fuzzy joker matching and delta-only prompts |
| `src/balatro_advisor/data/` | Jokers, blinds, vouchers, hand levels, verified mechanics |
| `fixtures/` | Replay fixtures, each carrying its hand-written derivation |

## Honest gaps

**No captured fixtures.** All 16 are `hand_computed`: the arithmetic was worked
out by hand, written into the fixture's `arithmetic` field, and checked against
the scorer. That proves the scorer is self-consistent and does not regress. It
**cannot** prove the scorer matches the game — both could be wrong the same
way. Closing this needs the mod adapter, which §0 blocks. See
[`fixtures/README.md`](fixtures/README.md).

**67 of 150 jokers have no effect data yet.** All 150 are *known* — correct
name, correct rarity, verified against an independent enumeration — but only 77
are *modelled*. The gap closes when the wiki pages land in
`data/sources/wiki/`; until then those jokers make a hand non-exact rather than
being guessed at. The 77 already modelled came from recall and have not
themselves been checked against a source, which is why the import verifies all
150 rather than only the 67.

**The live API path has not been exercised.** It is written against the SDK
reference, but no credential was available in the environment where this was
built. The stub provider is what the test suite runs.

## Not built yet, and why

Ordered by spec §8's build order. Steps 1–8 and 10 are done.

- **`adapters/mod` (Lua, step 9).** §0: *"Do not begin Phase 1 without
  confirming a working modded install exists."* The developer's machine — a
  2017 MacBook Pro on macOS 11 — cannot run one. An exporter that can never be
  run against the game produces untested Lua and fixtures with fake
  provenance. This is the blocking prerequisite for everything above marked as
  a gap.
- **`adapters/vision` (step 11).** §6: only after Phase 1 works end to end. The
  schema already carries the `confidence` field it will need.
- **Monte Carlo discard evaluation (step 12).** §4 defers draw probability to a
  later version. `discard_candidates` ship with a real `floor_score` and with
  `expected_score` and `p_clears_blind` left `null` rather than guessed. When
  added they must be sampled (~200 draws), never enumerated over the draw
  space.

## Development

```bash
pytest                          # 164 tests, no network required
python tools/build_fixtures.py  # regenerate fixtures from their derivations
balatro-advisor fixtures        # the regression gate for any scorer change
```

Run the fixture set on every scorer change. When one fails, read its
`arithmetic` field first — the fixture is as likely to be wrong as the scorer,
and the derivation is recorded so you can tell which.
