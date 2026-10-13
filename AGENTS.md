# AGENTS.md — rules for anyone (human or AI) changing this repo

Read this before editing. It captures the non-obvious decisions that keep this
project working. Keep changes surgical and in the existing style.

## What this is

A personal, local web app that reads conference/journal PDFs and provides
whole-document translation, a grounded "Research Anatomy", an auto summary, and
paper-aware Q&A chat. It runs through the user's own subscription in the chosen AI
Backend (Claude Code, Hermes, or ChatGPT) — never a billable API key.

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
  `get_text("rawdict")` for per-word position/size, margin filtering, region drop/
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
  `formula` box, and a line is dropped only when its first word is inside a
  `figure`/`table` box (a `body`/`title`/`caption` box overrides an
  overlapping drop box, so a table that bleeds into the next column does not eat
  it). A `furniture` box drops only running heads, page numbers and sideways stamps
  (see *Running headers*). There is NO heuristic fallback:
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
  comments. This app needs no API key; it uses subscription CLI or ChatGPT OAuth access.
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

Requires Claude Code (`claude`, signed in), Hermes (`hermes setup`), or ChatGPT login. Pick one in the
**AI** badge; `SUNDARK_BACKEND=claude|hermes|chatgpt` overrides it for one run.

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
  `gpt-6.1-sol` for the rest. ChatGPT uses the same task defaults, with account-specific
  choices fetched after login. NOTE: the bare `haiku` alias in the Claude CLI still resolves to
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
- **When in doubt, translate.** Text translated that need not be costs little; text left
  untranslated is lost to the reader. Every drop rule below is narrowed to what is surely
  not content, and was checked against the PDF text of 31 papers (every word that reaches
  no sentence, by reason).
- **Running headers/footers are dropped.** `running_headers()` finds lines that
  repeat at the top/bottom across pages (title, venue, 'A PREPRINT', author names)
  and `layout()` removes them, so they are not translated. The threshold is a
  third of the sampled pages, because headers often alternate between even/odd
  pages — do not raise it back to a half. Lines match by their letters only
  (`_norm_header`): the page number moves ("1:2 • Kerbl et al." vs "1:4" and "• Kerbl et
  al." as two lines). A repeated line goes in the top 12% / bottom 10% of the page (where
  it is also counted) or inside a model `furniture` box. Not by height alone: the
  model's furniture ("abandon") class also holds footnotes and now and then a heading it
  mistook ("D. Combining Lhybrid and Lvlb Models"), so a furniture box drops only
  repeated lines, bare page numbers ("12", "1:4", "xii", a lone symbol) and text set
  sideways (the arXiv stamp along the first page's edge).
- **Footnotes and figure/table captions come after the page's text.** Footnotes (the rest
  of a furniture box) and captions are *asides*: their paragraphs are built apart and
  placed at the end of the page, captions first, flagged `aside`. A caption is a model
  `figure_caption`/`table_caption` box, taken as its whole PDF block (a box over the first
  line only split 2506.09985's captions), or a caption rescued from a table box (below); an
  equation number such as "(5.7)" is a model caption too (`formula_caption`) and stays in
  place, or lead-ins lose it and adjacent equations merge. In reading order an aside sits
  between the halves of a sentence that runs from the foot of one column to the top of the
  next: a footnote split it (1512.03385 p2), and a caption label stuck to it ("... the
  galaxy auto- Fig. 20."); placing them after the text made 75 sentences whole over 31
  papers. Captions never join footnotes, and a line opening with a caption label starts a
  new caption. Asides also hid the page's real last sentence from cross-page stitching, so
  `_tail_idx` skips them.
- **Figure- and table-internal text is dropped.** A PDF line is dropped when the center
  of its first word is inside a model `figure`/`table` box. There are no
  stroke/font-size heuristics any more (they overfit one paper). A
  `body`/`title`/`caption` box overrides an overlapping drop box, so a table/figure
  that bleeds into the adjacent column does not eat its neighbour's prose; captions (a
  `caption` box) stay translatable. Deciding by the first word means a box that covers
  only PART of a line takes all of it or none of it, so a box edge over a caption or
  footnote never leaves half a sentence ("Here the [results are on the COCO ...]"). A
  caption the box swallowed without a `caption` box is kept: a PDF block whose first line,
  or a line, opens with a caption label ("Table 5.", "TABLE V:", "Fig. 2", `_CAPTION_LABEL`).
- **Sentences that wrap across a page break are stitched whole.** `layout()` keeps
  such a sentence on the page where it STARTS: that page absorbs the next page's
  continuation fragment and records its geometry under `words_next` for cross-page
  highlighting; the next page drops that fragment (the browser maps those words back
  to the owning sentence, so hovering them highlights it on both pages). Both pages
  decide with the same rule (`_stitch_idx`), so they agree. The continuation is the
  first lowercase-starting sentence that opens its paragraph; sentences inside a
  model `caption` box are skipped and do not count toward the scan limit. A tail
  ending in a hyphenated word ("fea-" | "tures") is rejoined, and a tail left open on
  ")" or an acronym is stitched when the next page's first prose sentence starts with
  an author-year citation ("...(FiLM)" | "Perez et al. (2018) as well as ...").
  Known limit: when the real continuation starts uppercase ("the 34-layer" | "ResNet
  reduces ..."), the rule can pick a later lowercase paragraph instead.
  `_page_layout()` is the per-page core; `layout()` is the stitching wrapper that also
  peeks at the neighbours.
- **Over-split sentences are rejoined; list items are split.** spaCy breaks sentences
  mid-way: at an inline math operator ("...z" | "= (z_1, ..., z_n)"), a citation
  ("...(CNNs)" | "Ronneberger et al. (2015) ...", "VGG nets" | "[41] but"), a capital
  math symbol ("... σ 2" | "I is ..."), a parenthesis, closing punctuation
  (", Hamilton proved"), a line-broken compound ("LIBERO-" | "Long"), a spaced
  abbreviation ("et al ."), or inside a PDF word ("( 2020 );", which then belonged to
  two sentences and was highlighted with both), or right after a standalone math operator
  ("w_i / ∑" | "Nc j =1 ..."). `_mend_word_splits` and
  `_merge_fragments` rejoin those; the punctuation- and symbol-led rules apply only
  while the running text is not a finished sentence (`_ends_sentence`, which also
  accepts curly quotes, footnote marks and a spaced ". )"), so two real sentences are
  not merged. Enumeration runs ("a) ... b) ... c)", "(1) ..., (2) ...") are split into
  items by `_split_on_markers`; "Eqn.(1)" references and two-part subfigure phrases
  ("show (a) X and (b) Y") are left whole. Short prose that `_keep_sentence` would drop
  as a label (a lead-in before a display equation, "Then we have") is kept by
  paragraph context. These rules were measured on 25 papers with no word lost or
  duplicated. A sentence segmenter swap (Stanza, SaT) was measured and rejected: none
  reliably splits comma-joined lists, and both are much slower (Stanza ran 8x slower
  than spaCy on real per-paragraph calls).
- **Word boxes come from the glyphs.** `_words_with_style()` builds each word's box from its
  own glyphs (`rawdict`). Do not go back to splitting a span by character count: a justified
  line often comes as ONE span, the guess drifted up to ~20pt, and a sentence's first capital
  ("H" of "Here", "LSTM" of "LSTM-GMM") was highlighted with the previous sentence (791
  glyphs over 31 papers; 0 now). A word in the side margin is dropped only when its whole
  PDF line is there: on a narrow page a body line starts inside the 44pt margin. A line
  fragment that opens with a stacked subscript ("{ã^j}_{j=1}^{N}" + "are used") rejoins its
  line although it starts under the superscript (`_joins_line`). TeX's CMEX10 symbol font is
  decoded like MathPi (`_CMEX`): its text layer says "P" for "∑", "e" for a wide tilde, "("
  for a big "{". Only codes the font's own ToUnicode map leaves unmapped are remapped
  (`_cmex_tables`): pdfTeX's map covers just the tall-delimiter pieces, but a full map reads
  "(" as a real parenthesis, so a map's mere presence decides nothing.
- **Inline scripts are marked for the translator only.** A sentence with inline
  sub/superscripts also carries `tex` ("{ ˜ a ^{j} } ^{N neg} _{j = 1}"), and the browser
  sends `tex` for translation; `text` stays plain, because splitting, page stitching and the
  anatomy quote lookup match on it. A script is a smaller word whose baseline sits above or
  below its line's, ATTACHED to the glyph before it (`_script_roles`), so an inline fraction
  ("≜ β²₁/√β₂", after a gap) is not mistaken for scripts. Measured on 9 inline-math sentences,
  2 runs each: unmarked 31/34 expressions right ("ã_j" for "ã^j", "k^q_cmp"), marked 34/34.
- **Highlights follow lines, not glyph boxes.** `lineRects()` in `web/app.js` folds a
  script-size glyph into its line's rect instead of opening a second, overlapping
  rect, and attaches tall TeX math-font boxes (CMSY10/CMEX10, which PyMuPDF sizes from
  the font descender) by x-extent only, so a bar never grows over the next line;
  display equations keep the plain rule. The rects that still overlap (a script that
  sticks out, line boxes taller than the line spacing) paint ONCE: the rects are
  opaque and the highlight layer, which only ever holds one sentence, carries the
  translucency, so do not move the opacity back onto `.hlrect`. Text-layer spans are
  clipped to their word box: the fallback font draws wider than the PDF font, and a
  rotated side stamp ("arXiv ... 2026") would otherwise cover a whole column and
  steal its hover.
- **Zoom resizes the pages in place.** `setScale()` in `web/app.js` resizes each page
  shell and redraws it; it does NOT rebuild `S.pages`. A translation in progress paints
  through the page objects (`P.spanByK`, `P.trBuf`), and rebuilding them (as resizing the
  panel once did) left the rest of the translation blank. Canvases are drawn at
  `devicePixelRatio` (Retina) up to `MAX_CANVAS_PX`, and a page more than 3000px from the
  view gives its canvas and text layer back (`onPageFar`), so memory stays bounded at a
  high zoom. 100% is the paper's real size (96/72 CSS px per point).
- **The reference list is not translated; everything else is.** `end_matter_range()`
  returns the (start, end) range of the reference list, and `layout()` drops words
  inside it. Acknowledgements, funding and the like are translated.
  - It starts at a References heading (`_REF_RE`): the WHOLE line, capitalized, so a
    line of prose that starts with the word does not ("reference of 17,135 actions."
    cost 2610.02200 ten pages; a figure label "reference" cost 2610.10489 its Methods),
    and only if a reference list follows (`_BIB_RE`: a tenth of the lines or more carry
    a year, "[12]", arXiv/doi/a URL, "et al", "Proceedings", "pp. 12"; every reference
    page of 31 papers has a quarter or more).
  - It ends at the first appendix/supplementary heading after it, which must stand out
    from the list: its median size + 1pt (11pt always does; 3DGS sets "A" at 10pt over
    7pt references). It also ends at the top of the first page that is not a reference
    list, whatever that page's headings (the Transformer paper's "Attention
    Visualizations"), so a missed appendix heading costs at most the rest of one page.
  - The ends compare in READING order (`_before`): on a two-column page the whole left
    column is read before the right one, so a right-column heading does not drop the
    left column's foot (Diffusion Policy lost "Limitations and Future Work" that way),
    and a word takes its LINE's column, so a caption across both columns stays whole.
    Both ends allow 2pt: an appendix title split into lines ("A" + "Implementation
    Details") is kept whole.
- **Heading detection is the model's `title` boxes.** A line is a heading only when
  its center is inside a `title` box (no font-size/bold/numbered-pattern heuristics).
  One reading-order fix survives because it is not region typing: a lone section
  number typeset as its own element ("2.1" on its own line) is merged into the
  title-like line that follows and the pair is marked a heading, since a `title` box
  often omits the detached number. The lines of one `title` box are ONE heading, never
  cut into sentences: a wrapped title came out as two headings, and the splitter cut
  "A.1. Pre-training" after "A.1." (58 such headings over 31 papers). A section and its
  first subsection right below it have a box each, so they stay apart.
- **Page 0 drops its front matter, above the Abstract heading.** The heading is a word
  that opens its line and reads "Abstract"/"ABSTRACT", alone or followed by punctuation
  ("Abstract—We ...", `_ABSTRACT_RE`); "abstracting" in 2411.14499's introduction (which
  has no Abstract heading) cut away its abstract and introduction. Without the heading
  the cut is 22% down the page. A figure caption above the Abstract (SAM's teaser) is kept.
- **An appendix cover's copy of the front matter is dropped.** Page 0 drops everything
  above the Abstract (title, authors, affiliations; `_front_cut`). An appendix or
  supplement often opens on a cover that repeats those lines, and the model may type its
  author line as a title (2007.06559: four author "headings"). On a later page, a
  paragraph made only of lines that repeat page 0's front matter is dropped
  (`_front_matter`, compared by letters only, since the copies differ in spacing,
  affiliation digits and quotes). A cover title that adds words ("Appendix for ...")
  is not a pure copy and stays.
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
