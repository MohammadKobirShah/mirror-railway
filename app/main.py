from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from starlette.background import BackgroundTask
import httpx
import re
from urllib.parse import unquote, urlparse
from pathlib import PurePosixPath

app = FastAPI(title="⚡ Cloudflare Edge Mirror", version="3.0")

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive"
}


def extract_filename(source_url: str, headers) -> str:
    cd = headers.get("content-disposition", "")

    # filename*=UTF-8''name.ext
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1)).strip().strip('"')

    # filename="name.ext"
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # filename=name.ext
    m = re.search(r"filename\s*=\s*([^;]+)", cd, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')

    path_name = PurePosixPath(urlparse(source_url).path).name
    if path_name:
        return unquote(path_name)

    return "download.bin"


@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>⚡ CF Mirror</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
            body{
                font-family:Arial,sans-serif;
                background:#0f172a;
                color:#fff;
                display:flex;
                justify-content:center;
                align-items:center;
                min-height:100vh;
                margin:0;
            }
            .box{
                width:min(680px,92vw);
                background:#111827;
                padding:28px;
                border-radius:18px;
                box-shadow:0 10px 30px rgba(0,0,0,.35);
            }
            input{
                width:100%;
                padding:14px;
                border-radius:12px;
                border:none;
                margin:12px 0;
                background:#1f2937;
                color:#fff;
            }
            button{
                width:100%;
                padding:14px;
                border:none;
                border-radius:12px;
                background:#2563eb;
                color:#fff;
                font-weight:700;
                cursor:pointer;
            }
            code{
                background:#0b1220;
                padding:3px 8px;
                border-radius:8px;
            }
        </style>
    </head>
    <body>
        <div class="box">
            <h1>⚡ Cloudflare Tunnel Mirror</h1>
            <p>ডিরেক্ট লিংক পেস্ট করো, সার্ভার সেটা stream করবে।</p>
            <form id="f">
                <input id="u" type="text" placeholder="https://example.com/file.zip" required />
                <button type="submit">Mirror Download</button>
            </form>
            <p style="margin-top:16px"><code>/mirror?url=https://example.com/file.zip</code></p>
        </div>
        <script>
            document.getElementById('f').addEventListener('submit', function(e){
                e.preventDefault();
                const u = document.getElementById('u').value.trim();
                if(u) location.href = '/mirror?url=' + encodeURIComponent(u);
            });
        </script>
    </body>
    </html>
    """


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

    # Resume support
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
        "X-Mirror-Source": str(upstream.url)[:200],
        "X-Powered-By": "ENI-CF-Mirror-v3",
        "Cache-Control": "no-store"
    }

    passthrough_headers = [
        "content-length",
        "content-range",
        "accept-ranges",
        "etag",
        "last-modified",
        "content-encoding"
    ]

    for h in passthrough_headers:
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
