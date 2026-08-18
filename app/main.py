from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from starlette.background import BackgroundTask
import httpx
import re
import asyncio
from urllib.parse import unquote, urlparse
from pathlib import PurePosixPath

app = FastAPI(title="⚡ CF Edge Mirror — Best Compatibility", version="4.0")

# ─── User-Agent pool (rotate করবে anti-bot bypass এর জন্য) ────────────────
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
]

_ua_index = 0

def next_ua() -> str:
    global _ua_index
    ua = UA_POOL[_ua_index % len(UA_POOL)]
    _ua_index += 1
    return ua


def build_upstream_headers(request: Request, target_url: str) -> dict:
    parsed = urlparse(target_url)
    origin  = f"{parsed.scheme}://{parsed.netloc}"
    referer = origin + "/"

    headers = {
        "User-Agent":      next_ua(),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Connection":      "keep-alive",
        "Referer":         referer,
        "Origin":          origin,
        "DNT":             "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "same-origin",
        "Sec-Fetch-User":  "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    # Range / resume support
    rng = request.headers.get("range")
    if rng:
        headers["Range"] = rng

    # Forward cookies if client sends any (session/auth links)
    cookies = request.headers.get("cookie")
    if cookies:
        headers["Cookie"] = cookies

    # Forward any x-custom-* headers from client (power user feature)
    for k, v in request.headers.items():
        if k.lower().startswith("x-mirror-"):
            real_key = k[9:]  # strip x-mirror- prefix
            headers[real_key] = v

    return headers


def extract_filename(source_url: str, headers) -> str:
    cd = headers.get("content-disposition", "")

    m = re.search(r"filename\*\s*=\s*UTF-8''([^;\s]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1)).strip().strip('"\'')

    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    m = re.search(r"filename\s*=\s*([^;\s]+)", cd, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"\'')

    path_name = PurePosixPath(urlparse(source_url).path).name
    if path_name and "." in path_name:
        return unquote(path_name)

    return "download.bin"


async def open_upstream(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    retries: int = 3,
    delay: float = 1.5,
):
    """Retry loop — handles transient failures and 429 backoff."""
    last_exc = None
    for attempt in range(retries):
        try:
            req      = client.build_request("GET", url, headers=headers)
            upstream = await client.send(req, stream=True)

            # 429 Too Many Requests — back off and retry
            if upstream.status_code == 429:
                retry_after = float(upstream.headers.get("retry-after", delay * (attempt + 1)))
                await upstream.aclose()
                await asyncio.sleep(min(retry_after, 10))
                continue

            return upstream

        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt < retries - 1:
                await asyncio.sleep(delay * (attempt + 1))
            continue

    raise last_exc or httpx.RequestError("All retry attempts failed")


HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CF Mirror — by Kobir Shah</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#09090b;
  --card:#111113;
  --border:#1e1e22;
  --accent:#3b82f6;
  --accent2:#8b5cf6;
  --text:#e4e4e7;
  --muted:#71717a;
  --input-bg:#18181b;
  --glow:rgba(59,130,246,.12);
  --success:#34d399;
  --warn:#fbbf24;
}
body{
  font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif;
  background:var(--bg);
  color:var(--text);
  min-height:100vh;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  overflow-x:hidden;
  padding:20px 0;
}
body::before{
  content:'';
  position:fixed;inset:0;
  background-image:
    linear-gradient(rgba(255,255,255,.014) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.014) 1px,transparent 1px);
  background-size:56px 56px;
  pointer-events:none;z-index:0;
}
.wrapper{position:relative;z-index:1;width:min(600px,92vw)}
.glow-orb{
  position:absolute;top:-140px;left:50%;
  transform:translateX(-50%);
  width:500px;height:220px;
  background:radial-gradient(ellipse,rgba(59,130,246,.09) 0%,rgba(139,92,246,.05) 45%,transparent 70%);
  pointer-events:none;filter:blur(24px);
}
.card{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:18px;
  padding:40px 38px 34px;
  box-shadow:0 1px 2px rgba(0,0,0,.5),0 24px 64px rgba(0,0,0,.3);
}
.logo-row{display:flex;align-items:center;gap:14px;margin-bottom:6px}
.logo-icon{
  width:44px;height:44px;border-radius:12px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  display:flex;align-items:center;justify-content:center;
  font-size:22px;flex-shrink:0;
  box-shadow:0 0 24px rgba(59,130,246,.28);
}
h1{
  font-size:1.5rem;font-weight:700;letter-spacing:-.025em;
  background:linear-gradient(135deg,#f4f4f5 20%,#a1a1aa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.tagline{color:var(--muted);font-size:.83rem;margin:4px 0 26px 58px;line-height:1.5}
.badge-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
.badge{
  display:inline-flex;align-items:center;gap:5px;
  font-size:.68rem;text-transform:uppercase;letter-spacing:.09em;
  padding:4px 11px;border-radius:100px;
}
.badge.green{color:var(--success);background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.14)}
.badge.blue{color:#60a5fa;background:rgba(96,165,250,.08);border:1px solid rgba(96,165,250,.14)}
.badge.purple{color:#c084fc;background:rgba(192,132,252,.08);border:1px solid rgba(192,132,252,.14)}
.badge .dot{width:5px;height:5px;border-radius:50%;background:currentColor;animation:blink 2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}

/* tabs */
.tabs{display:flex;gap:2px;background:var(--bg);border-radius:10px;padding:3px;margin-bottom:22px}
.tab{
  flex:1;padding:8px;border:none;border-radius:8px;
  background:transparent;color:var(--muted);
  font-size:.82rem;font-family:inherit;font-weight:500;
  cursor:pointer;transition:all .15s;letter-spacing:.01em;
}
.tab.active{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.3)}

/* panels */
.panel{display:none}
.panel.active{display:block}

/* input group */
.input-wrap{position:relative;margin-bottom:12px}
.input-wrap input,.input-wrap textarea{
  width:100%;
  padding:13px 16px 13px 44px;
  border:1px solid var(--border);border-radius:10px;
  background:var(--input-bg);color:var(--text);
  font-size:.92rem;font-family:inherit;outline:none;
  transition:border-color .18s,box-shadow .18s;
  resize:vertical;
}
.input-wrap input:focus,.input-wrap textarea:focus{
  border-color:var(--accent);
  box-shadow:0 0 0 3px var(--glow);
}
.input-wrap input::placeholder,.input-wrap textarea::placeholder{color:#3f3f46}
.input-wrap .ico{
  position:absolute;left:14px;top:14px;
  color:var(--muted);font-size:1rem;pointer-events:none;
}

.row{display:flex;gap:10px}
.row .input-wrap{flex:1}

label.field-label{
  display:block;font-size:.72rem;
  text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);margin-bottom:6px;
}

/* button */
button.primary{
  width:100%;padding:13px;border:none;border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;font-size:.93rem;font-weight:600;font-family:inherit;
  cursor:pointer;letter-spacing:.01em;
  transition:opacity .15s,transform .1s,box-shadow .15s;
  box-shadow:0 2px 14px rgba(59,130,246,.22);
  margin-top:4px;
}
button.primary:hover{opacity:.91;transform:translateY(-1px);box-shadow:0 4px 20px rgba(59,130,246,.3)}
button.primary:active{transform:translateY(0);opacity:.84}
.loading button.primary{pointer-events:none;opacity:.65}

/* copy btn */
.copy-row{display:flex;gap:8px;align-items:center;margin-top:10px}
.copy-row input{
  flex:1;padding:10px 14px;
  border:1px solid var(--border);border-radius:8px;
  background:var(--bg);color:#a5b4fc;
  font-family:'SF Mono','Fira Code',monospace;font-size:.8rem;
  outline:none;
}
.copy-btn{
  padding:10px 16px;border:1px solid var(--border);border-radius:8px;
  background:var(--input-bg);color:var(--muted);
  font-size:.8rem;font-family:inherit;cursor:pointer;
  transition:all .15s;white-space:nowrap;
  flex-shrink:0;
}
.copy-btn:hover{color:var(--text);border-color:var(--accent)}

/* info section */
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:16px}
.info-card{
  padding:14px;background:var(--bg);
  border:1px solid var(--border);border-radius:10px;
}
.info-card .ic-title{font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin-bottom:6px}
.info-card .ic-list{list-style:none}
.info-card .ic-list li{font-size:.78rem;color:var(--text);padding:2px 0}
.info-card .ic-list li::before{content:"✓ ";color:var(--success)}
.info-card .ic-list li.no::before{content:"✗ ";color:#f87171}

/* stats */
.stats{display:flex;gap:1px;margin-top:16px;background:var(--border);border-radius:10px;overflow:hidden}
.stat{flex:1;padding:14px 8px;background:var(--card);text-align:center}
.stat:first-child{border-radius:10px 0 0 10px}
.stat:last-child{border-radius:0 10px 10px 0}
.stat .val{font-size:1.05rem;font-weight:700;color:var(--text);letter-spacing:-.02em}
.stat .lbl{font-size:.63rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-top:2px}

/* spinner */
.spinner{
  display:none;width:18px;height:18px;
  border:2px solid rgba(255,255,255,.25);border-top-color:#fff;
  border-radius:50%;animation:spin .55s linear infinite;margin:0 auto;
}
.loading .spinner{display:inline-block}
.loading .btn-text{display:none}
@keyframes spin{to{transform:rotate(360deg)}}

/* credit */
.credit{margin-top:26px;text-align:center;color:var(--muted);font-size:.76rem;letter-spacing:.02em}
.credit .sep{width:36px;height:1px;background:linear-gradient(90deg,transparent,var(--border),transparent);margin:10px auto}
.credit .name{
  font-weight:800;font-size:.9rem;letter-spacing:.12em;
  background:linear-gradient(135deg,#c084fc 0%,#60a5fa 50%,#34d399 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  display:inline-block;text-transform:uppercase;
}

@media(max-width:480px){
  .card{padding:28px 18px 24px}
  .tagline{margin-left:0;margin-top:8px}
  .logo-row{flex-wrap:wrap}
  .info-grid{grid-template-columns:1fr}
  .row{flex-direction:column;gap:0}
}
</style>
</head>
<body>
<div class="wrapper">
  <div class="glow-orb"></div>
  <div class="card">

    <div class="logo-row">
      <div class="logo-icon">⚡</div>
      <h1>Cloudflare Mirror</h1>
    </div>
    <p class="tagline">Stream any direct link through Cloudflare Edge — fast, private, best compatibility.</p>

    <div class="badge-row">
      <div class="badge green"><span class="dot"></span> Edge Active</div>
      <div class="badge blue">Retry + Resume</div>
      <div class="badge purple">Anti-Hotlink Bypass</div>
    </div>

    <!-- Tabs -->
    <div class="tabs">
      <button class="tab active" onclick="switchTab('simple',this)">⚡ Quick Mirror</button>
      <button class="tab" onclick="switchTab('advanced',this)">⚙️ Advanced</button>
      <button class="tab" onclick="switchTab('api',this)">📡 API</button>
    </div>

    <!-- Simple Tab -->
    <div class="panel active" id="tab-simple">
      <form id="f-simple">
        <div class="input-wrap">
          <span class="ico">🔗</span>
          <input id="u-simple" type="url" placeholder="Paste any direct download URL..." required autocomplete="off" spellcheck="false"/>
        </div>
        <button type="submit" class="primary" id="btn-simple">
          <span class="btn-text">⚡ Mirror Download</span>
          <span class="spinner"></span>
        </button>
      </form>
    </div>

    <!-- Advanced Tab -->
    <div class="panel" id="tab-advanced">
      <form id="f-adv">
        <div class="input-wrap">
          <span class="ico">🔗</span>
          <input id="u-adv" type="url" placeholder="Direct download URL..." required autocomplete="off" spellcheck="false"/>
        </div>
        <div class="row">
          <div>
            <label class="field-label">Custom Referer (optional)</label>
            <div class="input-wrap">
              <span class="ico">🌐</span>
              <input id="adv-ref" type="url" placeholder="https://source-site.com" autocomplete="off"/>
            </div>
          </div>
          <div>
            <label class="field-label">Cookie (optional)</label>
            <div class="input-wrap">
              <span class="ico">🍪</span>
              <input id="adv-cookie" type="text" placeholder="session=abc123" autocomplete="off"/>
            </div>
          </div>
        </div>
        <button type="submit" class="primary" id="btn-adv">
          <span class="btn-text">⚡ Mirror with Options</span>
          <span class="spinner"></span>
        </button>
      </form>
    </div>

    <!-- API Tab -->
    <div class="panel" id="tab-api">
      <div class="info-grid">
        <div class="info-card">
          <div class="ic-title">✅ Works Well</div>
          <ul class="ic-list">
            <li>Direct .zip .rar .iso</li>
            <li>Direct .mp4 .mp3 .apk</li>
            <li>Range / Resume requests</li>
            <li>Hotlink-protected hosts</li>
            <li>Signed / expiring URLs</li>
            <li>Retry on 429 / timeout</li>
          </ul>
        </div>
        <div class="info-card">
          <div class="ic-title">⚠️ Limitations</div>
          <ul class="ic-list">
            <li class="no">Google Drive pages</li>
            <li class="no">JS-based downloaders</li>
            <li class="no">Login-wall content</li>
            <li class="no">IP-bound tokens</li>
            <li class="no">CAPTCHA-protected</li>
            <li class="no">Streaming HLS/DASH</li>
          </ul>
        </div>
      </div>

      <div style="margin-top:16px">
        <label class="field-label">Basic</label>
        <div class="copy-row">
          <input id="api-basic" readonly value="/mirror?url=https://example.com/file.zip"/>
          <button class="copy-btn" onclick="copy('api-basic',this)">Copy</button>
        </div>
        <label class="field-label" style="margin-top:12px">Advanced (custom referer + cookie)</label>
        <div class="copy-row">
          <input id="api-adv" readonly value="/mirror?url=https://example.com/file.zip&referer=https://source.com&cookie=sess=abc"/>
          <button class="copy-btn" onclick="copy('api-adv',this)">Copy</button>
        </div>
      </div>
    </div>

    <div class="stats">
      <div class="stat"><div class="val">3×</div><div class="lbl">Auto Retry</div></div>
      <div class="stat"><div class="val">64KB</div><div class="lbl">Chunk Size</div></div>
      <div class="stat"><div class="val">4</div><div class="lbl">UA Rotation</div></div>
      <div class="stat"><div class="val">0</div><div class="lbl">Logs Stored</div></div>
    </div>

    <div class="credit">
      <div class="sep"></div>
      Crafted with ⚡ by <span class="name">Kobir Shah</span>
    </div>
  </div>
</div>

<script>
function switchTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}

function buildUrl(base, params) {
  var q = Object.entries(params).filter(([,v]) => v).map(([k,v]) => k+'='+encodeURIComponent(v)).join('&');
  return base + (q ? '?' + q : '');
}

document.getElementById('f-simple').addEventListener('submit', function(e) {
  e.preventDefault();
  var u = document.getElementById('u-simple').value.trim();
  if (!u) return;
  document.getElementById('f-simple').classList.add('loading');
  setTimeout(function() {
    window.location.href = '/mirror?url=' + encodeURIComponent(u);
  }, 280);
});

document.getElementById('f-adv').addEventListener('submit', function(e) {
  e.preventDefault();
  var u   = document.getElementById('u-adv').value.trim();
  var ref = document.getElementById('adv-ref').value.trim();
  var ck  = document.getElementById('adv-cookie').value.trim();
  if (!u) return;
  document.getElementById('f-adv').classList.add('loading');
  var params = {url: u};
  if (ref) params.referer = ref;
  if (ck)  params.cookie  = ck;
  setTimeout(function() {
    window.location.href = buildUrl('/mirror', params);
  }, 280);
});

function copy(id, btn) {
  var val = document.getElementById(id).value;
  navigator.clipboard.writeText(val).then(function() {
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy'; }, 1800);
  });
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return HOME_HTML


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "4.0"}


@app.get("/mirror")
async def mirror(
    request: Request,
    url:     str = Query(...),
    referer: str = Query(None),
    cookie:  str = Query(None),
):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    headers = build_upstream_headers(request, url)

    # Query-param overrides win over auto-detected values
    if referer:
        headers["Referer"] = referer
        headers["Origin"]  = referer.rstrip("/")
    if cookie:
        headers["Cookie"] = cookie

    timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=30.0)
    client  = httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        http2=False,
        verify=False,
    )

    try:
        upstream = await open_upstream(client, url, headers)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Upstream failed: {exc}")

    if upstream.status_code >= 400:
        body = await upstream.aread()
        await client.aclose()
        raise HTTPException(status_code=upstream.status_code,
                            detail=f"Upstream {upstream.status_code}: {body[:200].decode('utf-8','replace')}")

    filename     = extract_filename(str(upstream.url), upstream.headers)
    content_type = upstream.headers.get("content-type", "application/octet-stream")

    resp_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Powered-By":        "Kobir-Shah-Mirror-v4",
        "Cache-Control":       "no-store",
    }

    for h in ["content-length", "content-range", "accept-ranges", "etag", "last-modified"]:
        if h in upstream.headers:
            pretty = "-".join(p.capitalize() for p in h.split("-"))
            resp_headers[pretty] = upstream.headers[h]

    async def cleanup():
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(chunk_size=65536),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=resp_headers,
        background=BackgroundTask(cleanup),
    )
