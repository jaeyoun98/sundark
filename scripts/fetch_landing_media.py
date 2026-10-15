"""Fetch pinned release media for local previews and the static Pages build.

This standalone build helper deliberately has no application dependencies.
"""

import argparse
import hashlib
import json
import re
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "jmy-org/sundark"
FILES = {
    f"{clip}.{extension}"
    for clip in ("translation", "anatomy", "chat")
    for extension in ("mp4", "jpg")
}
MAX_BYTES = 50 * 1024 * 1024


def load_manifest(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported media manifest version")
    if manifest.get("repository") != REPOSITORY:
        raise ValueError("Unexpected release repository")
    tag = manifest.get("tag", "")
    if not re.fullmatch(r"landing-media-\d{4}-\d{2}-\d{2}-v[1-9]\d*", tag):
        raise ValueError("Expected a fixed landing-media release tag")
    assets = manifest.get("assets", [])
    if len(assets) != len(FILES) or {a.get("file") for a in assets} != FILES:
        raise ValueError("Expected exactly the six landing media assets")
    for asset in assets:
        if type(asset.get("bytes")) is not int or not (0 < asset["bytes"] <= MAX_BYTES):
            raise ValueError("Invalid media byte count")
        if not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", "")):
            raise ValueError("Invalid media SHA-256")
    return manifest


def matches(path, asset):
    return (
        path.is_file()
        and path.stat().st_size == asset["bytes"]
        and hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]
    )


def fetch_asset(url, destination, asset):
    """Keep an existing destination intact until its replacement is verified."""
    if matches(destination, asset):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".media-", delete=False
        ) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            count = 0
            request = urllib.request.Request(
                url, headers={"User-Agent": "sundark-landing-media"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                while chunk := response.read(64 * 1024):
                    count += len(chunk)
                    if count > asset["bytes"]:
                        raise ValueError(
                            f"Media exceeds expected size: {asset['file']}"
                        )
                    digest.update(chunk)
                    output.write(chunk)
            if count != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
                raise ValueError(f"Media integrity check failed: {asset['file']}")
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "site/media-manifest.json"
    )
    parser.add_argument("--destination", type=Path, default=ROOT / "site/assets/demos")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    base = f"https://github.com/{REPOSITORY}/releases/download/{manifest['tag']}"
    for asset in manifest["assets"]:
        fetch_asset(f"{base}/{asset['file']}", args.destination / asset["file"], asset)
        print(f"Verified {asset['file']} ({asset['bytes']} bytes)")


if __name__ == "__main__":
    main()
