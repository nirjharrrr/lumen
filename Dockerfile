FROM python:3.12-slim

# ffmpeg: HLS/stream muxing & merging. curl/unzip: fetch the deno runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# deno is the JS runtime yt-dlp uses to solve YouTube's signature / "n" challenges.
# Install to /usr/local so it's on PATH for the non-root runtime user below.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# Hugging Face Spaces run the container as UID 1000 — create that user and make
# the app dir writable (downloads/, the EJS solver cache, etc.). Also fine on Render.
RUN useradd -m -u 1000 user

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/downloads && chown -R user:user /app

USER user
ENV HOME=/home/user \
    PATH=/usr/local/bin:$PATH \
    HOST=0.0.0.0 \
    PORT=8899

# Hosts that inject $PORT (Render/Railway) are honored by app.py; HF uses app_port.
EXPOSE 8899
CMD ["python", "app.py"]
