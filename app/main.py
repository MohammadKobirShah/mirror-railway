from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
import httpx
import re
from urllib.parse import unquote

app = FastAPI(title="⚡ Cloudflare Edge Mirror", version="2.0")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive"
}

@app.get("/", response_class=HTMLResponse)
async def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>⚡ Pro Mirror Server</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Segoe UI', sans-serif; 
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                color: #fff;
            }
            .container {
                background: rgba(255,255,255,0.05);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                max-width: 600px;
                width: 90%;
                box-shadow: 0 8px 32px rgba(0,0,0,0.3);
                border: 1px solid rgba(255,255,255,0.1);
            }
            h1 { 
                font-size: 2.5em; 
                margin-bottom: 10px;
                background: linear-gradient(90deg, #f093fb, #f5576c);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .subtitle { color: #888; margin-bottom: 30px; }
            input[type="text"] {
                width: 100%;
                padding: 15px 20px;
                border: none;
                border-radius: 10px;
                background: rgba(255,255,255,0.1);
                color: #fff;
                font-size: 16px;
                margin-bottom: 15px;
                outline: none;
            }
            input::placeholder { color: #666; }
            button {
                width: 100%;
                padding: 15px;
                border: none;
                border-radius: 10px;
                background: linear-gradient(90deg, #f093fb, #f5576c);
                color: #fff;
                font-size: 18px;
                font-weight: bold;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
            }
            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 20px rgba(240,147,251,0.4);
            }
            .info {
                margin-top: 30px;
                padding: 20px;
                background: rgba(0,0,0,0.2);
                border-radius: 10px;
                font-size: 14px;
            }
            code {
                background: rgba(255,255,255,0.1);
                padding: 3px 8px;
                border-radius: 5px;
                font-family: 'Fira Code', monospace;
            }
            .status { 
                display: inline-block;
                width: 10px; height: 10px;
                background: #00ff88;
                border-radius: 50%;
                margin-right: 8px;
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚡ Pro Mirror</h1>
            <p class="subtitle"><span class="status"></span>Cloudflare Edge Powered</p>
            
            <form id="mirrorForm">
                <input type="text" id="urlInput" placeholder="পেস্ট করো ডিরেক্ট ডাউনলোড লিঙ্ক..." required>
                <button type="submit">🚀 মিরর করো</button>
            </form>
            
            <div class="info">
                <p><strong>API ব্যবহার:</strong></p>
                <p style="margin-top:10px"><code>GET /mirror?url=https://example.com/file.zip</code></p>
            </div>
        </div>
        
        <script>
            document.getElementById('mirrorForm').addEventListener('submit', function(e) {
                e.preventDefault();
                const url = document.getElementById('urlInput').value;
                if(url) {
                    window.location.href = '/mirror?url=' + encodeURIComponent(url);
                }
            });
        </script>
    </body>
    </html>
    """
    return html


@app.get("/mirror")
async def mirror(url: str = Query(..., description="Direct download URL")):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, 
            timeout=300.0,
            limits=httpx.Limits(max_connections=100)
        ) as client:
            
            # HEAD request first to get headers
            head_resp = await client.head(url, headers=HEADERS)
            content_length = head_resp.headers.get("content-length")
            content_type = head_resp.headers.get("content-type", "application/octet-stream")
            
            # Extract filename
            filename = "downloaded_file"
            cd = head_resp.headers.get("content-disposition", "")
            
            if "filename=" in cd:
                match = re.findall(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';]+)', cd, re.IGNORECASE)
                if match:
                    filename = unquote(match[0])
            else:
                path_filename = url.split("/")[-1].split("?")[0]
                if path_filename and "." in path_filename:
                    filename = unquote(path_filename)

            # Stream the actual content
            async def stream():
                async with client.stream("GET", url, headers=HEADERS) as response:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        yield chunk

            response_headers = {
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Mirror-Source": url[:100],
                "X-Powered-By": "ENI-Mirror-v2",
                "Cache-Control": "public, max-age=3600"
            }
            
            if content_length:
                response_headers["Content-Length"] = content_length

            return StreamingResponse(
                stream(),
                media_type=content_type,
                headers=response_headers
            )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Source server timeout")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect: {str(e)}")


@app.get("/health")
async def health():
    return {"status": "alive", "server": "cloudflare-edge-mirror"}
