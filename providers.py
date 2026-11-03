"""Generation backends.

Two paths to the same thing: OpenAI directly, or OpenRouter as an aggregator
so one key reaches several providers' models. Both return PNG bytes.

Each variation is requested as its own n=1 call rather than asking a provider
for n images. It behaves the same across providers, gives more varied results,
and one failure doesn't take the whole batch down.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, asdict

import httpx

OPENAI_BASE = "https://api.openai.com/v1"
OPENROUTER_URL = "https://openrouter.ai/api/v1/images"

# Generation is slow; a short timeout is the most likely cause of spurious
# failures, so be generous.
TIMEOUT = httpx.Timeout(600.0, connect=30.0)


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    provider: str
    note: str = ""


MODELS: list[Model] = [
    Model("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", "openai", "Best editing precision"),
    Model("gpt-image-2.5-flare", "GPT Image 2.5 Flare", "openai", "Faster"),
    Model("gpt-image-2", "GPT Image 2", "openai"),
    Model("gpt-image-1.5", "GPT Image 1.5", "openai"),
    Model("openai/gpt-image-2", "GPT Image 2", "openrouter"),
    Model("black-forest-labs/flux.2-pro", "FLUX 2 Pro", "openrouter"),
    Model("bytedance-seed/seedream-4.5", "Seedream 4.5", "openrouter"),
    Model("google/gemini-2.5-flash-image", "Gemini 2.5 Flash Image", "openrouter"),
]

# GPT Image 2.x takes arbitrary sizes, not a fixed enum, subject to: both
# dimensions divisible by 16, longest edge <= 3840, aspect ratio <= 3:1, and a
# total pixel budget (2560x2560 is accepted, 3072x3072 is refused). These are
# the useful points inside that envelope — larger costs more and takes longer.
SIZES = [
    "1024x1024", "1536x1024", "1024x1536",
    "2048x2048", "2560x1536", "1536x2560", "2560x2560",
    "auto",
]
# The full set the API accepts; 'high' is the ceiling, there is no tier above it.
QUALITIES = ["high", "medium", "low", "auto"]


PROVIDERS = ("openai", "openrouter")


def _enabled() -> frozenset[str]:
    """Providers the operator has switched on. Blank or unset means all.

    Holding a key is not the same as wanting to spend it: an account can be out
    of credits while its key stays perfectly valid, and the UI would otherwise
    keep defaulting to it and failing with a 429. `run.sh` re-sources
    ~/.secrets, so unsetting a key cannot express that — hence an explicit
    switch rather than relying on the environment.

    Read once at import, like STUDIO_PASSWORD. An unrecognised name is fatal
    rather than ignored: silently falling back to "all providers" would turn a
    typo into the exact failure this setting exists to prevent.
    """
    raw = os.environ.get("STUDIO_PROVIDERS", "")
    chosen = {p.strip().lower() for p in raw.split(",") if p.strip()}
    if not chosen:
        return frozenset(PROVIDERS)
    unknown = chosen - set(PROVIDERS)
    if unknown:
        raise ValueError(
            f"STUDIO_PROVIDERS names unknown provider(s): {', '.join(sorted(unknown))}."
            f" Valid values: {', '.join(PROVIDERS)}")
    return frozenset(chosen)


ENABLED = _enabled()


def key_for(provider: str) -> str | None:
    if provider not in ENABLED:
        return None
    return os.environ.get("OPENAI_API_KEY" if provider == "openai" else "OPENROUTER_API_KEY")


def catalogue() -> dict:
    """Models the server can actually reach, given which keys are configured."""
    configured = {p: bool(key_for(p)) for p in ("openai", "openrouter")}
    return {
        "models": [asdict(m) | {"available": configured[m.provider]} for m in MODELS],
        "providers": configured,
        "sizes": SIZES,
        "qualities": QUALITIES,
    }


async def _openai(client, model, prompt, size, quality, references) -> bytes:
    headers = {"Authorization": f"Bearer {key_for('openai')}"}
    # `background` is deliberately not sent: gpt-image-2 rejects transparent
    # backgrounds outright. Ask for white in the prompt and key it out after.
    fields: dict = {"model": model, "prompt": prompt, "n": 1, "output_format": "png"}
    if size != "auto":
        fields["size"] = size
    if quality:
        fields["quality"] = quality

    if references:
        # Multipart carries everything as text; JSON needs the real types, so
        # only the multipart branch stringifies.
        files = [("image[]", (f"ref{i}.png", data, "image/png")) for i, data in enumerate(references)]
        form = {k: str(v) for k, v in fields.items()}
        r = await client.post(f"{OPENAI_BASE}/images/edits", headers=headers, data=form, files=files)
    else:
        r = await client.post(f"{OPENAI_BASE}/images/generations", headers=headers, json=fields)
    r.raise_for_status()
    return base64.b64decode(r.json()["data"][0]["b64_json"])


async def _openrouter(client, model, prompt, size, quality, references) -> bytes:
    body: dict = {"model": model, "prompt": prompt, "n": 1, "output_format": "png"}
    if size != "auto":
        body["size"] = size
    if quality:
        body["quality"] = quality
    if references:
        body["input_references"] = [
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + base64.b64encode(d).decode()}}
            for d in references
        ]
    r = await client.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key_for('openrouter')}",
                 "Content-Type": "application/json"},
        json=body,
    )
    r.raise_for_status()
    return base64.b64decode(r.json()["data"][0]["b64_json"])


async def generate_one(provider: str, model: str, prompt: str, size: str,
                       quality: str, references: list[bytes]) -> bytes:
    if not key_for(provider):
        if provider not in ENABLED:
            raise RuntimeError(
                f"{provider} is switched off by STUDIO_PROVIDERS"
                f" (currently: {', '.join(sorted(ENABLED))})")
        raise RuntimeError(f"No API key configured for {provider}")
    backend = _openai if provider == "openai" else _openrouter
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            return await backend(client, model, prompt, size, quality, references)
        except httpx.HTTPStatusError as e:
            # Provider error bodies carry the actual reason; surface it rather
            # than a bare status code.
            detail = e.response.text[:400]
            raise RuntimeError(f"{provider} returned {e.response.status_code}: {detail}") from e
