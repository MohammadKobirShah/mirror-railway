from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, FileResponse
from starlette.background import BackgroundTask
import httpx
import re
import os
import asyncio
import hashlib
import time
import json
from urllib.parse import unquote, urlparse, quote
from pathlib import PurePosixPath, Path

app = FastAPI(title="⚡ BDIX Local Cache Mirror", version="7.1")

# ─── Config ────────────────────────────────────────────────────
CACHE_DIR      = Path("/app/cache")
META_DIR       = Path("/app/cache/.meta")
MAX_CACHE_SIZE = 50  * 1024 * 1024 * 1024  # 50GB
MAX_FILE_SIZE  = 10  * 1024 * 1024 * 1024  # 10GB
CACHE_TTL      = 86400 * 7                  # 7 days
CHUNK_SIZE     = 256 * 1024                 # 256KB

CACHE_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]
_ua_idx = 0

def next_ua() -> str:
    global _ua_idx
    ua = UA_POOL[_ua_idx % len(UA_POOL)]
    _ua_idx += 1
    return ua


active_downloads: dict[str, asyncio.Event] = {}


def url_to_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def safe_filename(name: str) -> str:
    """
    যেকোনো string থেকে safe filename বানায়।
    slash, backslash, null byte সব strip করে।
    """
    # শুধু basename নাও — slash থাকলে শেষ part নাও
    name = name.replace("\\", "/").split("/")[-1]
    # Null bytes এবং control chars সরাও
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)
    # Dangerous chars replace করো
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip(". ")
    return name or "download.bin"


def extract_filename(source_url: str, headers) -> str:
    cd = headers.get("content-disposition", "")

    m = re.search(r"filename\*\s*=\s*UTF-8''([^;\s]+)", cd, re.IGNORECASE)
    if m:
        raw = unquote(m.group(1)).strip().strip('"\'')
        return safe_filename(raw)

    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m:
        return safe_filename(m.group(1))

    m = re.search(r"filename\s*=\s*([^;\s]+)", cd, re.IGNORECASE)
    if m:
        return safe_filename(m.group(1))

    # URL path থেকে বের করো
    path_name = PurePosixPath(urlparse(source_url).path).name
    if path_name:
        return safe_filename(unquote(path_name))

    return "download.bin"


def get_file_dir(url_hash: str) -> Path:
    d = CACHE_DIR / url_hash
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_meta_path(url_hash: str) -> Path:
    return META_DIR / f"{url_hash}.json"


def save_meta(url_hash: str, data: dict):
    get_meta_path(url_hash).write_text(json.dumps(data))


def load_meta(url_hash: str) -> dict | None:
    p = get_meta_path(url_hash)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def is_cache_valid(url_hash: str) -> bool:
    meta = load_meta(url_hash)
    if not meta:
        return False
    if meta.get("status") != "complete":
        return False
    filename = meta.get("filename", "")
    if not filename:
        return False
    cache_path = CACHE_DIR / url_hash / filename
    if not cache_path.exists():
        return False
    age = time.time() - meta.get("cached_at", 0)
    return age <= CACHE_TTL


def get_total_cache_size() -> int:
    total = 0
    for f in CACHE_DIR.rglob("*"):
        if f.is_file() and META_DIR not in f.parents:
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def cleanup_old_cache():
    entries = []
    for meta_file in META_DIR.glob("*.json"):
        try:
            data = json.loads(meta_file.read_text())
            entries.append((data.get("cached_at", 0), meta_file.stem, data))
        except Exception:
            meta_file.unlink(missing_ok=True)

    entries.sort()

    while get_total_cache_size() > int(MAX_CACHE_SIZE * 0.85) and entries:
        _, url_hash, data = entries.pop(0)
        fn = data.get("filename", "")
        if fn:
            fp = CACHE_DIR / url_hash / fn
            fp.unlink(missing_ok=True)
        d = CACHE_DIR / url_hash
        try:
            d.rmdir()
        except OSError:
            pass
        get_meta_path(url_hash).unlink(missing_ok=True)


def build_headers(target_url: str, referer: str = None, cookie: str = None) -> dict:
    parsed   = urlparse(target_url)
    auto_ref = f"{parsed.scheme}://{parsed.netloc}/"
    headers  = {
        "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept":                    "*/*",
        "Accept-Encoding":           "identity",
        "Referer":                   referer or auto_ref,
        "Origin":                    (referer or auto_ref).rstrip("/"),
        "DNT":                       "1",
        "Connection":                "keep-alive",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "cross-site",
        "Upgrade-Insecure-Requests": "1",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


# ─── Background downloader ─────────────────────────────────────

async def background_download(
    url: str, url_hash: str,
    referer: str, cookie: str,
    event: asyncio.Event,
):
    try:
        headers = build_headers(url, referer, cookie)
        timeout = httpx.Timeout(connect=30.0, read=120.0, write=None, pool=30.0)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            http2=False,
            verify=False,
        ) as client:

            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    save_meta(url_hash, {
                        "status": "error",
                        "detail": f"Source returned HTTP {resp.status_code}"
                    })
                    return

                filename       = extract_filename(url, dict(resp.headers))
                content_type   = resp.headers.get("content-type", "application/octet-stream")
                content_length = int(resp.headers.get("content-length", 0))

                file_dir  = get_file_dir(url_hash)
                file_path = file_dir / filename
                temp_path = file_dir / (filename + ".tmp")

                downloaded = 0

                save_meta(url_hash, {
                    "status":     "downloading",
                    "url":        url,
                    "filename":   filename,
                    "progress":   0,
                    "downloaded": 0,
                    "total":      content_length,
                })

                with open(temp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=CHUNK_SIZE):
                        f.write(chunk)
                        downloaded += len(chunk)

                        if downloaded > MAX_FILE_SIZE:
                            temp_path.unlink(missing_ok=True)
                            save_meta(url_hash, {"status": "error", "detail": "File exceeded 10GB limit"})
                            return

                        progress = int((downloaded / content_length) * 100) if content_length else 0

                        save_meta(url_hash, {
                            "status":     "downloading",
                            "url":        url,
                            "filename":   filename,
                            "progress":   min(progress, 99),
                            "downloaded": downloaded,
                            "total":      content_length,
                        })

                # Download complete — atomic rename
                temp_path.rename(file_path)

                save_meta(url_hash, {
                    "status":       "complete",
                    "url":          url,
                    "filename":     filename,
                    "content_type": content_type,
                    "size":         downloaded,
                    "cached_at":    time.time(),
                })

    except Exception as e:
        save_meta(url_hash, {"status": "error", "detail": str(e)})
    finally:
        event.set()
        active_downloads.pop(url_hash, None)
        if get_total_cache_size() > MAX_CACHE_SIZE:
            cleanup_old_cache()


# ─── HTML ──────────────────────────────────────────────────────

HOME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BDIX Cache Mirror — by Kobir Shah</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#09090b;--card:#111113;--border:#1e1e22;
  --accent:#3b82f6;--accent2:#8b5cf6;
  --text:#e4e4e7;--muted:#71717a;
  --input-bg:#18181b;--glow:rgba(59,130,246,.12);
  --success:#34d399;
}
body{
  font-family:'Inter','Segoe UI',system-ui,sans-serif;
  background:var(--bg);color:var(--text);
  min-height:100vh;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  overflow-x:hidden;padding:20px 0;
}
body::before{
  content:'';position:fixed;inset:0;
  background-image:
    linear-gradient(rgba(255,255,255,.013) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.013) 1px,transparent 1px);
  background-size:56px 56px;pointer-events:none;z-index:0;
}
.wrapper{position:relative;z-index:1;width:min(620px,92vw)}
.glow-orb{
  position:absolute;top:-130px;left:50%;transform:translateX(-50%);
  width:500px;height:220px;
  background:radial-gradient(ellipse,rgba(59,130,246,.09) 0%,rgba(139,92,246,.05) 45%,transparent 70%);
  pointer-events:none;filter:blur(24px);
}
.card{
  background:var(--card);border:1px solid var(--border);
  border-radius:18px;padding:40px 38px 34px;
  box-shadow:0 1px 2px rgba(0,0,0,.5),0 24px 64px rgba(0,0,0,.3);
}
.logo-row{display:flex;align-items:center;gap:14px;margin-bottom:6px}
.logo-icon{
  width:44px;height:44px;border-radius:12px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  display:flex;align-items:center;justify-content:center;
  font-size:22px;flex-shrink:0;box-shadow:0 0 24px rgba(59,130,246,.28);
}
h1{
  font-size:1.45rem;font-weight:700;letter-spacing:-.025em;
  background:linear-gradient(135deg,#f4f4f5 20%,#a1a1aa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.tagline{color:var(--muted);font-size:.82rem;margin:4px 0 22px 58px;line-height:1.5}
.badge-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:22px}
.badge{
  display:inline-flex;align-items:center;gap:5px;
  font-size:.67rem;text-transform:uppercase;letter-spacing:.09em;
  padding:4px 11px;border-radius:100px;
}
.badge.green{color:var(--success);background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.14)}
.badge.blue{color:#60a5fa;background:rgba(96,165,250,.08);border:1px solid rgba(96,165,250,.14)}
.badge.purple{color:#c084fc;background:rgba(192,132,252,.08);border:1px solid rgba(192,132,252,.14)}
.badge.orange{color:#fb923c;background:rgba(251,146,60,.08);border:1px solid rgba(251,146,60,.14)}
.badge .dot{width:5px;height:5px;border-radius:50%;background:currentColor;animation:blink 2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
.tabs{display:flex;gap:2px;background:var(--bg);border-radius:10px;padding:3px;margin-bottom:20px}
.tab{flex:1;padding:9px 6px;border:none;border-radius:8px;background:transparent;color:var(--muted);font-size:.8rem;font-family:inherit;font-weight:500;cursor:pointer;transition:all .15s}
.tab.active{background:var(--card);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.3)}
.panel{display:none}.panel.active{display:block}
.input-wrap{position:relative;margin-bottom:12px}
.input-wrap input{width:100%;padding:13px 16px 13px 44px;border:1px solid var(--border);border-radius:10px;background:var(--input-bg);color:var(--text);font-size:.92rem;font-family:inherit;outline:none;transition:border-color .18s,box-shadow .18s}
.input-wrap input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--glow)}
.input-wrap input::placeholder{color:#3f3f46}
.input-wrap .ico{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:1rem;pointer-events:none}
.row2{display:flex;gap:10px}.row2>div{flex:1}
label.fl{display:block;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:6px}
button.primary{width:100%;padding:13px;border:none;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font-size:.93rem;font-weight:600;font-family:inherit;cursor:pointer;transition:opacity .15s,transform .1s;box-shadow:0 2px 14px rgba(59,130,246,.22);margin-top:4px}
button.primary:hover{opacity:.91;transform:translateY(-1px)}
button.primary:active{transform:translateY(0)}
.loading button.primary{pointer-events:none;opacity:.65}
.progress-area{margin-top:16px;padding:18px;display:none;background:var(--bg);border:1px solid var(--border);border-radius:12px}
.progress-area.show{display:block}
.p-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:8px}
.p-text{font-size:.88rem;color:var(--text);margin-bottom:10px;font-weight:500}
.progress-bar{width:100%;height:8px;background:var(--border);border-radius:4px;overflow:hidden}
.progress-bar .fill{height:100%;width:0%;background:linear-gradient(90deg,var(--accent),var(--success));border-radius:4px;transition:width .4s ease}
.p-detail{font-size:.72rem;color:var(--muted);margin-top:8px}
.flow-box{margin-top:16px;padding:16px;background:var(--bg);border:1px solid var(--border);border-radius:12px}
.flow-title{font-size:.68rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin-bottom:10px}
.flow{display:flex;align-items:center;justify-content:center;gap:5px;flex-wrap:wrap}
.fs{padding:6px 12px;background:var(--card);border:1px solid var(--border);border-radius:8px;font-size:.72rem;color:var(--text);text-align:center}
.fa{color:var(--muted);font-size:.85rem}
.fs.h{border-color:var(--accent);color:#60a5fa}
.fs.g{border-color:var(--success);color:var(--success)}
.stats{display:flex;gap:1px;margin-top:16px;background:var(--border);border-radius:10px;overflow:hidden}
.stat{flex:1;padding:13px 6px;background:var(--card);text-align:center}
.stat:first-child{border-radius:10px 0 0 10px}
.stat:last-child{border-radius:0 10px 10px 0}
.stat .val{font-size:1rem;font-weight:700;color:var(--text);letter-spacing:-.02em}
.stat .lbl{font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-top:2px}
.spinner{display:none;width:18px;height:18px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin .55s linear infinite;margin:0 auto}
.loading .spinner{display:inline-block}
.loading .btn-text{display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.credit{margin-top:24px;text-align:center;color:var(--muted);font-size:.76rem}
.sep{width:36px;height:1px;background:linear-gradient(90deg,transparent,var(--border),transparent);margin:10px auto}
.name{font-weight:800;font-size:.9rem;letter-spacing:.12em;background:linear-gradient(135deg,#c084fc 0%,#60a5fa 50%,#34d399 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;display:inline-block;text-transform:uppercase}
@media(max-width:480px){.card{padding:26px 16px 22px}.tagline{margin-left:0;margin-top:8px}.logo-row{flex-wrap:wrap}.row2{flex-direction:column}}
</style>
</head>
<body>
<div class="wrapper">
  <div class="glow-orb"></div>
  <div class="card">
    <div class="logo-row">
      <div class="logo-icon">⚡</div>
      <h1>BDIX Cache Mirror</h1>
    </div>
    <p class="tagline">Downloads to local cache first, then serves via Cloudflare Dhaka Edge at full BDIX speed.</p>
    <div class="badge-row">
      <div class="badge green"><span class="dot"></span> BDIX Speed</div>
      <div class="badge blue">Local Disk Cache</div>
      <div class="badge purple">CF Edge Serve</div>
      <div class="badge orange">7 Day TTL</div>
    </div>
    <div class="tabs">
      <button class="tab active" onclick="sw('q',this)">⚡ Quick</button>
      <button class="tab" onclick="sw('a',this)">⚙️ Advanced</button>
      <button class="tab" onclick="sw('h',this)">ℹ️ How It Works</button>
    </div>

    <div class="panel active" id="p-q">
      <form id="f1">
        <div class="input-wrap">
          <span class="ico">🔗</span>
          <input id="u1" type="url" placeholder="Paste direct download URL..." required autocomplete="off" spellcheck="false"/>
        </div>
        <button type="submit" class="primary">
          <span class="btn-text">⚡ Cache &amp; Download</span>
          <span class="spinner"></span>
        </button>
      </form>
      <div class="progress-area" id="prog">
        <div class="p-label">Download Progress</div>
        <div class="p-text" id="p-text">Initializing...</div>
        <div class="progress-bar"><div class="fill" id="p-fill"></div></div>
        <div class="p-detail" id="p-detail">Please wait...</div>
      </div>
    </div>

    <div class="panel" id="p-a">
      <form id="f2">
        <div class="input-wrap">
          <span class="ico">🔗</span>
          <input id="u2" type="url" placeholder="Direct download URL..." required autocomplete="off" spellcheck="false"/>
        </div>
        <div class="row2">
          <div>
            <label class="fl">Custom Referer</label>
            <div class="input-wrap">
              <span class="ico">🌐</span>
              <input id="r2" type="url" placeholder="https://source.com" autocomplete="off"/>
            </div>
          </div>
          <div>
            <label class="fl">Cookie</label>
            <div class="input-wrap">
              <span class="ico">🍪</span>
              <input id="c2" type="text" placeholder="session=abc" autocomplete="off"/>
            </div>
          </div>
        </div>
        <button type="submit" class="primary">
          <span class="btn-text">⚡ Cache &amp; Download</span>
          <span class="spinner"></span>
        </button>
      </form>
    </div>

    <div class="panel" id="p-h">
      <div class="flow-box">
        <div class="flow-title">First Download</div>
        <div class="flow">
          <div class="fs">Source</div><div class="fa">→</div>
          <div class="fs h">App saves to disk</div><div class="fa">→</div>
          <div class="fs h">CF Dhaka Edge</div><div class="fa">→</div>
          <div class="fs g">BDIX → You 🚀</div>
        </div>
      </div>
      <div class="flow-box" style="margin-top:8px">
        <div class="flow-title">Cached Download</div>
        <div class="flow">
          <div class="fs g">Local disk → CF Edge → BDIX → You 🚀🚀</div>
        </div>
      </div>
    </div>

    <div class="stats">
      <div class="stat"><div class="val">🚀</div><div class="lbl">BDIX</div></div>
      <div class="stat"><div class="val">50GB</div><div class="lbl">Cache Pool</div></div>
      <div class="stat"><div class="val">7d</div><div class="lbl">TTL</div></div>
      <div class="stat"><div class="val">256KB</div><div class="lbl">Chunks</div></div>
    </div>

    <div class="credit">
      <div class="sep"></div>
      Crafted with ⚡ by <span class="name">Kobir Shah</span>
    </div>
  </div>
</div>
<script>
function sw(n,el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('p-'+n).classList.add('active');
}

function fmt(b){
  if(!b)return '...';
  if(b>1073741824)return(b/1073741824).toFixed(2)+' GB';
  if(b>1048576)return(b/1048576).toFixed(1)+' MB';
  if(b>1024)return(b/1024).toFixed(0)+' KB';
  return b+' B';
}

async function doMirror(url,ref,ck){
  var prog=document.getElementById('prog');
  var pText=document.getElementById('p-text');
  var pFill=document.getElementById('p-fill');
  var pDetail=document.getElementById('p-detail');
  prog.classList.add('show');
  pText.textContent='Checking cache...';
  pFill.style.width='5%';
  pDetail.textContent='';

  var q='url='+encodeURIComponent(url);
  if(ref)q+='&referer='+encodeURIComponent(ref);
  if(ck)q+='&cookie='+encodeURIComponent(ck);

  try{
    var res=await fetch('/api/cache?'+q,{method:'POST'});
    var data=await res.json();

    if(data.status==='ready'){
      pText.textContent='Already cached! Starting download...';
      pFill.style.width='100%';
      pDetail.textContent=data.filename+' — '+fmt(data.size);
      setTimeout(()=>{ window.location.href=data.download_url; },500);
    } else if(data.status==='downloading'){
      pText.textContent='Downloading to cache...';
      pFill.style.width='10%';
      pollStatus(q);
    } else {
      pText.textContent='Error: '+(data.detail||'Unknown');
      pFill.style.width='0%';
    }
  } catch(e){
    pText.textContent='Network error: '+e.message;
  }
}

function pollStatus(q){
  var pText=document.getElementById('p-text');
  var pFill=document.getElementById('p-fill');
  var pDetail=document.getElementById('p-detail');
  var iv=setInterval(async()=>{
    try{
      var res=await fetch('/api/status?'+q);
      var d=await res.json();
      if(d.status==='complete'){
        clearInterval(iv);
        pText.textContent='Cached! Starting download...';
        pFill.style.width='100%';
        pDetail.textContent=d.filename+' — '+fmt(d.size);
        setTimeout(()=>{ window.location.href=d.download_url; },600);
      } else if(d.status==='downloading'){
        var pct=d.progress||0;
        pFill.style.width=Math.max(pct,10)+'%';
        pText.textContent='Caching... '+pct+'%';
        pDetail.textContent=fmt(d.downloaded)+' / '+fmt(d.total);
      } else if(d.status==='error'){
        clearInterval(iv);
        pText.textContent='Error: '+d.detail;
        pFill.style.width='0%';
      }
    } catch(e){}
  },1200);
}

function sub(fid,uid,rid,cid){
  document.getElementById(fid).addEventListener('submit',function(e){
    e.preventDefault();
    var u=document.getElementById(uid).value.trim();
    if(!u)return;
    this.classList.add('loading');
    var r=rid?document.getElementById(rid).value.trim():'';
    var c=cid?document.getElementById(cid).value.trim():'';
    doMirror(u,r,c);
  });
}
sub('f1','u1');
sub('f2','u2','r2','c2');
</script>
</body>
</html>"""


# ─── Routes ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home():
    return HOME_HTML


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/health")
async def health():
    cache_mb = get_total_cache_size() / 1024 / 1024
    return {"status": "ok", "version": "7.1", "cache_used_mb": round(cache_mb, 1)}


@app.post("/api/cache")
async def api_cache(
    url:     str = Query(...),
    referer: str = Query(None),
    cookie:  str = Query(None),
):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL")

    url_hash = url_to_hash(url)

    if is_cache_valid(url_hash):
        meta = load_meta(url_hash)
        fn   = meta["filename"]
        return {
            "status":       "ready",
            "filename":     fn,
            "size":         meta["size"],
            "download_url": f"/dl/{url_hash}/{quote(fn, safe='')}",
        }

    if url_hash in active_downloads:
        return {"status": "downloading"}

    event = asyncio.Event()
    active_downloads[url_hash] = event

    save_meta(url_hash, {
        "status": "downloading", "url": url,
        "progress": 0, "downloaded": 0, "total": 0,
    })

    asyncio.create_task(
        background_download(url, url_hash, referer, cookie, event)
    )

    return {"status": "downloading"}


@app.get("/api/status")
async def api_status(url: str = Query(...)):
    url_hash = url_to_hash(url)
    meta     = load_meta(url_hash)

    if not meta:
        return {"status": "not_found"}

    if meta["status"] == "complete":
        fn = meta["filename"]
        return {
            "status":       "complete",
            "filename":     fn,
            "size":         meta["size"],
            "download_url": f"/dl/{url_hash}/{quote(fn, safe='')}",
        }

    return meta


@app.get("/dl/{url_hash}/{filename}")
async def serve_file(url_hash: str, filename: str):
    meta = load_meta(url_hash)

    if not meta or meta.get("status") != "complete":
        raise HTTPException(status_code=404, detail="File not cached yet")

    real_fn   = meta["filename"]
    file_path = CACHE_DIR / url_hash / real_fn

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Cached file not found on disk")

    ttl = 86400 * 7

    return FileResponse(
        path=str(file_path),
        filename=real_fn,
        media_type=meta.get("content_type", "application/octet-stream"),
        headers={
            "Cache-Control":                f"public, max-age={ttl}, s-maxage={ttl}",
            "CDN-Cache-Control":            f"public, max-age={ttl}",
            "Cloudflare-CDN-Cache-Control": f"public, max-age={ttl}",
            "Accept-Ranges":                "bytes",
            "X-Powered-By":                 "Kobir-Shah-BDIX-v7.1",
        },
    )
