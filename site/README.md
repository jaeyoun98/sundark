# Sundark landing page

The public product introduction is separate from the local reader in `web/`.
This directory contains only public, static files. It does not run the reader or
accept papers, credentials, or AI requests.

## Structure

```text
site/
  index.html          Korean product copy, recorded demos, installation, FAQ
  assets/
    styles.css        Responsive layout, focus styles, reduced-motion support
    favicon.svg       Brand mark
    demos/            MP4 recordings, JPEG posters, Korean and English WebVTT captions
  README.md           Maintenance and delivery instructions
```

The page presents three recordings of the local reader using _Attention Is All
You Need_: paired translation highlights, Research Anatomy with source navigation,
and paper-aware chat. The hero places the translation recording across the full content width so the
reader remains legible. On wide screens the introduction uses two columns to
bring the recording closer to the top; the other two
appear with the reading features. These are recorded interactions, not an embedded
reader or a promise about AI response time.

There is no JavaScript or frontend framework in the published page. Pages fetches pinned release media before uploading the static artifact; visitors receive those files from the same site origin.
Use relative asset paths so the site works under the `/sundark/` project prefix.
Keep installation commands aligned with the root README.

## Video maintenance

Each scene in `assets/demos/` has a matching `.mp4`, `.jpg` poster, and separate `.ko.vtt` and `.en.vtt`
caption tracks, named `translation`, `anatomy`, or `chat`. Keep those paths aligned
with the HTML. The players reserve an 8:5 frame, use native controls and inline
playback, and load video data only when requested (`preload="none"`). Do not add
autoplay or looping: visitors choose when motion starts. Posters provide a static
preview before playback. Korean captions are selected by default; visitors can select English or turn
captions off using the native player. Do not burn editorial captions into the
video: language choice belongs to the separate WebVTT tracks. The Korean reader
UI remains part of the real recording; selectable captions do not translate the
website or the recorded UI. Each figure provides a collapsed scene description
with a direct video download.

When replacing a recording, update its poster, caption timings, and on-page scene
description together. Verify that all visible interactions and statements match
the actual recording. Keep source text legible during zooms and avoid abrupt
camera movement. Capture real reader behavior with actual AI results; do not
stage generated answers as live output. Do not include credentials or private
papers in public media.

## Preview

From the repository root, restore the six media files (Python standard library only), then serve:

```sh
python3 scripts/fetch_landing_media.py
python3 -m http.server 8744 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8744`. Check narrow and wide viewports, keyboard navigation,
the skip link, FAQ disclosure controls, and installation links before publishing.
For each video, check poster loading, playback, pause, seeking, fullscreen, Korean and English
caption selection, keyboard focus, download links, and the scene-description disclosure.
Check the same assets under a `/sundark/` URL prefix. A successful source check is
not a substitute for playing all three final MP4 files in a browser.

The basic Python server may not support HTTP byte ranges, which can prevent
seeking until a video is fully downloaded. For playback verification with byte
ranges and the project prefix, use the existing app dependencies after `uv sync`:

```sh
uv run python - <<'PY'
from pathlib import Path
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
import uvicorn

app = Starlette(routes=[Mount("/sundark", app=StaticFiles(directory=Path("site").resolve(), html=True))])
uvicorn.run(app, host="127.0.0.1", port=8744)
PY
```

Open `http://127.0.0.1:8744/sundark/`. A byte-range request to an MP4 should return
`206 Partial Content`, rather than the entire file. Stop the basic server before
starting this one on the same port.

## GitHub Pages

Heavy MP4/JPEG files are ignored by Git and stored in the dedicated `landing-media-2026-10-15-v1` GitHub prerelease. `site/media-manifest.json` pins the six delivery files by release tag, byte count and SHA-256. The fetch helper verifies every file and fails the build on a mismatch. Authoring source MP4s are separate release attachments and are not downloaded for Pages. Remotion source and its isolated lockfile live in `tools/landing-video/`; its dependencies are needed only to author films, never to install the reader or serve the page.

To replace media, publish a new versioned release and update the manifest; do not replace bytes under an existing tag.

`.github/workflows/pages.yml` downloads and verifies the media, then uploads only `site/` using GitHub's official Pages
artifact and deployment actions. Pushes to `main` that change the site or workflow
trigger deployment; manual dispatch is also available. Enable **GitHub Actions**
as the publishing source in the repository's **Settings > Pages** first.

The expected project URL is `https://jmy-org.github.io/sundark/`; a successful
workflow and an HTTP check are required before treating it as published.
The deployment does not publish `app/`, `web/`, `data/`, or `papers/`.
To roll back, restore the previous site revision on `main` and let Pages redeploy.
