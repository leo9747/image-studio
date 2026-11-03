# Image Studio

A small web UI for prompting image models. Type a prompt, attach reference
images, generate several variations at once, compare them, strip the white
background, download.

Built because there is no off-the-shelf tool that does all three of: a visual
interface, using API keys you already hold, and the background-removal step
that turns a generation into a usable asset. OpenAI has no image playground at
all; OpenRouter is API-only; fal.ai has a UI but needs its own key.

## Why variations matter

Image generation is dice-rolling. The main thing this gives you over a CLI is
**N variations per prompt, generated in parallel, side by side**. Each one is a
separate `n=1` request, so results vary more and one failure doesn't take the
batch down — failed tiles show their error and the rest still arrive.

## Quick start

```bash
./run.sh
```

Open <http://localhost:8080>. The script installs anything missing, picks up
keys from `~/.secrets` if that file exists, and binds to localhost. It is safe
to re-run, which matters in a container that wipes installed packages on
rebuild — the pip cache persists, so reinstalling is quick.

To expose it beyond this machine, pass a host — but it refuses unless
`STUDIO_PASSWORD` is set, since an open instance spends your API credits:

```bash
STUDIO_PASSWORD=something-long ./run.sh 0.0.0.0
```

Doing it by hand instead:

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...          # and/or OPENROUTER_API_KEY
uvicorn server:app --host 127.0.0.1 --port 8080
```

## When the port is taken

`run.sh` resolves the port before announcing anything. Uvicorn binds *after* it
prints its startup banner, so a clash used to look like a successful start —
`Application startup complete` followed immediately by `Errno 98` — which reads
as working and isn't.

Three outcomes now:

- **The studio is already on that port** — it says so and exits 0 rather than
  starting a second copy. Re-running `./run.sh` is therefore harmless.
- **Something else holds it** — it names the holding process (read from `/proc`,
  since `ss`/`lsof`/`netstat` are absent from slim images) and moves to the next
  port in `STUDIO_PORT_FALLBACKS`.
- **Nothing is free** — it fails before starting, listing what it tried.

The fallback list matters in a container: only *published* ports reach the host,
so drifting onto an arbitrary free port would serve happily and still look
unreachable in the browser. Inside a container the default list is the other
ports this box publishes (`8000 3001 5173 5174 4321`); outside one, where any
port works, it scans upward from `PORT`. Override it if your container publishes
a different set:

```bash
STUDIO_PORT_FALLBACKS="9000 9001" ./run.sh
```

## Configuration

Copy `.env.example` to `.env` for reference; the app reads plain environment
variables.

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Reaches the GPT Image models directly |
| `OPENROUTER_API_KEY` | Reaches GPT Image (2.5 included), FLUX, Seedream and Gemini through one key |
| `STUDIO_PASSWORD` | Shared password for the UI. Blank disables authentication |
| `STUDIO_OUTPUT_DIR` | Where generations are written (default `./output`) |
| `STUDIO_PROVIDERS` | Restrict which providers are offered (see below) |
| `PORT` | Port to serve on (default `8080`) |
| `STUDIO_PORT_FALLBACKS` | Ports to try when `PORT` is taken (see below) |

At least one API key is needed. The model dropdown greys out anything the
configured keys can't reach.

### Switching a provider off

Holding a key is not the same as wanting to spend it. An account can run out of
credits while its key stays perfectly valid — the key authenticates fine and
every generation comes back `429 insufficient_quota`. Since the dropdown
defaults to the first *available* model, it will keep selecting the dead
provider and failing.

Unsetting the key does not help: `run.sh` re-sources `~/.secrets` on every
start and puts it back. Name the providers you want instead:

```bash
STUDIO_PROVIDERS=openrouter ./run.sh
```

Anything omitted is treated as having no key: greyed out in the dropdown, and
refused server-side with a message naming the switch rather than a confusing
"no API key configured". Blank or unset means all providers, so the default is
unchanged.

## Security

**An open instance spends your API credits.** Anyone who can load the page can
generate images billed to you.

- Keys live in the server environment and are never sent to the browser.
- With no `STUDIO_PASSWORD`, bind to `127.0.0.1` only.
- Before exposing it to any network, set `STUDIO_PASSWORD` **and** put it behind
  HTTPS — the password is sent as a bearer token and is readable in plain HTTP.
- The password is a single shared secret with no rate limiting. It is adequate
  for a personal instance, not for something widely shared.

## Hosting

```bash
docker build -t image-studio .
docker run -p 8080:8080 \
  -e OPENAI_API_KEY=sk-... \
  -e STUDIO_PASSWORD=something-long \
  image-studio
```

The container binds `0.0.0.0`, so set a password. Any host that runs a container
and holds env secrets works — Fly, Render, Railway, a VPS behind a reverse proxy.

**Mount `STUDIO_OUTPUT_DIR` as a volume**, or a container restart takes every
generation with it:

```bash
docker run -p 8080:8080 -v "$PWD/output:/app/output" \
  -e OPENAI_API_KEY=sk-... -e STUDIO_PASSWORD=something-long image-studio
```

Storage is a plain directory, so more than one replica would need shared
storage. For a single-user tool that isn't worth solving.

## How it works

| File | Role |
|---|---|
| `server.py` | Routes, auth, derived-image cache |
| `storage.py` | Naming, saving, favourites, gallery listing |
| `providers.py` | OpenAI and OpenRouter backends; model catalogue |
| `imaging.py` | White removal, crop to subject, WebP/PNG/JPEG export |
| `static/` | The page — no build step, no framework |

## Storage and naming

```
output/
  generations/   everything, as it is made
  favorites/     the ones you star
```

Starring **moves** the file rather than copying it, so a picture is only in one
place and `generations/` stays a pile of candidates you can delete wholesale.
Unstarring moves it back.

Filenames are a 3–5 word compression of the prompt plus a short random suffix:

```
floating-island-lighthouse-terraced-gardens--129547.png
```

The name is built from the **subject** box alone, never the style suffix — the
suffix is identical on every generation, so including it would name every file
the same thing. Articles and filler are dropped; if that leaves too little to be
descriptive, it falls back to the raw words.

That filename stem is also the image's id, which means ids arrive from the
browser and get joined to a path. Every lookup is validated against a pattern
admitting no slash or dot first, so a traversal attempt is a 404.

Each PNG carries its prompt, model, size and quality in its metadata, so a file
found later still says what made it:

```bash
python3 -c "from PIL import Image; print(Image.open('output/favorites/x.png').info)"
```

**Why white backgrounds.** The models won't reliably return transparency, and
GPT Image rejects a transparent-background request outright. The reliable route
is to ask for a plain white background in the prompt and remove it afterwards —
which is what "Remove white" does. The alpha ramp keeps antialiased line edges
from leaving a white halo.

Previews are downscaled *before* the per-pixel work and encoded with fast WebP
settings; downloads get full resolution and thorough encoding. That's the
difference between a preview taking 0.3s and 6s.

The backdrop switcher (checker / white / page / ink) exists to judge whether
background removal actually worked — on white, a failed key-out looks identical
to a good one.

## Output size

GPT Image 2.x takes arbitrary dimensions rather than a fixed set, within four
limits — confirmed against the API, not from documentation:

- both dimensions divisible by 16
- longest edge no greater than 3840
- aspect ratio no wider than 3:1
- a total pixel budget: 2560x2560 is accepted, 3072x3072 is refused

The quality setting does not lower the ceiling; 2560x2560 is accepted at
`high`. The dropdown offers useful points inside that envelope. Larger sizes
cost more per image and take noticeably longer, so draft at 1024 and only go
large once a prompt is settled.

## Reusing a generation

Every card carries two actions beside `download`:

- **reuse** puts that generation's subject, style suffix, model, size and
  quality back into the form, ready to tweak and run again.
- **copy prompt** copies the full prompt. The async clipboard API only exists in
  a secure context, which a hosted instance on plain HTTP is not, so there is an
  older fallback underneath.

Assets are served with an mtime-stamped query string. Without it a browser
cheerfully keeps a cached `app.js` after an edit and the page silently runs old
code — which is exactly how a shipped feature can appear to be missing.

## Relationship to the d/acc fellowship site

The white-removal and crop logic is the same as
`scripts/generate_illustrations.py` in the `dacc-fellowship` repo. That script
stays the right tool for regenerating the site's seven illustrations in one
reproducible batch from `prompts.yaml`. This is for exploring before committing
to a prompt.

## Limits

- Reference images are sent on every request; providers cap how many they accept
  and the cap differs between them.
- Nothing prunes `generations/`. It grows until you delete things, and at roughly
  1.5 MB per image that adds up — favourites are safe from a bulk delete, which
  is the point of the two folders.
- No prompt presets across reloads — the browser keeps nothing but the password
  for the session.
- OpenRouter returns a per-generation cost that is not yet surfaced in the UI.
