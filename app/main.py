from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from starlette.background import BackgroundTask
import httpx
import re
from urllib.parse import unquote, urlparse
from pathlib import PurePosixPath

app = FastAPI(title="⚡ CF Edge Mirror", version="3.1")

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive"
}


def extract_filename(source_url: str, headers) -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1)).strip().strip('"')
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"filename\s*=\s*([^;]+)", cd, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    path_name = PurePosixPath(urlparse(source_url).path).name
    if path_name:
        return unquote(path_name)
    return "download.bin"


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
}
@font-face{
  font-family:'Inter';
  src:local('Inter'),local('Segoe UI'),local('system-ui');
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
}

/* subtle grid bg */
body::before{
  content:'';
  position:fixed;
  inset:0;
  background-image:
    linear-gradient(rgba(255,255,255,.015) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.015) 1px,transparent 1px);
  background-size:64px 64px;
  pointer-events:none;
  z-index:0;
}

.wrapper{
  position:relative;
  z-index:1;
  width:min(580px,92vw);
}

/* top glow */
.glow-orb{
  position:absolute;
  top:-120px;
  left:50%;
  transform:translateX(-50%);
  width:400px;
  height:200px;
  background:radial-gradient(ellipse,rgba(59,130,246,.10) 0%,rgba(139,92,246,.06) 40%,transparent 70%);
  pointer-events:none;
  filter:blur(20px);
}

.card{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:16px;
  padding:40px 36px 32px;
  box-shadow:0 1px 3px rgba(0,0,0,.4),0 20px 60px rgba(0,0,0,.25);
}

/* header */
.logo-row{
  display:flex;
  align-items:center;
  gap:12px;
  margin-bottom:6px;
}
.logo-icon{
  width:40px;height:40px;
  border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  display:flex;align-items:center;justify-content:center;
  font-size:20px;
  flex-shrink:0;
  box-shadow:0 0 20px rgba(59,130,246,.25);
}
h1{
  font-size:1.5rem;
  font-weight:700;
  letter-spacing:-.02em;
  background:linear-gradient(135deg,#f0f0f0 30%,#a0a0a0);
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
}
.tagline{
  color:var(--muted);
  font-size:.85rem;
  margin:4px 0 28px 52px;
  letter-spacing:.01em;
}

/* status pill */
.status{
  display:inline-flex;
  align-items:center;
  gap:6px;
  font-size:.72rem;
  text-transform:uppercase;
  letter-spacing:.08em;
  color:#34d399;
  background:rgba(52,211,153,.08);
  padding:4px 12px;
  border-radius:100px;
  border:1px solid rgba(52,211,153,.15);
  margin-bottom:24px;
}
.status .dot{
  width:6px;height:6px;
  border-radius:50%;
  background:#34d399;
  animation:blink 2s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

/* form */
.input-group{
  position:relative;
  margin-bottom:14px;
}
.input-group input{
  width:100%;
  padding:14px 16px 14px 44px;
  border:1px solid var(--border);
  border-radius:10px;
  background:var(--input-bg);
  color:var(--text);
  font-size:.95rem;
  font-family:inherit;
  outline:none;
  transition:border-color .2s,box-shadow .2s;
}
.input-group input:focus{
  border-color:var(--accent);
  box-shadow:0 0 0 3px var(--glow);
}
.input-group input::placeholder{color:#52525b}
.input-group .icon{
  position:absolute;
  left:14px;top:50%;
  transform:translateY(-50%);
  color:var(--muted);
  font-size:1.1rem;
  pointer-events:none;
}

button{
  width:100%;
  padding:13px;
  border:none;
  border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;
  font-size:.95rem;
  font-weight:600;
  font-family:inherit;
  cursor:pointer;
  letter-spacing:.01em;
  transition:opacity .15s,transform .1s;
  box-shadow:0 2px 12px rgba(59,130,246,.25);
}
button:hover{opacity:.92;transform:translateY(-1px)}
button:active{transform:translateY(0);opacity:.85}

/* API box */
.api-box{
  margin-top:24px;
  padding:16px;
  background:var(--bg);
  border:1px solid var(--border);
  border-radius:10px;
}
.api-box .label{
  font-size:.7rem;
  text-transform:uppercase;
  letter-spacing:.1em;
  color:var(--muted);
  margin-bottom:8px;
}
.api-box code{
  display:block;
  font-family:'SF Mono','Cascadia Code','Fira Code',monospace;
  font-size:.82rem;
  color:#a5b4fc;
  word-break:break-all;
  line-height:1.5;
}
.api-box code .method{color:#34d399;font-weight:700}
.api-box code .path{color:#e4e4e7}
.api-box code .param{color:#fbbf24}

/* stats row */
.stats{
  display:flex;
  gap:1px;
  margin-top:16px;
  background:var(--border);
  border-radius:10px;
  overflow:hidden;
}
.stat{
  flex:1;
  padding:14px 8px;
  background:var(--card);
  text-align:center;
}
.stat:first-child{border-radius:10px 0 0 10px}
.stat:last-child{border-radius:0 10px 10px 0}
.stat .val{
  font-size:1.1rem;
  font-weight:700;
  color:var(--text);
  letter-spacing:-.02em;
}
.stat .lbl{
  font-size:.65rem;
  text-transform:uppercase;
  letter-spacing:.08em;
  color:var(--muted);
  margin-top:2px;
}

/* footer credit */
.credit{
  margin-top:28px;
  text-align:center;
  color:var(--muted);
  font-size:.78rem;
  letter-spacing:.02em;
}
.credit .name{
  font-weight:700;
  font-size:.85rem;
  letter-spacing:.06em;
  background:linear-gradient(135deg,#c084fc,#60a5fa,#34d399);
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
  display:inline-block;
}
.credit .sep{
  display:block;
  width:40px;
  height:1px;
  background:linear-gradient(90deg,transparent,var(--border),transparent);
  margin:10px auto;
}

/* loading state */
.loading button{pointer-events:none;opacity:.7}
.spinner{
  display:none;
  width:18px;height:18px;
  border:2px solid rgba(255,255,255,.3);
  border-top-color:#fff;
  border-radius:50%;
  animation:spin .6s linear infinite;
  margin:0 auto;
}
.loading .spinner{display:inline-block}
.loading .btn-text{display:none}
@keyframes spin{to{transform:rotate(360deg)}}

/* responsive */
@media(max-width:480px){
  .card{padding:28px 20px 24px}
  h1{font-size:1.25rem}
  .tagline{margin-left:0;margin-top:8px}
  .logo-row{flex-wrap:wrap}
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
    <p class="tagline">Stream any direct link through CF Edge — fast, private, zero logs.</p>

    <div class="status"><span class="dot"></span> Edge Network Active</div>

    <form id="f">
      <div class="input-group">
        <span class="icon">🔗</span>
        <input id="u" type="url" placeholder="Paste direct download URL here..." required autocomplete="off" spellcheck="false"/>
      </div>
      <button type="submit" id="btn">
        <span class="btn-text">⚡ Start Mirror Download</span>
        <span class="spinner"></span>
      </button>
    </form>

    <div class="api-box">
      <div class="label">API Endpoint</div>
      <code><span class="method">GET</span> <span class="path">/mirror?url=</span><span class="param">{direct_url}</span></code>
    </div>

    <div class="stats">
      <div class="stat"><div class="val">200+</div><div class="lbl">Edge PoPs</div></div>
      <div class="stat"><div class="val">64KB</div><div class="lbl">Chunk Size</div></div>
      <div class="stat"><div class="val">0</div><div class="lbl">Logs Stored</div></div>
    </div>

    <div class="credit">
      <div class="sep"></div>
      Crafted by <span class="name">KOBIR SHAH</span>
    </div>
  </div>
</div>

<script>
document.getElementById('f').addEventListener('submit',function(e){
  e.preventDefault();
  var u=document.getElementById('u').value.trim();
  if(!u)return;
  document.getElementById('f').classList.add('loading');
  setTimeout(function(){
    window.location.href='/mirror?url='+encodeURIComponent(u);
  },300);
});
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
    return {"status": "ok"}


@app.get("/mirror")
async def mirror(request: Request, url: str = Query(..., description="Direct file URL")):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    headers = dict(BASE_HEADERS)

    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    timeout = httpx.Timeout(connect=30.0, read=None, write=60.0, pool=60.0)
    client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        http2=False
    )

    try:
        req = client.build_request("GET", url, headers=headers)
        upstream = await client.send(req, stream=True)
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Upstream connection failed: {str(e)}")

    if upstream.status_code >= 400:
        detail = f"Upstream returned {upstream.status_code}"
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=upstream.status_code, detail=detail)

    filename = extract_filename(str(upstream.url), upstream.headers)
    content_type = upstream.headers.get("content-type", "application/octet-stream")

    response_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Powered-By": "Kobir-Shah-Mirror",
        "Cache-Control": "no-store"
    }

    for h in ["content-length", "content-range", "accept-ranges", "etag", "last-modified"]:
        if h in upstream.headers:
            pretty = "-".join(part.capitalize() for part in h.split("-"))
            response_headers[pretty] = upstream.headers[h]

    async def cleanup():
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(chunk_size=1024 * 64),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=response_headers,
        background=BackgroundTask(cleanup)
    )
