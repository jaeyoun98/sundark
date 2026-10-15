# Landing film authoring

This is an isolated Remotion 4.0.535 project. Its dependencies are not application or website dependencies. The website uses ordinary MP4 video, with no Remotion Player or React runtime. This project renders the three actual Sundark recordings of *Attention Is All You Need* into reader-focused landing films.

## Install and restore inputs

Use Node.js 22 or later and pnpm 12.11.2. From this directory:

```sh
pnpm install --frozen-lockfile
mkdir -p public/inputs
gh release download landing-media-2026-10-15-v1 --repo jmy-org/sundark --pattern 'source-*.mp4' --dir public/inputs
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

manifest = json.loads(Path('input-manifest.json').read_text())
for item in manifest['inputs']:
    path = Path('public/inputs') / item['file']
    assert path.stat().st_size == item['bytes'], item['file']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], item['file']
    print('Verified', item['file'])
PY
```

The source MP4s are unzoomed, timestamp-faithful assemblies of the original real browser captures. Their immutable URLs, exact sizes and hashes are in `input-manifest.json`; the original JPEG names/timestamps/hashes are recorded in `capture-manifest.json` for provenance. The released MP4s are sufficient to re-render; the temporary recording machine is not needed. Do not publish into the same media tag with replacement bytes: create a new version and update both manifests instead.

## Preview and render

```sh
pnpm studio
pnpm check
pnpm render:translation
pnpm render:anatomy
pnpm render:chat
pnpm exec remotion still src/index.tsx Translation out/translation.jpg --frame=270 --image-format=jpeg --jpeg-quality=90
pnpm exec remotion still src/index.tsx Anatomy out/anatomy.jpg --frame=270 --image-format=jpeg --jpeg-quality=90
pnpm exec remotion still src/index.tsx Chat out/chat.jpg --frame=360 --image-format=jpeg --jpeg-quality=90
```

Rendering uses H.264, CRF 26, 1600×1000, 30 fps, yuv420p, no audio and a faststart header. Two rendering workers keep memory and temporary storage bounded. The first render downloads Remotion's supported Chrome Headless Shell. No cloud renderer, paid API, account login or AI request is part of the rendering process. Check Remotion's current license before using this authoring project for a different organization or team size: <https://www.remotion.dev/license>.

Only copy approved final MP4/JPG exports into `../../site/assets/demos/`; that destination and the source/input/render/dependency caches are ignored by Git. The coordinator publishes binaries into the dedicated media release. Pages downloads only the six delivery files and verifies hashes; the three source MP4s are authoring-only release attachments.

## Design and source integrity

Palette: paper white `#ffffff`, canvas `#f4f6ff`, pale blue `#e9eeff`, ink `#182141`, brand blue `#243dbe`. A gentle spring settles a desktop frame into the stage. After the real action, two close-up crops isolate the relevant native paper and product panes; an eased return restores the whole reading context. A drawn connector communicates their relationship without adding a localized headline. Every pane uses the same original capture time. It never recreates UI, equations, quotes, answers, pointer actions or highlights.

The source durations are preserved: translation 18 seconds; Anatomy 18 seconds; chat 22.5 seconds. Translation preserves the verified selection through 4.243 seconds, then holds the genuine selected frame. Anatomy preserves the real Main Claim quotation click and source navigation. Chat preserves the real question submission, then its original cut at 3.5 seconds to the unchanged generated answer. The removed wait is documented in `../../site/assets/demos/transcripts.md` and provenance; the film does not claim real-time AI latency.

The small Sundark brand mark is the only editorial lettering. Korean/English benefit captions remain separate WebVTT tracks in `site/assets/demos/` and can be changed or localized without re-rendering. Native app text is part of the recording. Keep captions on the host video player rather than burning them into the composition.

## Reference

- Official agent guidance: <https://github.com/remotion-dev/skills>
- Frame-driven animation: <https://www.remotion.dev/docs/animating-properties>
- Spring: <https://www.remotion.dev/docs/spring>
- Exact source video: <https://www.remotion.dev/docs/offthreadvideo>
- CLI rendering: <https://www.remotion.dev/docs/cli/render>
