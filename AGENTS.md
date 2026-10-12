# AGENTS.md — rules for anyone (human or AI) changing this repo

Read this before editing. It captures the non-obvious decisions that keep this
project working. Keep changes surgical and in the existing style.

## What this is

A personal, local web app that reads conference/journal PDFs and provides
whole-document translation, a grounded "Research Anatomy", an auto summary, and
paper-aware Q&A chat. It runs through the user's own subscription in the chosen AI
Backend (Claude Code or Hermes) — never a billable API key.

## Architecture and boundaries

- **Only `app/backends/` starts AI processes**; `app/ai.py` owns every prompt. See
  *AI Backends* below.
- **Paper ID vs File Fingerprint (ADR 0001).** `doc_id` is the File Fingerprint and keys every
  cache derived from the file (layout, translation, summary, anatomy, equations). The Paper ID
  (`app/paper_id.py`: `doi:`, `arxiv:`, `zotero:`, or `file:<doc_id>`) keys Chat History and
  the translation glossary. `app/biblio.py` fetches the Bibliographic Record and rejects a
  DOI whose record title is not on the first page (a cited work's DOI). Zotero integration
  calls `main._ingest(data, {"key": ..., "doi": ...})`; Zotero's DOI wins.
- **`app/pdf_utils.py`** turns a PDF page into word geometry + sentences:
  `get_text("dict")` for per-word position/size, margin filtering, region drop/
  heading/equation typing from the layout model's boxes (no font/stroke heuristics —
  see `layout_model.py`), paragraph reconstruction, and sentence splitting via
  **spaCy** (pysbd then regex as fallbacks). Keep it **format-agnostic** — it must
  work across many paper layouts, so do not add rules that fit one paper. Validate
  changes against several papers, not one (the `eval/` harness scores section-title
  recall against arXiv `.tex` ground truth).
- **`app/layout_model.py`** is the REQUIRED region detector (DocLayout-YOLO), a base
  dependency. It types each page's regions (title/body/figure/table/formula/
  furniture) from a rendered image, and `pdf_utils` trusts those boxes alone: a line
  is a heading only inside a `title` box, a display-equation line only inside a
  `formula` box, and a word is dropped only inside a `figure`/`table`/`furniture`
  box (a `body`/`title`/`caption` box overrides an overlapping drop box, so a table
  that bleeds into the next column does not eat it). There is NO heuristic fallback:
  region typing came in two flavours before and the font/stroke heuristics overfit a
  single paper's layout (a full-width false-positive band once swallowed a whole
  column of body text and a section heading), so they were removed. The model is
  loaded at startup; if it can't load, startup FAILS with a clear message (see
  `app/main.py`) instead of serving a degraded parser. The text and word coordinates
  still come from PyMuPDF, so **highlighting is unchanged** — the model only says
  WHAT each area is, never replaces the text layer. Install gotcha: torch and
  torchvision must come from the SAME index (mixing a CPU torch with a default
  torchvision fails at import with `torchvision::nms does not exist`).
- **`app/main.py`** is FastAPI: upload/library, `/api/layout`, streaming
  translation, summarize, anatomy, chat. **`web/`** is plain JS + pdf.js + KaTeX,
  no build step. Highlighting maps server word coordinates (PDF points) to the
  viewport, so highlights are sentence-accurate.
- **`app/store.py`** persists uploads + cached AI results under `data/`.

## Security (do not weaken)

- Never hardcode an API key, token, or password anywhere — including tests and
  comments. This app needs no API key; it uses the subscription CLI.
- PDF text is **untrusted input**. It is framed as data in prompts and the CLI is
  run with tools disabled (`--allowed-tools ""`). Keep both. Pass text on stdin,
  never interpolated into a shell command.

## Conventions

- Python: **uv** + **ruff**, `line-length = 88` (team standard). CI runs
  `ruff format --check .` and `ruff check .`. Run `uvx ruff format .` before you
  commit; keep `uvx ruff check .` clean.
- Do **not** commit `data/` or `papers/` (gitignored: uploaded PDFs + cache).
- Commit messages: imperative, subject under 72 chars, no emoji. Co-author
  trailer: `Co-authored-by: Claude Code <noreply@anthropic.com>`.

## Run it

```bash
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8743
```

Requires Claude Code (`claude`, signed in) or Hermes (`hermes setup`). Pick one in the
**AI** badge; `PAPER_READER_BACKEND=claude|hermes` overrides it for one run.

## AI Backends

- `app/ai.py` builds every prompt and picks the Model for each Task from `app/settings.py`.
  Nothing outside `app/backends/` starts an AI process.
- Requests that carry PDF text or images run with every agent tool disabled (ADR 0002).
  Equation images go inline in the message; never enable a file-read tool for them.
- Never read or pass an API key. The Claude Code env drops `ANTHROPIC_API_KEY`; Hermes keeps the
  user's Provider untouched.
- Errors shown to users come from `app/errors.py`; never put stderr or provider bodies in them.

### Adding an AI Backend (OpenClaw is next)

1. Add `app/backends/<name>.py` exposing `NAME`, `LABEL`, `MODEL_SUGGESTIONS`, `installed()`,
   `describe()`, `async run(prompt, *, model, system=None, images=())`, and
   `stream(prompt, *, model, system=None)`.
2. Disable every tool in that transport and send images inline.
3. Map its errors with `app.errors.from_text` so rate limits become `RateLimitError`.
4. Register it in `app/backends/__init__.py` and add its name and default Models to
   `app/settings.py` (`BACKEND_NAMES`, `DEFAULT_MODELS`).
5. Add `tests/test_backend_<name>.py` mirroring `tests/test_backend_hermes.py`.

## Behavior notes (so you don't "fix" them by mistake)

- **Translation is paragraph-parallel but displays STRICTLY in order.** The unit of work
  is a **paragraph batch**, not a page: `paragraphBatches()` groups a page's sentence-
  segments by paragraph (coalescing tiny ones to a minimum size so a lone heading does not
  cost its own call). `translateAll()` (in `web/app.js`) runs a producer that walks pages
  in order — laying each out, building its empty DOM (`preparePage`), and enqueuing its
  batches — while a pool of `trPool()` workers (the AI Backend's concurrency setting, default 8) translates batches concurrently,
  so even a single page keeps the pool busy. Display is gated by a sentence-level frontier:
  every result lands in a per-page buffer (`P.trBuf`), and `paintFrontier()` paints only the
  contiguous run of already-buffered sentences from `S.frontier`/`P.frontK`, stopping at the
  first gap — so a later paragraph that finishes first stays buffered and no sentence ever
  appears before an earlier one. Alignment is by ARRIVAL ORDER, not by the number the model
  prints: `ai.translate_stream` yields one translation per segment strictly in order and raises
  `AIError` if the model drifts (an out-of-order index, or too many/too few lines — a merged or
  renumbered line would otherwise shift every later segment), and the server numbers results by
  arrival (`{"i": len(collected)}`). A drifted stream therefore errors instead of being shown or
  cached. A failed batch falls back to the source text for its range so it cannot stall the
  frontier. Every AI call is bounded by a per-backend semaphore in `app/ai.py`
  sized by the same concurrency setting, which also covers summary/anatomy. A rate limit
  (`RateLimitError`, HTTP 429, or a stream line with `rate_limited`) stops the remaining
  batches and leaves source text; the user lowers concurrency in Settings if it repeats. The server stays generic (it
  translates whatever segment list it is given and caches by a signature of those segments,
  so each batch caches independently). On open, the loader reveals the UI as soon as the
  FIRST translated sentence is painted (`S.onFirstPaint`) and a few pages of layout are
  buffered (`waitLayout`) — it does not wait for the whole first page or the whole document;
  translation and layout prewarm keep running in the background after the reveal.
- **Each Task has its own model** (`DEFAULT_MODELS` in `app/settings.py`). Claude Code uses
  `sonnet` for every task (it follows the per-segment translation contract far more reliably
  than haiku). Hermes uses `gpt-6-luna` for translation, equations, and the glossary, and
  `gpt-6.1-sol` for the rest. NOTE: the bare `haiku` alias in the Claude CLI still resolves to
  Haiku 4.5, so if you ever set a Haiku model use the explicit `claude-haiku-5-5` id.
- **A per-paper translation glossary ("brief") is injected into every page.**
  `translate_brief()` reads just the front matter (`brief_source()`: title +
  abstract + intro) on the glossary Task's fast model and returns the paper's field,
  defined abbreviations, and key terms with a preferred target-language rendering.
  It is generated once on the first page's translation, cached as `brief_<lang>`,
  and prepended (as reference, not content) to each page so terminology stays
  consistent across pages. Translation itself stays on the user's chosen model.
  Keep the brief model cheap and the source short — it must not slow translation.
- **Display equations are re-read from the image.** The PDF text layer scrambles 2D
  math, so `/api/math` crops each equation region (`pdf_utils.crop_region_png`) and
  a vision call (`ai.equation_latex`) transcribes it to clean LaTeX; the translation
  panel then swaps the scrambled text for `\[ ... \]`. The PNG goes inline in the
  message with every tool disabled (ADR 0002) — never enable a file-read tool for it.
  Cached per page/backend/model; best-effort (keeps the original text on
  failure). A line is a display-equation line only when its center is inside a model
  `formula` box; consecutive such lines (one box spans the whole equation) group into
  one segment, which gives a whole-equation crop.
- Results are cached per paper/model under `data/`, so reopening is instant. The
  layout cache key carries `_LAYOUT_VERSION` (in `main.py`) — bump it when `layout()`
  changes what it emits, and old cached pages are bypassed instead of silently
  reused. Translation caches on a signature of the exact segments, so it follows a
  layout change automatically. Wipe `data/` only when you want a clean rebuild.
- **Running headers/footers are dropped.** `running_headers()` finds lines that
  repeat at the top/bottom across pages (title, venue, 'A PREPRINT', author names)
  and `layout()` removes them, so they are not translated. The threshold is a
  third of the sampled pages, because headers often alternate between even/odd
  pages — do not raise it back to a half.
- **Figure- and table-internal text is dropped.** A word is dropped when its center
  is inside a model `figure`/`table`/`furniture` box. There are no stroke/font-size
  heuristics any more (they overfit one paper). A `body`/`title`/`caption` box
  overrides an overlapping drop box, so a table/figure that bleeds into the adjacent
  column does not eat its neighbour's prose; captions (a `caption` box) stay
  translatable.
- **Sentences that wrap across a page break are stitched whole.** `layout()` keeps
  such a sentence on the page where it STARTS: that page absorbs the next page's
  continuation fragment (its first lowercase-starting sentence, so a float caption
  between the pages is skipped) and records its geometry under `words_next` for
  cross-page highlighting; the next page drops that fragment. Both pages find the
  continuation by the same rule, so they agree. `_page_layout()` is the per-page
  core; `layout()` is the stitching wrapper that also peeks at the neighbours.
- **End matter is not translated, but the appendix is.** `end_matter_range()`
  finds the References/Acknowledgements/Funding span (page 0 is excluded so an
  author-funding footnote never triggers it) and returns a (start, end) range that
  stops at the first appendix/supplementary heading after it. `layout()` drops
  words inside the range; an appendix that follows the references is outside it and
  is translated normally. This is why the skip is a *range*, not just a start.
- **Heading detection is the model's `title` boxes.** A line is a heading only when
  its center is inside a `title` box (no font-size/bold/numbered-pattern heuristics).
  One reading-order fix survives because it is not region typing: a lone section
  number typeset as its own element ("2.1" on its own line) is merged into the
  title-like line that follows and the pair is marked a heading, since a `title` box
  often omits the detached number.
- **Line-break hyphens are rejoined.** `_join_words()` turns 'to-' + 'kens' (split
  across a line) into 'tokens' for the sentence text, while keeping both word
  boxes for highlighting. Suspended hyphens ('code- and reasoning-related') are
  left alone because the next word is on the same line.
- Known limits: scanned/image-only PDFs are unsupported (no OCR); the compiled PDF
  has no LaTeX source, so math is reconstructed by the model, not parsed. Cross-page
  sentence stitching only looks at the IMMEDIATE next page — a sentence whose
  continuation is pushed two or more pages away by a full page of figures/tables is
  not stitched (rare; a correct multi-page version would need both the forward and
  backward passes to agree on how many float-only pages to skip). A layout model
  whose `title` detection fails on an unusual typography (e.g. an old single-column
  monograph) yields few headings; a font/number heuristic supplement was measured
  against arXiv `.tex` and REVERTED because it cost ~10 points of precision for
  almost no recall — so such papers keep flowing their headings as body text.
