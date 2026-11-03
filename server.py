"""Image Studio — a small web UI for prompting image models.

Run:
    uvicorn server:app --host 127.0.0.1 --port 8080

Keys come from the environment (OPENAI_API_KEY, OPENROUTER_API_KEY) and are
never sent to the browser. If STUDIO_PASSWORD is set, every API call must carry
it as a bearer token; the page asks for it once. Set it before exposing this
to anything but localhost — an open instance spends your API credits.
"""
from __future__ import annotations

import asyncio
import os
import secrets
from collections import OrderedDict
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import imaging
import providers
import storage

STATIC = Path(__file__).parent / "static"
PASSWORD = os.environ.get("STUDIO_PASSWORD", "")
MAX_VARIATIONS = 8

# Processed variants, keyed by the request that produced them, so flipping a
# toggle back and forth doesn't re-run the pipeline each time. Originals live
# on disk; this is only ever a cache and can be dropped freely.
_derived: "OrderedDict[tuple, bytes]" = OrderedDict()
DERIVED_LIMIT = 200

app = FastAPI(title="Image Studio")


def require_auth(request: Request) -> None:
    if not PASSWORD:
        return
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    # Constant-time compare so the password can't be guessed by timing.
    if not secrets.compare_digest(token, PASSWORD):
        raise HTTPException(status_code=401, detail="Bad or missing password")


@app.get("/api/config")
def config() -> JSONResponse:
    return JSONResponse(providers.catalogue() | {"auth_required": bool(PASSWORD)})


@app.get("/api/auth", dependencies=[Depends(require_auth)])
def auth_check() -> JSONResponse:
    """Lets the page verify a password before storing it."""
    return JSONResponse({"ok": True})


@app.get("/api/gallery", dependencies=[Depends(require_auth)])
def gallery() -> JSONResponse:
    return JSONResponse({"results": storage.gallery()})


@app.post("/api/generate", dependencies=[Depends(require_auth)])
async def generate(
    prompt: str = Form(...),
    model: str = Form(...),
    provider: str = Form(...),
    subject: str = Form(""),
    style: str = Form(""),
    size: str = Form("1024x1024"),
    quality: str = Form("high"),
    variations: int = Form(1),
    references: list[UploadFile] = File(default=[]),
) -> JSONResponse:
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is empty")
    count = max(1, min(MAX_VARIATIONS, variations))
    refs = [await f.read() for f in (references or [])]
    # Names come from the subject alone: the style suffix is identical on every
    # generation, so including it would name every file the same thing.
    naming_source = subject.strip() or prompt
    meta = {"prompt": prompt, "subject": subject.strip(), "style": style.strip(),
            "model": model, "provider": provider, "size": size, "quality": quality}

    results = await asyncio.gather(
        *(providers.generate_one(provider, model, prompt, size, quality, refs)
          for _ in range(count)),
        return_exceptions=True,
    )

    out = []
    for item in results:
        if isinstance(item, BaseException):
            out.append({"error": str(item)})
            continue
        # Saving re-encodes to attach metadata; keep it off the event loop.
        image_id = await asyncio.to_thread(storage.save, item, naming_source, meta)
        width, height = imaging.dimensions(item)
        out.append({
            "id": image_id,
            "name": image_id.rsplit("--", 1)[0].replace("-", " "),
            "favorite": False,
            "width": width,
            "height": height,
        } | meta)
    return JSONResponse({"results": out})


@app.post("/api/favorite/{image_id}", dependencies=[Depends(require_auth)])
def favorite(image_id: str, on: bool = True) -> JSONResponse:
    if not storage.set_favorite(image_id, on):
        raise HTTPException(status_code=404, detail="Image not found")
    return JSONResponse({"id": image_id, "favorite": on})


@app.get("/api/image/{image_id}", dependencies=[Depends(require_auth)])
def image(image_id: str, keyout: int = 0, crop: int = 0, fmt: str = "png",
          quality: int = 86, max_px: int = 0, download: int = 0) -> Response:
    path = storage.path_for(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    cache_key = (image_id, bool(keyout), bool(crop), fmt, quality, max_px)
    data = _derived.get(cache_key)
    if data is None:
        data = imaging.process(path.read_bytes(), keyout=bool(keyout), crop=bool(crop),
                               fmt=fmt, quality=quality, max_px=max_px or None)
        _derived[cache_key] = data
        while len(_derived) > DERIVED_LIMIT:
            _derived.popitem(last=False)

    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{image_id}.{fmt}"'
    return Response(content=data, media_type=f"image/{fmt}", headers=headers)


@app.get("/")
def index() -> HTMLResponse:
    # Stamp the asset URLs with their mtime. Without this a browser happily
    # serves a cached app.js after an edit, and the page silently runs old code.
    html = (STATIC / "index.html").read_text()
    stamp = int(max((STATIC / name).stat().st_mtime for name in ("app.js", "app.css")))
    html = html.replace("/static/app.css", f"/static/app.css?v={stamp}")
    html = html.replace("/static/app.js", f"/static/app.js?v={stamp}")
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
