# Known issues

Tracked problems that are understood but deliberately **not fixed yet**. Each entry has a
repro, the root cause, and concrete example sentences found by scanning the papers under
`/mnt/sdc1/aim/code/dwro/papers_list` (eval set). Do not "fix" the behaviours noted elsewhere in
AGENTS.md as intentional.

---

## 1. Inline math in the middle of a sentence breaks segmentation and highlighting

**Symptom.** When a TeX formula string sits *inside* a sentence (not as a standalone display
equation), the sentence is split into pieces and a garbage math fragment appears on its own; the
following clause is orphaned, and the highlight for the sentence no longer matches.

**Example (user-reported, `2303.04137v5` Diffusion Policy, page 1).** The source sentence
"… where ε_θ is the noise prediction network with parameters θ that will be optimized through
learning, and N(0, σ²I) is Gaussian noise added at each iteration." becomes three segments:

- `#36` `"where ε θ is the noise prediction network with parameters θ that will be optimized …"`
- `#37` `"0 , σ 2 I \x01"`  ← the inline `N(0, σ²I)` leaks out as its own fragment, carrying the
  PDF's `\x00`/`\x01` brace control characters
- `#38` `"is Gaussian noise added at each iteration."`  ← starts lowercase, but its predecessor is
  a math fragment, so the lowercase-merge rule in `_merge_fragments` cannot rejoin it

**Root cause.** The PDF text layer renders inline math with spaced glyphs and `\x00`/`\x01` as
large-bracket delimiters (e.g. `N \x00 0 , σ 2 I \x01`). The sentence segmenter (spaCy/pysbd)
breaks around these, and the leaked fragment is math-ish, so neither the display-equation path nor
the lowercase-start merge reattaches the pieces.

**Scope found in the eval set.**

| paper | real breakage | note |
|---|---|---|
| `2303.04137v5` (Diffusion Policy) | **yes** | `N(0, σ²I)` leak + 3-way split (above) |
| `2003.08934v2` (NeRF) | **yes (minor)** | tiny leaked fragments `"(c)."`, `"(d)."` |
| `2102.09672v1` (Improved DDPM) | no | `"where ϵ∼N(0, I)."` is a *legitimate* post-equation clause |
| `1706.03762v7` (Transformer) | no | `"where pos is the position …"` legitimate clause |
| `2003.12056v2` | no | clean |

**Not this bug.** A `"where X is Y."` clause as its own segment *after* a display equation is
correct (it reads fine and highlights correctly) — do not merge those into the equation.

**Fix direction (when tackled).** Detect inline-math runs (spaced glyphs + `\x00`/`\x01` + math
chars) and either keep them attached to their surrounding clause or strip the control characters
before segmentation, so a mid-sentence formula does not split the clause.

---

## 2. Display-equation block looks too wide / too spaced in the translation panel

**Symptom.** A display equation (`\[ … \]`) in the translation panel renders as a wide,
centered block with generous vertical space, even for a short formula.

**Root cause.** This is KaTeX's **default** display-math rendering (`.katex-display`: `display:
block`, `text-align: center`, `margin: 1em 0`). There is no custom CSS for it in `web/styles.css`,
so the equation takes the full panel width, centered, with 1em top/bottom margins. Rendering
display math as its own block is intentional; the generous centering/margin is just the default.

**Fix direction (cosmetic, when tackled).** Add CSS for `.trlist .katex-display` (e.g. smaller
margins, left-align) if a tighter look is preferred.
