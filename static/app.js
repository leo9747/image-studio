const $ = (id) => document.getElementById(id);
const state = {
  token: sessionStorage.getItem("studio_token") || "",
  models: [], refs: [], results: [], filter: "all",
};

const authHeaders = () => (state.token ? { Authorization: `Bearer ${state.token}` } : {});

/* ---------- Boot ---------- */

async function boot() {
  const cfg = await (await fetch("/api/config")).json();
  state.models = cfg.models;

  const reachable = Object.entries(cfg.providers).filter(([, ok]) => ok).map(([p]) => p);
  $("provider-note").textContent = reachable.length
    ? `keys: ${reachable.join(" · ")}`
    : "no API keys configured — set OPENAI_API_KEY or OPENROUTER_API_KEY";

  fill($("size"), cfg.sizes);
  fill($("quality"), cfg.qualities);
  $("model").replaceChildren(...cfg.models.map((m) => {
    const o = document.createElement("option");
    o.value = `${m.provider}|${m.id}`;
    o.textContent = `${m.label} · ${m.provider}${m.note ? " — " + m.note : ""}`;
    o.disabled = !m.available;
    return o;
  }));
  const firstUsable = cfg.models.findIndex((m) => m.available);
  if (firstUsable >= 0) $("model").selectedIndex = firstUsable;

  if (cfg.auth_required && !state.token) showGate();
  else { reveal(); loadGallery(); }
}

// Generations persist on disk, so past sessions reappear on load.
async function loadGallery() {
  const res = await fetch("/api/gallery", { headers: authHeaders() });
  if (!res.ok) return;
  state.results = (await res.json()).results;
  draw();
}

const fill = (select, values) =>
  select.replaceChildren(...values.map((v) => new Option(v, v)));

function showGate() {
  $("gate").hidden = false;
  $("shell").hidden = true;
}

function reveal() {
  $("gate").hidden = true;
  $("shell").hidden = false;
}

$("gate-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  state.token = $("gate-input").value;
  const probe = await fetch("/api/auth", { headers: authHeaders() });
  if (!probe.ok) {
    $("gate-error").hidden = false;
    return;
  }
  sessionStorage.setItem("studio_token", state.token);
  reveal();
  loadGallery();
});

/* ---------- References ---------- */

$("refs").addEventListener("change", (e) => {
  state.refs.push(...e.target.files);
  e.target.value = "";
  drawRefs();
});

function drawRefs() {
  $("ref-list").replaceChildren(...state.refs.map((file, i) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = `${file.name} · ${Math.round(file.size / 1024)}kb`;
    const drop = document.createElement("button");
    drop.type = "button";
    drop.textContent = "remove";
    drop.onclick = () => { state.refs.splice(i, 1); drawRefs(); };
    li.append(name, drop);
    return li;
  }));
}

/* ---------- Generate ---------- */

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const subject = $("prompt").value.trim();
  if (!subject) return setStatus("Prompt is empty.", "error");

  const style = $("style").value.trim();
  const [provider, model] = $("model").value.split("|");
  const count = Math.max(1, Math.min(8, Number($("variations").value) || 1));

  const body = new FormData();
  body.set("prompt", style ? `${subject}, ${style}` : subject);
  // Sent separately because filenames are built from the subject alone, and
  // because storing the two halves apart lets "reuse" restore them exactly.
  body.set("subject", subject);
  body.set("style", style);
  body.set("provider", provider);
  body.set("model", model);
  body.set("size", $("size").value);
  body.set("quality", $("quality").value);
  body.set("variations", String(count));
  state.refs.forEach((f) => body.append("references", f));

  $("go").disabled = true;
  setStatus(`Generating ${count}…`);
  showPending(count);
  const started = Date.now();

  try {
    const res = await fetch("/api/generate", { method: "POST", headers: authHeaders(), body });
    if (res.status === 401) { sessionStorage.removeItem("studio_token"); return showGate(); }
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const { results } = await res.json();
    state.results = results.concat(state.results);
    draw();
    const failed = results.filter((r) => r.error).length;
    const secs = ((Date.now() - started) / 1000).toFixed(0);
    setStatus(`${results.length - failed}/${results.length} in ${secs}s`, failed ? "error" : "");
  } catch (err) {
    setStatus(err.message, "error");
    draw();
  } finally {
    $("go").disabled = false;
  }
});

const setStatus = (text, tone = "") => {
  const el = $("status");
  el.textContent = text;
  el.dataset.tone = tone;
};

/* ---------- Results ---------- */

function url(id, { download = false, preview = false } = {}) {
  const p = new URLSearchParams({
    keyout: $("keyout").checked ? 1 : 0,
    crop: $("crop").checked ? 1 : 0,
  });
  if (preview) { p.set("fmt", "webp"); p.set("max_px", "900"); }
  else if (download) { p.set("fmt", "png"); p.set("download", "1"); }
  return `/api/image/${id}?${p}`;
}

// Images are fetched rather than set as src directly, because the password
// has to travel as a header.
async function load(img, id) {
  const res = await fetch(url(id, { preview: true }), { headers: authHeaders() });
  if (!res.ok) return;
  img.src = URL.createObjectURL(await res.blob());
}

function showPending(n) {
  const cards = Array.from({ length: n }, () => {
    const card = document.createElement("div");
    card.className = "card card--pending";
    card.innerHTML = '<div class="card__frame"></div><div class="card__meta">generating…</div>';
    return card;
  });
  $("grid").replaceChildren(...cards, ...$("grid").querySelectorAll(".card"));
}

function draw() {
  const grid = $("grid");
  const shown = state.results.filter((r) => state.filter === "all" || r.favorite);

  if (!shown.length) {
    grid.innerHTML = `<p class="empty">${
      state.filter === "favorite" ? "No favourites yet." : "Nothing generated yet."
    }</p>`;
    return;
  }

  grid.replaceChildren(...shown.map((r) => {
    const card = document.createElement("div");
    if (r.error) {
      card.className = "card card--error";
      card.innerHTML = "<p></p>";
      card.querySelector("p").textContent = r.error;
      return card;
    }
    card.className = "card";

    const frame = document.createElement("div");
    frame.className = "card__frame";
    const img = document.createElement("img");
    img.alt = r.name || "";
    frame.append(img);
    frame.onclick = () => openLightbox(r.id);
    load(img, r.id);

    const star = document.createElement("button");
    star.type = "button";
    star.className = "star" + (r.favorite ? " is-on" : "");
    star.title = r.favorite ? "Remove from favourites" : "Mark as favourite";
    star.textContent = r.favorite ? "★" : "☆";
    star.onclick = (e) => { e.stopPropagation(); toggleFavorite(r); };
    frame.append(star);

    const name = document.createElement("div");
    name.className = "card__name";
    name.textContent = r.name || r.id;
    name.title = r.prompt || "";

    const meta = document.createElement("div");
    meta.className = "card__meta";
    const dims = document.createElement("span");
    dims.textContent = `${r.width}×${r.height}`;

    const actions = document.createElement("span");
    actions.className = "card__actions";
    if (r.prompt) {
      const again = document.createElement("a");
      again.textContent = "reuse";
      again.href = "#";
      again.title = "Load this generation's settings back into the form";
      again.onclick = (e) => { e.preventDefault(); reuse(r); };
      actions.append(again);

      const copy = document.createElement("a");
      copy.textContent = "copy prompt";
      copy.href = "#";
      copy.onclick = async (e) => {
        e.preventDefault();
        const ok = await copyText(r.prompt);
        copy.textContent = ok ? "copied" : "copy failed";
        setTimeout(() => { copy.textContent = "copy prompt"; }, 1400);
      };
      actions.append(copy);
    }
    const save = document.createElement("a");
    save.textContent = "download";
    save.href = "#";
    save.onclick = (e) => { e.preventDefault(); download(r.id); };
    actions.append(save);
    meta.append(dims, actions);

    card.append(frame, name, meta);
    return card;
  }));
}

// Put a past generation's settings back into the form, ready to tweak.
function reuse(r) {
  if (r.subject) {
    $("prompt").value = r.subject;
    // Images made before the halves were stored separately have no `style`,
    // but the combined prompt is "<subject>, <style>" — so recover it rather
    // than wiping whatever is in the box.
    if (r.style) {
      $("style").value = r.style;
    } else if (r.prompt && r.prompt.startsWith(r.subject)) {
      $("style").value = r.prompt.slice(r.subject.length).replace(/^,\s*/, "");
    }
  } else if (r.prompt) {
    $("prompt").value = r.prompt;
  }

  if (r.provider && r.model) {
    const wanted = `${r.provider}|${r.model}`;
    const match = [...$("model").options].find((o) => o.value === wanted && !o.disabled);
    if (match) $("model").value = wanted;
  }
  for (const [id, value] of [["size", r.size], ["quality", r.quality]]) {
    if (value && [...$(id).options].some((o) => o.value === value)) $(id).value = value;
  }

  window.scrollTo({ top: 0, behavior: "smooth" });
  $("prompt").focus();
  setStatus(`Loaded settings from ${r.name}`);
}

async function copyText(text) {
  // The async clipboard API only exists in a secure context, which a hosted
  // instance on plain HTTP is not — hence the execCommand fallback.
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through */ }
  }
  const scratch = document.createElement("textarea");
  scratch.value = text;
  scratch.setAttribute("readonly", "");
  scratch.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.append(scratch);
  scratch.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  scratch.remove();
  return ok;
}

async function toggleFavorite(result) {
  const next = !result.favorite;
  const res = await fetch(`/api/favorite/${result.id}?on=${next}`, {
    method: "POST", headers: authHeaders(),
  });
  if (!res.ok) return setStatus("Could not update favourite.", "error");
  result.favorite = next;
  draw();
}

async function download(id) {
  const res = await fetch(url(id, { download: true }), { headers: authHeaders() });
  if (!res.ok) return setStatus("Download failed.", "error");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = `${id}.png`;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function openLightbox(id) {
  const res = await fetch(url(id, { preview: true }), { headers: authHeaders() });
  if (!res.ok) return;
  $("lightbox-img").src = URL.createObjectURL(await res.blob());
  $("lightbox").hidden = false;
}
$("lightbox").onclick = () => { $("lightbox").hidden = true; };

/* ---------- View options ---------- */

$("filter").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  $("filter").querySelectorAll("button").forEach((b) => b.classList.toggle("is-on", b === btn));
  state.filter = btn.dataset.filter;
  draw();
});

$("backdrop").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  $("backdrop").querySelectorAll("button").forEach((b) => b.classList.toggle("is-on", b === btn));
  $("grid").className = `grid bg-${btn.dataset.bg}`;
});

for (const id of ["keyout", "crop"]) $(id).addEventListener("change", draw);

$("grid").className = "grid bg-checker";

// Handle for debugging from the console: studio.state, studio.draw().
window.studio = { state, draw };

boot();
