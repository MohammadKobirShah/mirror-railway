FROM python:3.12-slim

# Install cloudflared
RUN apt-get update && apt-get install -y curl && \
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb && \
    dpkg -i cloudflared.deb && \
    rm cloudflared.deb && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Start script
RUN echo '#!/bin/bash\n\
uvicorn app.main:app --host 0.0.0.0 --port 8080 &\n\
sleep 2\n\
echo ""\n\
echo "========================================"\n\
echo "⚡ PRO MIRROR SERVER STARTING..."\n\
echo "========================================"\n\
echo ""\n\
cloudflared tunnel --url http://localhost:8080\n\
' > /start.sh && chmod +x /start.sh

EXPOSE 8080

CMD ["/bin/bash", "/start.sh"]
