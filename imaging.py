"""Post-processing: turn a white-background generation into a usable asset.

Models won't reliably return transparency, so the reliable route is to ask for
a plain white background in the prompt and remove it here.
"""
from __future__ import annotations

import io

from PIL import Image, ImageChops

# Pixels at or above WHITE are dropped, at or below SOLID are kept, and the
# band between them fades — so antialiased line edges don't leave a white halo.
WHITE = 248
SOLID = 212


def _alpha(im: Image.Image) -> Image.Image:
    r, g, b = im.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    span = WHITE - SOLID
    return darkest.point(
        lambda v: 0 if v >= WHITE else 255 if v <= SOLID else int((WHITE - v) / span * 255)
    )


def key_out_white(im: Image.Image) -> Image.Image:
    out = im.convert("RGB")
    out.putalpha(_alpha(im.convert("RGB")))
    return out


def crop_to_subject(im: Image.Image, margin: int = 16) -> Image.Image:
    if im.mode != "RGBA":
        return im
    box = im.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return im
    l, t, r, b = box
    return im.crop((max(0, l - margin), max(0, t - margin),
                    min(im.width, r + margin), min(im.height, b + margin)))


def process(png: bytes, keyout: bool = False, crop: bool = False,
            fmt: str = "png", quality: int = 86, max_px: int | None = None) -> bytes:
    """Apply the pipeline and encode. Returns the encoded image bytes."""
    im = Image.open(io.BytesIO(png))
    # Downscale before the per-pixel work, not after: previews are a fraction
    # of the pixels, and resizing a white ground first makes the keyed edges
    # cleaner rather than worse.
    if max_px:
        im.thumbnail((max_px, max_px), Image.LANCZOS)
    if keyout:
        im = key_out_white(im)
        if crop:
            im = crop_to_subject(im)

    buf = io.BytesIO()
    if fmt == "webp":
        # Slow, thorough encoding is worth it for a download and wasted on a
        # preview that is replaced on the next toggle.
        im.save(buf, format="WEBP", quality=quality, method=4 if max_px else 6)
    elif fmt == "jpeg":
        # JPEG has no alpha; flatten onto white so it doesn't go black.
        if im.mode == "RGBA":
            flat = Image.new("RGB", im.size, (255, 255, 255))
            flat.paste(im, mask=im.getchannel("A"))
            im = flat
        im.save(buf, format="JPEG", quality=quality)
    else:
        im.save(buf, format="PNG")
    return buf.getvalue()


def dimensions(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size
