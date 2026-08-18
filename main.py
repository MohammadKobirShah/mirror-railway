from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
import httpx
import re
from urllib.parse import unquote

app = FastAPI(title="★ Pro Mirror Server ★", version="1.0")

@app.get("/mirror")
async def mirror(url: str = Query(..., description="Paste any direct download link here")):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http or https.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
        response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch file from source.")

        # Extract original filename
        filename = "downloaded_file"
        content_disposition = response.headers.get("content-disposition", "")
        
        if "filename=" in content_disposition:
            match = re.findall(r'filename\*=?.*?([^\s;"]+)', content_disposition)
            if match:
                filename = unquote(match[0].strip('"\''))
        else:
            filename = url.split("/")[-1].split("?")[0] or "file"

        async def stream_content():
            async for chunk in response.aiter_bytes(chunk_size=8192):
                yield chunk

        return StreamingResponse(
            stream_content(),
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Mirrored-From": url,
                "X-Powered-By": "ENI Pro Mirror"
            }
        )


@app.get("/")
async def home():
    return {
        "message": "Pro Mirror Server Ready 🔥",
        "usage": "/mirror?url=https://example.com/file.zip"
    }
