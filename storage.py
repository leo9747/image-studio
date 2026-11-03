"""Where generations live on disk.

Everything generated lands in `generations/`. Marking a favourite moves the
file to `favorites/` — a move, not a copy, so a picture is only ever in one
place and the generations folder stays a pile of candidates.

The filename is a short slug of the prompt plus a random suffix, and that
filename stem doubles as the image's id. Ids therefore arrive from the client
and are joined to a path, so every lookup is validated against ID_RE first;
that pattern admits no slash or dot and makes traversal impossible.
"""
from __future__ import annotations

import io
import os
import re
import secrets
from pathlib import Path

from PIL import Image, PngImagePlugin

ROOT = Path(os.environ.get("STUDIO_OUTPUT_DIR") or Path(__file__).parent / "output")
GENERATIONS = ROOT / "generations"
FAVORITES = ROOT / "favorites"

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{6}$")

# Dropped when building a name: articles, prepositions and the filler that
# tends to open a prompt ("a small floating island..." -> "floating island").
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or",
    "its", "their", "his", "her", "it", "that", "this", "these", "those", "as",
    "by", "from", "into", "over", "under", "above", "below", "is", "are", "be",
    "being", "been", "very", "small", "large", "big", "tiny", "some", "few",
    "several", "many", "other", "another", "each", "both", "all", "no", "not",
}
MAX_WORDS = 5
MIN_WORDS = 3


def ensure_dirs() -> None:
    GENERATIONS.mkdir(parents=True, exist_ok=True)
    FAVORITES.mkdir(parents=True, exist_ok=True)


def slug_for(subject: str) -> str:
    """A 3-5 word compression of the prompt, safe for a filename."""
    words = re.findall(r"[a-z0-9]+", subject.lower())
    kept = [w for w in words if w not in STOPWORDS and len(w) > 1]
    # If filtering was too aggressive to be descriptive, fall back to the raw
    # words rather than returning something like "dome".
    if len(kept) < MIN_WORDS:
        kept = words or ["generation"]
    return "-".join(kept[:MAX_WORDS]) or "generation"


def save(png: bytes, subject: str, meta: dict[str, str]) -> str:
    """Write a generation and return its id. Blocking; call in a thread."""
    ensure_dirs()
    image_id = f"{slug_for(subject)}--{secrets.token_hex(3)}"

    # Re-encode to attach the prompt to the file itself, so a picture found
    # months later still says what made it.
    im = Image.open(io.BytesIO(png))
    info = PngImagePlugin.PngInfo()
    for key, value in meta.items():
        if value:
            info.add_text(key, str(value))
    im.save(GENERATIONS / f"{image_id}.png", format="PNG", pnginfo=info)
    return image_id


def path_for(image_id: str) -> Path | None:
    if not ID_RE.match(image_id):
        return None
    for folder in (GENERATIONS, FAVORITES):
        candidate = folder / f"{image_id}.png"
        if candidate.is_file():
            return candidate
    return None


def set_favorite(image_id: str, on: bool) -> bool:
    """Move between the two folders. True if the file ended up where asked."""
    current = path_for(image_id)
    if current is None:
        return False
    target_dir = FAVORITES if on else GENERATIONS
    target = target_dir / current.name
    if current == target:
        return True
    target_dir.mkdir(parents=True, exist_ok=True)
    current.replace(target)
    return True


# Everything echoed back to the client so a generation can be reproduced.
META_KEYS = ("prompt", "subject", "style", "model", "provider", "size", "quality")


def _entry(path: Path, favorite: bool) -> dict:
    with Image.open(path) as im:
        width, height = im.size
        info = im.info or {}
    entry = {
        "id": path.stem,
        "name": path.stem.rsplit("--", 1)[0].replace("-", " "),
        "favorite": favorite,
        "width": width,
        "height": height,
        "modified": path.stat().st_mtime,
    }
    entry.update({key: info.get(key, "") for key in META_KEYS})
    return entry


def gallery(limit: int = 200) -> list[dict]:
    """Everything on disk, newest first, favourites included."""
    ensure_dirs()
    found = [(p, False) for p in GENERATIONS.glob("*.png")]
    found += [(p, True) for p in FAVORITES.glob("*.png")]
    found.sort(key=lambda pair: pair[0].stat().st_mtime, reverse=True)
    return [_entry(path, fav) for path, fav in found[:limit]]
