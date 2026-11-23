"""Visual config builder — a localhost-only single page, no web framework.

`elenchus web` starts a stdlib HTTP server bound to 127.0.0.1, opens the page, and
lets you toggle checks/techniques and settings. The YAML is rendered server-side via
configio.render_config, so the file the UI produces is identical to `elenchus init`.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _page_data():
    from ..configio import EFFORTS, PROVIDERS, SENSITIVITIES
    from ..core.checks import CHECKS
    from ..core.config import load_config

    cfg = load_config()  # prefill from a discovered config, else defaults

    def tech_on(cid, t):
        cc = cfg.checks.get(cid)
        if cc and t.id in cc.techniques:
            return cc.techniques[t.id]
        return t.default_enabled

    return {
        "providers": PROVIDERS,
        "sensitivities": SENSITIVITIES,
        "efforts": EFFORTS,
        "defaults": {
            "provider": cfg.provider,
            "model": cfg.model,
            "effort": cfg.effort,
            "sensitivity": cfg.sensitivity,
            "rate_limit_per_min": cfg.rate_limit_per_min,
        },
        "checks": [
            {
                "id": cid,
                "title": c.title,
                "enabled": cfg.check_enabled(cid),
                "techniques": [
                    {"id": t.id, "name": t.name, "definition": t.definition, "enabled": tech_on(cid, t)}
                    for t in c.techniques
                ],
            }
            for cid, c in CHECKS.items()
        ],
    }


def _config_from_payload(p):
    from ..core.config import CheckConfig, Config

    checks = {
        cid: CheckConfig(
            enabled=bool(c.get("enabled", True)),
            techniques={k: bool(v) for k, v in (c.get("techniques") or {}).items()},
        )
        for cid, c in (p.get("checks") or {}).items()
    }
    try:
        rate = int(p.get("rate_limit_per_min") or 0)
    except (TypeError, ValueError):
        rate = 5
    return Config(
        provider=p.get("provider") or "anthropic",
        model=p.get("model") or "",
        effort=p.get("effort") or "medium",
        sensitivity=p.get("sensitivity") or "balanced",
        rate_limit_per_min=rate,
        checks=checks,
    )


def _make_handler(html: bytes):
    from ..configio import render_config

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # quiet
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, html, "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/render":
                yaml_text = render_config(_config_from_payload(payload))
                self._send(200, json.dumps({"yaml": yaml_text}).encode())
            elif self.path == "/api/save":
                path = payload.get("path") or "elenchus.yaml"
                try:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(payload.get("yaml", ""))
                    self._send(200, json.dumps({"ok": True, "path": str(path)}).encode())
                except OSError as exc:
                    self._send(200, json.dumps({"ok": False, "error": str(exc)}).encode())
            else:
                self._send(404, b"{}")

    return Handler


def serve(port: int = 8765, open_browser: bool = True) -> None:
    html = PAGE.replace("__DATA__", json.dumps(_page_data())).encode("utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(html))
    url = f"http://127.0.0.1:{port}/"
    print(f"Elenchus config builder → {url}   (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.shutdown()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Elenchus · config builder</title>
<style>
  :root {
    --bg:#f6f7f9; --panel:#ffffff; --ink:#1b1f24; --muted:#6b7280; --line:#e5e7eb;
    --accent:#7c5cff; --accent-ink:#fff; --code-bg:#0f1117; --code-ink:#e6e6e6; --on:#16a34a; --off:#cbd1d9; --chip:#eef0f4;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0e1013; --panel:#171a1f; --ink:#e8eaed; --muted:#9aa1ab; --line:#262b33;
            --code-bg:#0b0d11; --code-ink:#d6dae0; --off:#3a414b; --chip:#20252d; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  header { padding:22px 28px 0; display:flex; align-items:baseline; gap:12px; }
  header h1 { margin:0; font-size:20px; letter-spacing:.2px; }
  header .tag { color:var(--muted); font-size:13px; }
  .toolbar { display:flex; gap:12px; align-items:center; padding:16px 28px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
  .toolbar input[type=search], .toolbar select {
    background:transparent; color:var(--ink); border:1px solid var(--line); border-radius:9px; padding:8px 11px; font:inherit; }
  .toolbar input[type=search] { min-width:220px; }
  .summary { color:var(--muted); font-size:13px; margin-left:auto; }
  .wrap { display:grid; grid-template-columns: 1fr 460px; gap:24px; padding:24px 28px; align-items:start; }
  @media (max-width: 980px){ .wrap { grid-template-columns:1fr; } .preview-col { position:static; } }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin-bottom:20px; }
  .card h2 { margin:0 0 14px; font-size:14px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
  .row { display:flex; flex-wrap:wrap; gap:16px; }
  .field { display:flex; flex-direction:column; gap:5px; }
  .field label { font-size:12px; color:var(--muted); }
  select, input[type=text], input[type=number] {
    background:transparent; color:var(--ink); border:1px solid var(--line); border-radius:9px; padding:8px 10px; font:inherit; min-width:150px; }
  .check-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:6px; }
  .check-head .left { display:flex; align-items:center; gap:10px; cursor:pointer; user-select:none; }
  .check-head .title { font-weight:600; }
  .check-head .caret { color:var(--muted); transition:transform .15s; }
  .card.collapsed .caret { transform:rotate(-90deg); }
  .card.collapsed .techs { display:none; }
  .count { font-size:12px; color:var(--muted); background:var(--chip); border-radius:20px; padding:2px 9px; }
  .actions { display:flex; gap:10px; align-items:center; }
  .link { color:var(--accent); cursor:pointer; font-size:12px; background:none; border:none; padding:0; }
  .techs { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:6px 16px; margin-top:8px; }
  .tech { display:flex; align-items:flex-start; gap:9px; padding:6px 8px; border-radius:8px; }
  .tech.hidden { display:none; }
  .tech:hover { background:rgba(124,92,255,.06); }
  .tech input { margin-top:3px; }
  .tech .name { font-size:13.5px; }
  .tech .def { font-size:11.5px; color:var(--muted); }
  .switch { position:relative; width:38px; height:22px; flex:0 0 auto; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:var(--off); border-radius:22px; transition:.15s; }
  .slider:before { content:""; position:absolute; height:16px; width:16px; left:3px; top:3px; background:#fff; border-radius:50%; transition:.15s; }
  .switch input:checked + .slider { background:var(--on); }
  .switch input:checked + .slider:before { transform:translateX(16px); }
  .preview-col { position:sticky; top:24px; }
  .preview { background:var(--code-bg); color:var(--code-ink); border-radius:12px; padding:16px; font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
    white-space:pre; overflow:auto; max-height:56vh; margin:0; }
  .bar { display:flex; gap:10px; align-items:center; margin-top:14px; flex-wrap:wrap; }
  .btn { background:var(--accent); color:var(--accent-ink); border:none; border-radius:10px; padding:10px 16px; font:inherit; font-weight:600; cursor:pointer; }
  .btn.ghost { background:transparent; color:var(--ink); border:1px solid var(--line); }
  .path { flex:1; min-width:160px; }
  .status { color:var(--muted); font-size:12.5px; min-height:18px; margin-top:8px; }
  .status.ok { color:var(--on); }
  .status.err { color:#e5484d; }
</style>
</head>
<body>
<header>
  <h1>Elenchus</h1><span class="tag">visual config builder — build your <code>elenchus.yaml</code></span>
</header>
<div class="toolbar">
  <input id="search" type="search" placeholder="Filter techniques…" spellcheck="false" autocomplete="off">
  <select id="preset">
    <option value="">Apply a preset…</option>
    <option value="everything">Everything on</option>
    <option value="debate">Debate mode — fallacy + rhetoric</option>
    <option value="moderation">Moderation mode — scam + rhetoric</option>
    <option value="none">Turn all off</option>
  </select>
  <span class="summary" id="summary"></span>
</div>
<div class="wrap">
  <div class="settings-col">
    <div class="card">
      <h2>Model</h2>
      <div class="row">
        <div class="field"><label>Provider</label><select id="provider"></select></div>
        <div class="field"><label>Model</label><input id="model" type="text" style="min-width:220px"></div>
        <div class="field"><label>Effort</label><select id="effort"></select></div>
        <div class="field"><label>Sensitivity</label><select id="sensitivity"></select></div>
        <div class="field"><label>Rate limit / min</label><input id="rate" type="number" min="0" style="min-width:110px"></div>
      </div>
    </div>
    <div id="checks"></div>
  </div>
  <div class="preview-col">
    <div class="card">
      <h2>elenchus.yaml</h2>
      <pre class="preview" id="preview">…</pre>
      <div class="bar">
        <input id="path" class="path" type="text" value="elenchus.yaml" spellcheck="false">
        <button class="btn" id="save">Save</button>
        <button class="btn ghost" id="download">Download</button>
        <button class="btn ghost" id="copy">Copy</button>
      </div>
      <div class="status" id="status"></div>
    </div>
  </div>
</div>
<script>
const DATA = __DATA__;
const $ = (s, r=document) => r.querySelector(s);

function opt(sel, values, current) {
  sel.innerHTML = "";
  values.forEach(v => { const o=document.createElement("option"); o.value=v; o.textContent=v; if(v===current)o.selected=true; sel.appendChild(o); });
}
opt($("#provider"), DATA.providers, DATA.defaults.provider);
opt($("#effort"), DATA.efforts, DATA.defaults.effort);
opt($("#sensitivity"), DATA.sensitivities, DATA.defaults.sensitivity);
$("#model").value = DATA.defaults.model;
$("#rate").value = DATA.defaults.rate_limit_per_min;

const checksEl = $("#checks");
DATA.checks.forEach(c => {
  const card = document.createElement("div"); card.className="card"; card.dataset.check=c.id;
  const techs = c.techniques.map(t => `
    <label class="tech" data-tech="${t.id}">
      <input type="checkbox" id="t-${c.id}-${t.id}" ${t.enabled?"checked":""}>
      <span><span class="name">${t.name}</span><br><span class="def"></span></span>
    </label>`).join("");
  card.innerHTML = `
    <div class="check-head">
      <span class="left" data-toggle="${c.id}"><span class="caret">▾</span><span class="title">${c.title}</span><span class="count" id="count-${c.id}"></span></span>
      <div class="actions">
        <button class="link" data-all="${c.id}">all</button>
        <button class="link" data-none="${c.id}">none</button>
        <label class="switch"><input type="checkbox" id="chk-${c.id}" ${c.enabled?"checked":""}><span class="slider"></span></label>
      </div>
    </div>
    <div class="techs">${techs}</div>`;
  checksEl.appendChild(card);
});
DATA.checks.forEach(c => c.techniques.forEach(t => {
  $(`#t-${c.id}-${t.id}`).parentElement.querySelector(".def").textContent = t.definition;  // safe from HTML in data
}));

function buildState() {
  const checks = {};
  DATA.checks.forEach(c => {
    const techniques = {};
    c.techniques.forEach(t => { techniques[t.id] = $(`#t-${c.id}-${t.id}`).checked; });
    checks[c.id] = { enabled: $(`#chk-${c.id}`).checked, techniques };
  });
  return { provider:$("#provider").value, model:$("#model").value, effort:$("#effort").value,
           sensitivity:$("#sensitivity").value, rate_limit_per_min:Number($("#rate").value)||0, checks };
}

function updateCounts() {
  let total=0, on=0;
  DATA.checks.forEach(c => {
    const n = c.techniques.length, k = c.techniques.filter(t => $(`#t-${c.id}-${t.id}`).checked).length;
    $(`#count-${c.id}`).textContent = `${k}/${n}`;
    total += n; on += k;
  });
  $("#summary").textContent = `${on} of ${total} techniques enabled`;
}

let timer=null, current="";
async function render() {
  const res = await fetch("/api/render", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(buildState())});
  const j = await res.json(); current=j.yaml; $("#preview").textContent = current;
}
function schedule(){ clearTimeout(timer); timer=setTimeout(render, 100); updateCounts(); status(""); }

document.addEventListener("input", e => { if(e.target.id !== "search") schedule(); });

// search filter
$("#search").addEventListener("input", e => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll(".tech").forEach(el => {
    const txt = el.textContent.toLowerCase();
    el.classList.toggle("hidden", q && !txt.includes(q));
  });
});

// all / none per check, and collapse
document.addEventListener("click", e => {
  const all=e.target.dataset.all, none=e.target.dataset.none, toggle=e.target.closest("[data-toggle]");
  if(all){ DATA.checks.find(c=>c.id===all).techniques.forEach(t=>$(`#t-${all}-${t.id}`).checked=true); schedule(); }
  else if(none){ DATA.checks.find(c=>c.id===none).techniques.forEach(t=>$(`#t-${none}-${t.id}`).checked=false); schedule(); }
  else if(toggle){ toggle.closest(".card").classList.toggle("collapsed"); }
});

// presets
$("#preset").addEventListener("change", e => {
  const p = e.target.value; if(!p) return;
  const checkOn = { everything:()=>true, none:()=>false,
    debate:id=>id==="fallacy"||id==="rhetoric", moderation:id=>id==="scam"||id==="rhetoric" }[p];
  DATA.checks.forEach(c => {
    const on = checkOn(c.id);
    $(`#chk-${c.id}`).checked = on;
    c.techniques.forEach(t => { $(`#t-${c.id}-${t.id}`).checked = on; });
  });
  e.target.value = ""; schedule();
});

function status(msg, cls=""){ const s=$("#status"); s.textContent=msg; s.className="status "+cls; }
$("#save").onclick = async () => {
  const res = await fetch("/api/save", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:$("#path").value, yaml:current})});
  const j = await res.json(); j.ok ? status("Saved to "+j.path, "ok") : status("Error: "+j.error, "err");
};
$("#download").onclick = () => {
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob([current],{type:"text/yaml"})); a.download=$("#path").value||"elenchus.yaml"; a.click();
};
$("#copy").onclick = async () => { await navigator.clipboard.writeText(current); status("Copied to clipboard", "ok"); };

updateCounts();
render();
</script>
</body>
</html>"""
