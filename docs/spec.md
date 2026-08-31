<!--
This is the original build specification, preserved as written, with one
change: the JSON state block in section 2 has been replaced by a pointer to
schema/state.schema.json. That change is the spec's own instruction (see
section 2's schema notes and build step 1), carried out.

Where the implementation departs from this document, or defers part of it, the
README explains why. This file is not edited to match the code - it is the
brief the code is measured against.
-->

# Balatro Advisor — Build Specification

A tool that reads Balatro game state and returns strategic advice. Three input
modes, one shared advisory core.

---

## 0. Blocking prerequisite — read first

Phase 1 requires a moddable desktop Balatro install. As of this writing the
developer's machine is a 2017 MacBook Pro on macOS 11 Big Sur, which **cannot
run Phase 1**:

- Steam ended macOS 11 support on 15 October 2025; the client will not run.
- Balatro has no DRM-free desktop macOS build (not on GOG; Steam is the
  practical route).
- Upgrading to macOS 12 would break Waves audio plugins at V12 or below, which
  are capped at macOS 11.5.2 on Intel.

**Implication:** either build Phase 1 on a different machine, or build Phase 3
(manual entry) first and treat it as the reference implementation. Phase 3 has
no platform dependency and exercises the same advisory core.

Do not begin Phase 1 without confirming a working modded install exists.

---

## 1. Architecture

The single most important design decision, derived from testing the advisory
loop by hand:

> **Arithmetic and judgment must be separate stages.** A language model
> estimating a Balatro score produced 36,000 against an actual 20,700 — a 75%
> error — because it guessed at two joker values it could not read. The same
> model's structural advice (which joker to sell, which hand to play, which
> shop items to skip) was correct throughout.

Therefore:

```
input adapter  →  canonical state (JSON)  →  deterministic scorer  →  advisor  →  validator  →  output
   (3 modes)         one schema              pure code, no LLM       LLM       pure code
```

The advisor never computes a score. It receives computed scores as input and
reasons about them.

### Components

| Component | Responsibility | Implementation |
|---|---|---|
| `adapters/mod` | Read state file written by the Lua mod | Phase 1 |
| `adapters/vision` | Screenshot → state, with human confirmation | Phase 2 |
| `adapters/manual` | CLI/TUI prompts → state | Phase 3 |
| `core/schema` | Canonical state definition + validation | Phase 1 |
| `core/scorer` | Exact score for a candidate hand | Phase 1 |
| `core/enumerate` | Generate candidate plays from a hand | Phase 1 |
| `advisor` | LLM call with state + computed scores | Phase 1 |
| `validator` | Rule-check advice before it reaches the user | Phase 1 |

---

## 2. Canonical state schema

Every adapter produces this. Validate on ingest; refuse to advise on invalid
state rather than guessing.

> **The schema lives in [`schema/state.schema.json`](../schema/state.schema.json).**
>
> Section 2 of this document originally carried an illustrative copy of the
> state shape. That copy has been removed rather than kept in sync, because
> this section itself said so: *"Two copies of a schema drift, and the copy in
> the prose spec is the one that will silently go stale."* The schema file is
> now the single source of truth, and it is real JSON Schema that
> `core/schema.py` validates against on every ingest.
>
> The notes below are kept because they are reasoning, not structure, and the
> schema file cross-references them.

### Schema notes

- **Extract this schema to its own file once `core/schema` exists.** Move it to
  `schema/state.schema.json` as real JSON Schema, not the illustrative example
  above, and have this document reference it rather than duplicate it. Two
  copies of a schema drift, and the copy in the prose spec is the one that will
  silently go stale. Do this at build step 1 (§8), not later.
- **`choices_allowed` is not derivable from pack size.** Jumbo packs hold more
  cards but still allow one pick; Mega packs allow two. Read it from the game,
  do not infer it.
- **Stone cards have no rank.** They will not appear in rank tallies. Expect
  `deck.total` to exceed the sum of rank counts.
- **Wild cards count as multiple suits.** Suit totals may exceed the ranked card
  count. Do not treat this as a validation failure.
- **`current_contribution`** is what the joker is producing *right now*, not its
  description. Joker Stencil showing `xmult: 1` while five slots are full is
  correct and is exactly the kind of fact the advisor must be handed rather than
  asked to infer.
- **`seq` is a monotonic counter**, incremented by the writer on every write.
  The reader compares it against the last value seen to know whether state is
  fresh. Do not use `captured_at` for this — filesystem timestamp granularity and
  clock behaviour make it unreliable, and content diffing is both slower and
  wrong when a legitimate write produces identical state. Three lines of Lua that
  remove an entire class of race-condition debugging. See §3.1.

---

## 3. Phase 1 — Mod adapter

### 3.1 Lua mod

A Steamodded mod that serializes game state to disk.

**Write triggers** — state transitions only, never per frame:
- blind selected
- hand played (after scoring resolves)
- hand discarded
- shop entered
- shop item purchased / sold / rerolled
- booster pack opened
- round ended

**Write atomically.** Serialize to `state.json.tmp`, then `os.rename` to
`state.json`. A reader must never see a partial file.

**Increment `seq` on every write.** A single integer, monotonically increasing
for the lifetime of the process, written into the state file. The reader polls
the file and compares `seq` to the last value it processed; unchanged means
nothing new, and there is no need to diff content or trust timestamps. Combined
with atomic rename this makes the reader trivially correct.

**Output path:** `~/Library/Application Support/Balatro/advisor/state.json`

**Emit JSON, not Markdown.** Markdown invites formatting decisions and is harder
to parse reliably. Render Markdown downstream if a human wants to read it.

### 3.2 Scoring in Lua

Compute candidate hand scores **inside the mod**, where the real values live,
and include them in the state file. This eliminates the entire class of error
described in §1.

For the current hand, enumerate legal plays (see §4) and emit:

```json
"candidate_plays": [
  {
    "cards": [0, 1, 4, 5],
    "hand_type": "two_pair",
    "chips": 230,
    "mult": 90,
    "score": 20700,
    "clears_blind": true,
    "gold_forfeited": 9,
    "triggers": ["photograph", "blue_joker"]
  }
]
```

If reimplementing scoring in Lua proves too invasive, the fallback is a Python
scorer in `core/scorer` — but it must then reimplement the full joker ruleset,
including edition bonuses, retrigger order, and left-to-right joker evaluation.
Prefer the Lua route.

### 3.3 Discovery work

Most of the Phase 1 effort is finding which fields Steamodded exposes versus
which must be dug out of `G.GAME`, `G.jokers`, `G.playing_cards`. Budget for
this. Write the exporter first, dump a few real files, and inspect them by hand
before building anything downstream.

---

## 4. Candidate play enumeration

Given 8 cards, enumerate every subset of size 1–5, classify its poker hand, and
score it. That is 218 subsets — trivially cheap.

Rank candidates by:
1. Clears the blind (boolean, dominant)
2. Score, descending
3. Gold/Steel value forfeited, ascending (tiebreak)

Also enumerate **discard candidates** when discards remain: for each subset of
size 1–5, report what is kept and what the best play would be from the kept
cards. Do not attempt to compute draw probabilities in v1 — the advisor can
reason about them qualitatively from `deck.cards` where `location == "deck"`.

### Discards: sample, do not enumerate

When draw evaluation is added in a later version, **do not enumerate the draw
space.** The play enumeration is 218 subsets and costs nothing, but each discard
candidate implies a distribution over replacement draws, and enumerating that
distribution is combinatorially expensive for no added accuracy.

Use Monte Carlo instead: for each discard candidate, sample ~200 draws from
`deck.cards` where `location == "deck"`, score the best resulting play for each,
and report the mean and the probability of clearing the blind. Two hundred
samples is enough for a stable ranking and costs microseconds. Emit:

```json
"discard_candidates": [
  {
    "discard": [2, 3, 6],
    "keep": [0, 1, 4, 5, 7],
    "floor_score": 12400,
    "expected_score": 21800,
    "p_clears_blind": 0.62,
    "samples": 200
  }
]
```

`floor_score` is the best play available from the kept cards alone, with no draw
— the guaranteed outcome if every draw misses. The advisor needs both numbers:
the floor determines whether discarding is safe, the expectation determines
whether it is worthwhile.

---

## 5. Advisor layer

### Inputs
Canonical state + computed candidate scores. Nothing else.

### Prompt structure

```
SYSTEM: You advise on Balatro decisions. All scores below are computed
exactly by a deterministic engine. Never recompute or estimate a score.
If you need a number that is not provided, say so rather than guessing.

Report uncertainty explicitly. A wrong confident answer is worse than an
acknowledged gap.

USER: <canonical state JSON>
      <candidate plays with exact scores>
      <specific question, or "what should I do?">
```

### Required advisor behaviours

These are drawn from real failures observed while doing this by hand:

- **Never estimate a score.** If `candidate_plays` is absent, say so.
- **Surface dead jokers.** A joker producing X1 or +0 in the current
  configuration is the highest-value finding available and is easy to miss.
  Check `current_contribution` on every joker before advising anything else.
- **Account for opportunity cost of joker slots.** With Joker Stencil in play, an
  empty slot has a concrete multiplier value. Buying a joker can be strictly
  negative. Compute the delta, do not assume filling slots is good.
- **Respect stickers.** Eternal means the purchase is irreversible. Perishable
  means it expires. Both change a marginal buy into a bad one.
- **Flag economy effects of plays.** Gold enhancements pay only if held in hand
  at end of round; playing them forfeits the payout. Report the amount.
- **Do not assume mechanics.** If unsure whether a mechanic exists, say so.
  (Two errors made by hand: asserting that held consumables do not affect the
  shop pool, and reversing Jumbo/Mega pack pick counts. Both were stated
  confidently and both were wrong.)

### Output format

```
DECISION: <one line>
REASONING: <2–4 sentences, referencing exact numbers>
ALTERNATIVES: <what else was considered and why it lost>
UNCERTAIN: <anything the advisor could not determine, or empty>
```

---

## 5a. Verbosity modes

Two presentation modes over **identical analysis**. The scorer, the enumerator,
and the decision itself are the same in both. Only the explanation changes.

This is a hard constraint: beginner mode must never recommend a different action
than expert mode. Simplifying the reasoning is acceptable; simplifying the
decision is not. If the correct play is unintuitive, beginner mode explains it
more carefully — it does not substitute a safer, worse play.

### Expert mode (build first)

The default and the reference implementation. Assumes fluency with the game's
vocabulary.

- Uses terms unglossed: xmult, retrigger, scaling, dead slot, interest cap,
  sell value, pool, seed.
- States exact numbers and the multiplication chain.
- Discusses opportunity cost directly ("filling the slot converts Stencil X2 to
  X1, halving final mult").
- Assumes the player knows what each joker does; refers to jokers by name
  without describing their effect.
- Raises long-horizon considerations: whether a build has an unbounded scaling
  joker, whether the run is aimed at ante 8 or at Endless.

### Beginner mode

Same decision, explained for someone still learning.

- **Gloss every term on first use in a session.** "Xmult (a multiplier that
  multiplies your score, rather than adding to it)".
- **State what each joker involved actually does**, since the player may not
  remember. Pull the description from the static data table (§7).
- **Lead with the action, then the why.** "Sell Supernova." then the reason.
- **Give one reason, not four.** Expert mode may list competing considerations;
  beginner mode picks the dominant one and says so.
- **Show the arithmetic in words, not just symbols.** "Right now your multiplier
  is doubled once. After selling, it gets doubled twice — roughly 35% more
  score."
- **Do not surface marginal considerations at all.** A $3 Gold forfeit or a
  small interest-cap effect is noise when someone is learning; omit it unless it
  changes the decision.
- **Flag when a recommendation is counterintuitive**, and say why the obvious
  move is wrong. "Filling an empty joker slot usually helps. Not here, and this
  is why."

### Implementation

- Single flag on the advisor call: `mode: "expert" | "beginner"`.
- Two system prompts, one analysis path. Do **not** fork the reasoning logic.
- Simplest correct implementation: run the analysis once, then render the
  explanation at the requested level. If a single LLM call handles both, ensure
  the decision is produced before the verbosity instruction is applied, so
  presentation cannot influence the choice.
- Add a `mode` field to the test fixtures. For each fixture, assert that expert
  and beginner produce the **same** `DECISION` line. This is the regression test
  that matters.
- A per-session term glossary avoids re-explaining "xmult" on every turn. Track
  which terms have been glossed.

### Not in scope for beginner mode

Beginner mode is a presentation layer, not a teaching curriculum. It does not
run tutorials, quiz the player, or explain Balatro from scratch. It answers the
question asked, in plainer language.

---

## 5b. Response caching

Cache advisor responses keyed on a hash of the canonical state. Build this early
— it pays for itself during development, not just in production.

**Why it matters more than it looks:** shop states repeat constantly. A reroll
that changes one irrelevant item, a screenshot retaken because the first was
blurry, a fixture re-run for the fiftieth time while debugging the scorer — all
produce identical or near-identical state. Every one is a paid API call taking
several seconds. During development you will re-run the same fixtures hundreds
of times.

### Cache key

Hash the canonical state **excluding** fields that do not affect the advice:

- Exclude: `seq`, `captured_at`, `source`.
- Include: everything else, plus the advisor `mode` (§5a) — expert and beginner
  outputs differ and must not collide.
- Normalize before hashing: sort object keys, so serialization order cannot
  produce cache misses on identical state.

### Behaviour

- Cache on disk, not in memory, so it survives between runs. A simple
  `cache/{hash}.json` layout is sufficient.
- Include the prompt version in the key, or invalidate the whole cache when the
  system prompt changes. Stale advice from an old prompt is worse than no cache.
- Provide a `--no-cache` flag for when prompt changes are being evaluated.

---

## 5c. Decision logging

Log every advisory interaction with its outcome. This is the only mechanism that
distinguishes a wrong recommendation from a wrong calculation after the fact,
and both occurred during manual testing.

### Record per call

```json
{
  "seq": 1417,
  "timestamp": "2026-08-31T10:14:22Z",
  "mode": "expert",
  "state_hash": "a3f9...",
  "state": { /* full canonical state */ },
  "candidate_plays": [ /* as computed */ ],
  "advice": {
    "decision": "Play K♥ K♦ J♠ J♥ (Two Pair)",
    "reasoning": "...",
    "uncertain": []
  },
  "cache_hit": false
}
```

### Record the outcome, once known

```json
"outcome": {
  "action_taken": "played_recommended | played_other | ignored",
  "actual_score": 20700,
  "predicted_score": 20700,
  "cleared_blind": true,
  "ante_survived": true,
  "run_ended_at_ante": null
}
```

`predicted_score` versus `actual_score` is the single most valuable field. If
they diverge, the scorer is wrong and the advice was built on bad arithmetic. If
they match but the run still fails, the scorer is fine and the *judgment* needs
work. These are different bugs with different fixes, and without logging they
are indistinguishable.

The outcome can be filled in by the mod adapter automatically — it observes the
hand played and the score awarded. In manual and screenshot modes, prompt for it
or leave it null.

After fifty logged rounds you have a dataset showing where the advisor is
actually wrong, rather than where it feels wrong.

---

## 5d. Validation stage

A deterministic check that runs **between the advisor and the user**. Advice that
fails validation is never shown; it is regenerated or downgraded to an explicit
"cannot advise" response.

```
advisor (LLM)  →  validator (pure code)  →  output
                        ↓ fail
                  regenerate once, then refuse
```

### What this catches, and what it does not

The validator catches advice that **contradicts the game rules or the current
state**. It cannot catch advice that is legal but strategically wrong — selling
the wrong joker is a valid action, and no rule check will flag it. Judgment
quality is measured by the decision log (§5c), not here.

Both failure types occurred during manual testing. The validator addresses one
of them.

### Checks

**Legality — the action must be possible right now**

- Referenced cards exist in `current_hand` and are not duplicated.
- Play size is 1–5 cards.
- Discard size is 1–5 and `discards_remaining > 0`.
- Purchase price ≤ `run.money`.
- Purchase does not exceed `joker_slots_total` or `consumable_slots_total`
  without a corresponding sale in the same advice.
- Sale target exists and does not carry the `eternal` sticker.
- Pack picks ≤ `choices_allowed`.
- The action is legal in the current `phase` — no "play this hand" advice while
  in `shop`.

**Arithmetic — no invented numbers**

- Every score quoted in the advice text appears in `candidate_plays` or
  `discard_candidates`. Extract numerals from the prose and match them against
  the computed set.
- Any number in the text with no source is a hallucination. This is the check
  that would have caught the 36,000 estimate against an actual 20,700.
- Percentages and deltas must be derivable from computed values.

**Consistency — the advice matches its own reasoning**

- If `DECISION` names a play, that play exists in `candidate_plays`.
- If the reasoning asserts a joker contributes X, compare against
  `current_contribution`. Claiming Joker Stencil gives X2 while five slots are
  full is a contradiction the state already disproves.
- If `clears_blind` is false for the recommended play and hands remain, the
  advice must acknowledge it rather than implying the blind is cleared.

**Mechanics — no asserted rules outside the known set**

- Maintain `data/mechanics.json`: verified rules with a source.
- Flag advice that asserts a mechanic not in that file for review rather than
  blocking it. The goal is catching confident invention, not constraining
  discussion.
- Seed it with the errors already made by hand: Jumbo packs allow one pick and
  Mega allow two (stated backwards); whether held consumables affect the shop
  pool is **unresolved** (§9) and must not be asserted in either direction.

### On failure

1. Log the failure with the offending advice and the failed check. These are the
   most valuable entries in the decision log.
2. Regenerate once, with the failed check appended to the prompt as a constraint.
3. If it fails again, return the deterministic fallback: the top-ranked entry
   from `candidate_plays` with no prose reasoning, plus a note that advisory
   generation failed.

Never show unvalidated advice. A refusal is recoverable; confident wrong advice
is what loses trust in the product.

### Cost

Pure code, no API call, microseconds. It runs on every response including cache
hits — a cached response that was valid under an older ruleset may not be valid
now.

---

## 6. Phase 2 — Screenshot mode

Only start this after Phase 1 works end to end.

### Known limitations, measured

Testing by hand against real screenshots:

- **Numeric UI reads reliably.** Ante, round, money, hands, discards, deck
  count, blind requirement, hand levels, shop prices — all read correctly.
- **Joker identity does not read reliably.** Sprites at UI scale are not
  distinguishable with confidence. Negative edition inverts colours and makes it
  worse. Editions were also misread: Blue Joker's base art has diagonal blue
  striping that resembles a Foil overlay, and reading it as Foil introduced a
  50-chip error.
- **A second, closer screenshot did not fix this.** The limit is the source
  resolution, not the crop.

### Design consequence

Do not attempt full automatic recognition. Structure it as:

1. Vision pass extracts everything it can into the canonical schema.
2. **Every joker field is marked `confidence: low` by default** and surfaced for
   confirmation.
3. User confirms or types corrections.
4. Only then does the scorer run.

Deck composition is not visible in a board screenshot. Prompt for a second
capture of the deck view, or mark `deck.cards` as unknown and have the advisor
degrade gracefully.

### Cheap win
Cache confirmed joker identities for the session. Jokers change rarely; ask once
per acquisition, not once per screenshot.

---

## 7. Phase 3 — Manual entry

Lowest fidelity, zero dependencies, and the only mode that runs on the
constrained machine described in §0. Consider building it first.

- Prompt for jokers by name with fuzzy matching against a static joker table.
- Persist run state between prompts; only ask for deltas each round.
- Accept shorthand card entry: `KH QD 10S 4D` etc.
- Reuse the identical schema and scorer.

A static data table of jokers, vouchers, blinds, and hand base values is needed
regardless of mode. Build it in Phase 1 and share it across all three.

---

## 8. Build order

1. `core/schema` + validator — extract the §2 schema to
   `schema/state.schema.json` as real JSON Schema; the spec then references it
   instead of carrying a second copy
2. Static data tables (jokers, blinds, vouchers, hand levels) — include a plain
   English effect description per joker; beginner mode needs it later
3. **Replay fixtures** — capture real state/score pairs before writing the
   scorer, so it has ground truth to be validated against from the first commit
4. `core/enumerate` + `core/scorer` — run against the full fixture set on every
   change
5. `adapters/manual` — cheapest way to exercise 1–4
6. `advisor` — expert mode only, prompt, output parsing
7. `validator` (§5d) — build immediately after the advisor, before any real use;
   an unvalidated advisor is the failure mode the whole architecture exists to
   prevent
8. **Response cache** (§5b) and **decision log** (§5c) — build alongside the
   advisor, not after; the cache pays for itself immediately during development
   and the log is worthless if it starts late
9. `adapters/mod` — Lua exporter with `seq`, atomic writes, Lua-side scoring,
   automatic fixture and outcome emission
10. Beginner mode — presentation layer over the working advisor
11. `adapters/vision` — last, and only with confirmation UI
12. Monte Carlo discard evaluation (§4) — optional, after everything else works

Ordering notes:

- Fixtures at step 3 precede the scorer deliberately. Writing the scorer first
  and testing it later means testing it against your own assumptions rather than
  against the game.
- Beginner mode lands at step 10. It is a rendering of expert output, so expert
  mode must be correct and stable first; building both at once risks the two
  paths diverging in what they recommend.
- Step 8 looks like infrastructure that can wait. It cannot: an uncached advisor
  makes development slow and expensive, and a log that starts after fifty rounds
  of play has missed the fifty rounds that would have told you the most.

### Test fixtures — build these first

The scorer is the component everything else depends on. Get ground truth for it
before writing the advisor, or you will spend time debugging advice when the
bug is in the arithmetic.

**Replay fixtures are free ground truth.** Play a hand and record two things:
the canonical state immediately before, and the score the game actually awarded
after. Each pair is a test case the game itself validated. Twenty of these makes
the scorer trustworthy.

```json
{
  "name": "ante5_house_two_pair_kings_jacks",
  "state_before": { /* canonical state */ },
  "cards_played": [0, 1, 3, 4],
  "expected_chips": 230,
  "expected_mult": 90,
  "expected_score": 20700
}
```

The mod can emit these automatically: on every hand played, write the pre-play
state and the resolved score as a fixture file. Playing normally for an hour
generates a full regression suite at no effort.

Cover shop, playing, and pack-open phases. Include at least one fixture whose
correct answer is counterintuitive — selling a joker beating buying one, or a
lower-level hand outscoring a higher one — since those are the cases where a
plausible-but-wrong implementation still passes everything else.

Run the full fixture set on every scorer change. A scorer that passes twenty
real hands is a scorer you can build on.

### What not to optimize

**Resist making sprite recognition cleverer.** The confirmation step in §6 is not
a stopgap awaiting a better model; it is the design. Effort spent on template
matching, sprite hashing, or fine-tuning a classifier is effort not spent on the
scorer, which is where the value is. Vision mode's job is to save typing, not to
be autonomous.

---

## 9. Open questions

- Does holding a consumable exclude it from the shop/pack pool? This was
  disputed during manual testing and could not be verified from the wikis. It
  affects hold-versus-sell advice. Resolve by reading the game's pool logic
  directly once the mod is running — that is the authoritative source.
- Retrigger ordering with Blueprint/Brainstorm chains: verify against the game
  rather than assuming.
- Boss blind effects that alter scoring (not just drawing) need explicit
  modelling in the scorer.
