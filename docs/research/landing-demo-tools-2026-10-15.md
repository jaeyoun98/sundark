# Landing demo video tooling

Verified against official sources on 2026-10-15. No independent market-share
ranking was established; vendor adoption statements are not a ranking. The
executed recording workflow and its evidence are documented below.

## Comparison

| Tool                                                                 | Verified capability                                                                                                                                                                                                                            | Fit and caveat                                                                                                                                                                                                                                                                                                                               |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Remotion Agent Skills](https://www.remotion.dev/docs/ai/skills)     | Official coding-agent guidance covers composition creation, animation, captions, preview and rendering.                                                                                                                                        | Strong for repeatable, frame-controlled editing of an actual recording. It does not establish that the recorded app works. Check the [license](https://www.remotion.dev/license) for the actual organization/use; do not assume unrestricted use.                                                                                            |
| [Playwright Video](https://playwright.dev/docs/api/class-video)      | A browser context created with `recordVideo` produces per-page video; closing the context guarantees the file is written.                                                                                                                      | Strong for reproducible web-app capture. It is a recorder, not an automatic cinematic editor. Set explicit viewport/video dimensions and retain authentic interaction results.                                                                                                                                                               |
| [Tella MCP](https://www.tella.com/mcp)                               | Agents can inspect transcripts, edit clips, add zooms and export. Its [September 30 workflow](https://www.tella.com/blog/automate-demo-polish-with-mcp) documents `generate_auto_zooms`, `add_zoom` and preview checks.                        | Good integrated agent editing after account authorization. [Auto Zoom](https://www.tella.com/features/zoom) uses captured clicks/cursor data; uploaded videos without that data support manual zooms. Do not assume a third-party recording gains automatic click tracking.                                                                  |
| [Arcade MCP](https://www.arcade.software/lp/mcp)                     | Prompt-driven branded product videos, generated scenes and editing, with an authorized Arcade account.                                                                                                                                         | Useful for marketing narratives and interactive demos. Generated visuals must not be represented as actual app execution. The vendor reports more than 30,000 companies; this is vendor-reported adoption. Export/plan availability needs checking before committing to that workflow.                                                       |
| [Supademo MCP](https://supademo.com/blog/product-update-april-recap) | Natural-language creation/customization/management of demo content. A [September engineering article](https://supademo.com/blog/designing-mcp-server-100-tools) describes demo editing, voiceover generation, translation and analytics tools. | Stronger fit for an interactive walkthrough library than for this small set of self-hosted videos. Account authorization and actual tool/plan access need checking. The older [announcement](https://supademo.com/blog/product-update-mcp-server) still uses “upcoming” wording; later dated updates provide stronger availability evidence. |
| [Screen Studio](https://screen.studio/guide/auto-zoom)               | Auto zoom targets recorded clicks; manual zoom selects a target where there is no click. Its [official product page](https://screen.studio/) also advertises cursor smoothing and animated zooms.                                              | Good visual reference for polished macOS recordings. Official pages reviewed here do not establish an agent API/CLI. `screen.studio` is the source used; similarly named sites are not interchangeable.                                                                                                                                      |

## Recommended production route

Capture the running Sundark app with Playwright, using actual app-generated
translation, Research Anatomy and chat results for _Attention Is All You Need_.
Keep recording provenance separate from the edited deliverables. Cached real AI
results can make a repeatable capture. Document cache use and timing edits in
production provenance; never present edited timing as live generation speed.

Use controlled smooth zooms to guide the eye from the whole reader to a sentence,
a cited passage or an answer. Hold long enough to read, then return to the wider
context. Avoid continuous camera movement, exaggerated magnification and crops
that hide the result or the action that caused it.

The local runtime check found `ffmpeg`, `node` and `pnpm` on PATH. For bounded
cuts, scaling and easing, the installed FFmpeg is a practical first choice;
its [official filter documentation](https://www.ffmpeg.org/ffmpeg-filters.html#zoompan)
documents expression-controlled zoom/pan. Remotion is a suitable alternative when
a reusable composition or richer animated typography justifies its project and
license requirements. Neither route requires adding a build step to Sundark's
plain-JavaScript web app. The renderer choice does not change the requirement to
verify saved files and inspect rendered frames before claiming completion.

## Executed workflow

The actual capture used the browser tool's supported Chrome CDP capability,
rather than Playwright's `recordVideo`: screencast frames for the translation
interaction and chat submission, and timestamped screenshot sampling for Anatomy
and answer reading. FFmpeg rendered the actual frames with eased zoom, pan and
return. This avoids an additional recording account or frontend build step.

The deliverables are translation (18 seconds), Research Anatomy (18 seconds),
and chat (22.5 seconds), each at 1600 × 1000, H.264, 30 fps, without audio.
Korean and English captions are separate WebVTT files selected through native
video controls. No editorial captions are burned into the video. The recorded
app UI remains Korean. Chat waiting time is cut; preparation and timing edits
are documented in production provenance. At the user's direction, production
notes are kept out of promotional copy and captions. A real selected translation
frame is held for reading; these are demonstrations of actual results and
navigation, not measurements of live generation speed.

Anatomy preparation initially reached the app's unchanged 300-second timeout.
The pending request metadata showed maximum effort and a 128,000-token output
ceiling; it did not establish the cause of the timeout. Supported, isolated
runtime settings `CLAUDE_CODE_EFFORT_LEVEL=low` and
`CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192` then took effect, verified in request metadata.
The app's normal JSON-repair fallback returned a complete result, and its quote
verifier matched the filmed quotation. Application source, prompts, disabled
tools, timeout, and user global settings were unchanged. These are documented
[Claude Code environment options](https://code.claude.com/docs/en/env-vars), not
a product-source fix for slow default requests.

See the [recording provenance and asset hashes](../../site/assets/demos/provenance.json)
and [scene transcripts](../../site/assets/demos/transcripts.md). The recordings
show the desktop web app, not a physical mobile device. Raw frames, rejected
probes and production commands are retained outside the public site in the
temporary capture workspace.

## Final verification

| Gate                           | Evidence and result                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Actual app behavior            | Captured sentence/source highlighting, verified Anatomy quote navigation and the genuine chat submission/answer. No app source, DOM content or responses were replaced.                                                                                                                                                                                    |
| Encoded files                  | All three MP4 files fully decoded with FFmpeg. FFprobe confirmed 1600 × 1000 H.264, 30 fps, no audio, and durations 18 / 18 / 22.5 seconds.                                                                                                                                                                                                                |
| Asset integrity and delivery   | Independently recomputed 13 manifest asset hashes. All 14 page-referenced assets returned HTTP 200 under `/sundark/` with matching sizes and appropriate media MIME types.                                                                                                                                                                                 |
| Separate multilingual captions | Both caption languages parsed on all three native players, with 4 / 4 / 6 matching cues per language. Native-menu Korean-to-English switching worked. With captions disabled at chat time 4.537 seconds, no editorial text was visible in the base video.                                                                                                  |
| Playback and seeking           | Actual native play, pause and time progression worked for all three clips. Native timeline seeking worked; the preview was changed to Starlette StaticFiles so byte-range requests return HTTP 206. The basic Python server's missing range support had limited seeking before full download.                                                              |
| Layout and keyboard access     | Desktop 1440 × 1000 and mobile-width 390 × 844 had no horizontal overflow. Videos stacked at mobile width. Skip-link activation and all scene-description disclosures worked by keyboard. No initial MP4 requests or autoplay were observed.                                                                                                               |
| Source review                  | A non-Orca Claude Opus one-shot reviewed HTML/CSS/copy, and a closure review checked the fixes. CTA and figcaption-order issues were fixed. The user then explicitly directed production notes out of promotional copy; those details remain in provenance and production transcripts. Codex performed capture, integration and browser/file verification. |
| Fullscreen limitation          | The connected-browser native fullscreen control did not enter fullscreen during automation. Fullscreen was enabled and the tab visible/focused, but transient user activation was false after the attempt. This does not establish the cause or a product defect; fullscreen acceptance remains unverified.                                                |
| Other limits                   | No physical-mobile, Safari or VoiceOver acceptance was performed. No push, PR or public deployment was requested or performed.                                                                                                                                                                                                                             |

## Landing placement and access

These are design recommendations, not measured conversion claims:

- Put one representative short, silent product clip near the hero. Start from a
  clear poster and an explicit play control; do not autoplay.
- Put focused feature demos next to the related explanation: parallel translation
  and linked highlighting, Research Anatomy to source evidence, and paper-aware
  chat. Each should show one outcome instead of a long tour of menus.
- Preserve native playback controls, keyboard access, captions/descriptive text,
  a readable transcript and an accessible name. Silent screen recordings still
  need text that conveys actions and visual results. Keep meaningful copy outside
  the video so pausing or skipping it loses no essential explanation.
- Self-host compact MP4 files with posters, deferred loading and no unnecessary
  initial download. Verify mobile sizing, native playback and the actual encoded
  output. Provide stable layout dimensions to avoid page movement while loading.

## Paper content and attribution

Source: Vaswani et al., [Attention Is All You Need, arXiv:1706.03762](https://arxiv.org/abs/1706.03762).
Use a bibliographic attribution and direct paper link alongside the demo.

For the chat demo, a factual oracle is Section 3.2.1 and Equation (1):
`Attention(Q,K,V) = softmax(QK^T / sqrt(d_k)) V`. Scaling divides the dot-product
scores by `sqrt(d_k)` **before** softmax. The paper explains that large dot products
can push softmax into regions with very small gradients; scaling counteracts that
effect. A response that scales after softmax or divides by `d_k` is incorrect.
See the [primary paper text](https://arxiv.org/html/1706.03762v7#S3.SS2.SSS1).

The paper includes a specific permission notice for reproducing its tables and
figures in journalistic or scholarly works. That notice is not a blanket marketing
license. Keep attribution and publication-rights review separate from technical
video validation; do not infer unrestricted rights from public arXiv access.

## Remotion production and release delivery

The initial FFmpeg-only exports above were superseded by an isolated Remotion
4.0.535 authoring project in `tools/landing-video/`. The compositions preserve real
capture timing and native UI, then move from a whole-reader view into paired
close-ups of the relevant paper and product panes. The frame-driven entrance,
eased camera move, paired cards, connector and return are rendered into MP4;
there is no Remotion/React runtime in the website. Korean and English editorial
captions remain separate WebVTT tracks.

Heavy binaries are excluded from Git. A dedicated versioned GitHub media
prerelease holds six delivery files and three unzoomed authoring inputs. Pages
uses the standard-library `scripts/fetch_landing_media.py` helper to download only
the six delivery files, verifies pinned byte counts and SHA-256 hashes from
`site/media-manifest.json`, and uploads the resulting static site. The reader's
Python dependencies, application source and installation path are unchanged.
Authoring dependencies and caches remain outside the application dependency graph.

This follows GitHub's supported [release asset delivery](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
and Remotion's [frame-driven animation](https://www.remotion.dev/docs/animating-properties)
and [CLI export](https://www.remotion.dev/docs/cli/render) model. It avoids a separate
media hosting account for this small static site. A later CDN migration can keep
the same static player interface if measured traffic warrants it.

Final Remotion delivery checks are separate from the earlier FFmpeg browser run.
All final clips are checked by full H.264 decoding, metadata, faststart position
and representative rendered frames. The five release-helper unit tests and
scoped Ruff checks pass. A non-Orca Claude Opus source-only review and an
independent Codex delivery review found no correctness/security blockers. The
Opus missing-context hygiene questions were checked against the authoring-local
ignore rules and formatter config; the stdlib-only tests generate their own temp
files and do not require ignored media. A full application regression suite was
not run for this isolated media change.

The final-media browser executor could not complete navigation through the
Chrome extension (30-second and 60-second timeouts), despite the preview responding
HTTP 200. Real Chrome native-control interaction remains unverified; earlier browser results do
not transfer to these exact exports. A subsequent isolated headless check is recorded below. No physical-mobile, Safari, fullscreen or VoiceOver acceptance
is claimed.

Published media-only prerelease:
[landing-media-2026-10-15-v1](https://github.com/jmy-org/sundark/releases/tag/landing-media-2026-10-15-v1).
Its nine public assets were downloaded into fresh temporary directories without
GitHub authentication, and every byte count and SHA-256 matched the delivery/input
manifests. Final MP4 total: 7,972,369 bytes; posters: 589,080 bytes; delivery total:
8,561,449 bytes. Authoring inputs total: 2,979,512 bytes. The release targets
existing remote commit `9e9eaf82a03920c48bcc13e1c9644cbb7343fdca`, is a prerelease,
and does not change the latest application release. At media publication, source
and workflow changes were still local. The user subsequently authorized PR #15
and squash merge; the final headless checks below were completed before merge.

### Final isolated Chromium verification

After the extension connection failed, a delegated executor used Remotion's
existing Chrome Headless Shell with a temporary profile, leaving the user's
Chrome profile untouched. All three exact final files played with advancing time,
paused without advancing, and sought to nine seconds. Both Korean and English
tracks parsed four cues per film and displayed the expected active cue; disabling
tracks removed the editorial overlay. Captions-on/off screenshots were checked.
With cache disabled, initial MP4 request count was zero. Desktop 1440 × 1000 and
mobile 390 × 844 had no horizontal overflow.

These are headless media API/CDP results, not native-control click, fullscreen,
Safari, physical-mobile or assistive-technology acceptance. Local evidence:
`/tmp/sundark-headless-final-qa.json` and `/tmp/sundark-headless-final-*.png`.
The PR's Linux regression and Windows credential checks also passed.
