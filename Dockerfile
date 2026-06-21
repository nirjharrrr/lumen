FROM python:3.12-slim

# ffmpeg: HLS/stream muxing & merging. curl/unzip: fetch the deno runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# deno is the JS runtime yt-dlp uses to solve YouTube's signature / "n" challenges.
# Without it, YouTube downloads get throttled and lose high-quality formats.
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hosts (Render/Railway/Fly/Cloud Run) inject $PORT; default to 8899 locally.
EXPOSE 8899
ENV HOST=0.0.0.0
CMD ["python", "app.py"]
