# Wiki source drop-zone

`balatrowiki.org` is unreachable from the build environment — the egress proxy
returns `EGRESS_BLOCKED` for both `curl` and the fetch tool. So the pages get
fetched by hand and committed here, and `tools/import_wiki.py` parses whatever
lands in this directory.

Everything here is **committed verbatim, unmodified**. That is the point: the
joker table's numbers become auditable against a file in the repo rather than
against someone's memory of a web page.

---

## Step 1 — the index page. Two files, five minutes.

Do this first. It may be all that's needed.

**1a. Wikitext.** Append `?action=raw` to the Jokers page URL and save the
result:

```
https://balatrowiki.org/w/Jokers?action=raw
```

Save as `jokers.wikitext`.

**1b. Rendered HTML.** Load the same page normally, then
**Save Page As → "Web Page, HTML Only"** (not "Complete" — images and CSS are
just noise here).

Save as `jokers.html`.

Both, because game wikis usually build effect text out of templates. Wikitext
shows the template call; the rendered HTML shows the numbers it produced. One
of the two will have what we need and it is not worth a round-trip to find out
which.

> If the URL layout differs — `/wiki/Jokers`, `/index.php?title=Jokers`,
> whatever — just use what the browser shows and append `?action=raw`
> (or `&action=raw` if there is already a `?` in it).

---

## Step 2 — the jackpot check. 30 seconds, possibly saves everything else.

Game wikis often keep their real data in a Lua module rather than in the
article, which would give structured numeric constants directly. Load:

```
https://balatrowiki.org/w/Special:AllPages?namespace=828
```

If anything looks like `Module:Jokers/data`, `Module:CardData` or similar, grab
it with `?action=raw` and save it here under its own name. That would replace
most of the parsing work and is the single highest-value thing on this page.

---

## Step 3 — only if step 1 comes up short.

If the index page turns out not to carry per-joker numbers, the individual
joker pages are needed. Do **not** save 150 pages by hand — use `Special:Export`,
which takes a pasted list of titles and returns one XML file:

1. Go to `https://balatrowiki.org/w/Special:Export`
2. Paste the contents of **`PAGES-all-150.txt`** into the big text box
3. Tick **"Save as file"**, leave "Include only the current revision" ticked
4. Export, and save the result here as `jokers-export.xml`

**Use the 150 list, not the 67 list.** `PAGES-67-missing.txt` is the set with no
effect data at all, but the other 77 were modelled from recall and have never
been checked against a source. Exporting all 150 lets the importer verify those
too, which matters more than it sounds: a wrong constant in an already-modelled
joker is invisible today and produces confidently wrong scores.

---

## What the importer does with this

`tools/import_wiki.py` (built once the first file lands here) will:

- parse name, rarity, cost and effect text for all 150
- translate effect text into the declarative grammar in `data/jokers.json`
- stamp every entry with `source: "balatrowiki.org/<page> @ <fetch date>"` and
  clear `needs_verification`
- **report disagreements** with the 77 already-modelled jokers rather than
  silently overwriting either side — each one gets looked at by hand
- leave the two mechanics that spec §9 records as unresolved (whether holding a
  consumable excludes it from the pool; Blueprint/Brainstorm ordering) marked
  unresolved regardless of what the wiki asserts. §9 is explicit that those
  could not be settled from wikis and resolve only by reading the game's pool
  logic.

## Randomness

Random effects are modelled with their real probability, and the scorer reports
expected value (with floor and ceiling alongside, since they are nearly free).
So what is needed from the wiki for e.g. Misprint is the **range**, and for
Bloodstone the **odds** — not a single number. Those are stated on the pages;
they just need to land here rather than being recalled.
