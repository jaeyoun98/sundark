# Known issues

Tracked problems found by scanning the papers under `/mnt/sdc1/aim/code/dwro/papers_list` (the
eval set). Do not "fix" the behaviours noted in AGENTS.md as intentional.

---

## 1. Inline math in the middle of a sentence splits the clause  (PARTIALLY FIXED)

**Symptom.** When a TeX formula sits *inside* a sentence (not as a standalone display equation),
the surrounding clause is split and the highlight no longer matches.

**Example (`2303.04137v5` Diffusion Policy, page 1).** The source
"… where ε_θ is the noise prediction network … and **N(0, σ²I)** is Gaussian noise added at each
iteration." is split around the inline `N(0, σ²I)`.

**Fixed part (control-character garbage).** The PDF renders math brackets with `\x00`/`\x01`
control chars, which used to leak into the text as a fragment like `"0 , σ 2 I \x01"`.
`_CTRL_STRIP` in `pdf_utils._words_with_style` now strips C0/DEL control chars from every word
token, so that garbage fragment is gone and the display equation renders cleanly.

**Still open (the clause split).** The layout model boxes the inline `N(0, σ²I)` as a `formula`
region, so paragraph reconstruction splits the prose line into two paragraphs
("… optimized through learning, and" | "is Gaussian noise added …"). They are not rejoined because
a naive lowercase-start merge across paragraphs would also wrongly merge a legitimate lead-in and
its post-equation clause (e.g. "The process follows the equation" + [display eq] + "where ε_θ …").
A correct fix needs to distinguish an **inline** formula box (small, mid-prose-line) from a
**display** equation box and not break the prose around the inline one.

**Scope in the eval set:** confirmed on `2303.04137v5` and (minor leaked `(c)./(d)` fragments) on
`2003.08934v2` (NeRF). A `"where X is Y."` clause *after* a display equation is NOT this bug —
it is a correct standalone clause; do not merge those.

---

## 2. Display-equation block too wide/spaced in the translation panel  (FIXED)

KaTeX display math (`\[ … \]`) defaulted to a full-width centred block with 1em margins, so a short
formula took a big empty cell. Fixed in `web/styles.css` with
`.trlist .katex-display { margin: 0.35em 0; overflow-x: auto; overflow-y: hidden; }` — tighter
vertical spacing, and a wide equation scrolls instead of stretching the panel.
