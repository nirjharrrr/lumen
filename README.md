# Lumen

A self-hosted media downloader with a clean web UI. Paste a link from YouTube, Instagram, Pinterest, Twitter/X, Loom, Reddit, TikTok, and 1000+ other sites — grab it as **video (MP4)**, **audio (MP3)**, or **everything in the post as a ZIP** (all photos *and* videos in a carousel, plus thumbnail, captions, and subtitles).

![Python](https://img.shields.io/badge/python-3.9+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

### Run your own in one click

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/nirjharrrr/lumen)

One click gives you your own private instance on Render's free tier — your own IP
and quota, no shared limits. **Note:** YouTube and Instagram often block cloud
(datacenter) IPs, so a cloud instance is less reliable for those two than running
Lumen locally on your own machine. For the most reliable experience, run it
locally (see Quick Start) — it works on your home IP.

## Features

- **Three download modes**
  - **MP4** — best-quality video, with a resolution picker
  - **MP3** — audio-only extraction
  - **ALL · ZIP** — *everything* in a post: every photo and video (full Instagram/Reddit-style carousels), plus thumbnail, captions/metadata, and subtitles, bundled into a single ZIP
- **Multi-platform** — video via [yt-dlp](https://github.com/yt-dlp/yt-dlp), images & galleries via [gallery-dl](https://github.com/mikf/gallery-dl); Lumen picks the right engine per site and de-dupes overlap
- **Fast** — HLS pulled in one ffmpeg connection, parallel fragment downloads, and metadata reuse so slow extractors (X, Instagram) aren't run twice
- **Bulk** — paste multiple URLs at once; automatic de-duplication
- **Carousel-aware** — shows an item count and bundles all of it
- **Free private-content auth** — optionally reuse your own browser's login (no paid API) for rate-limited or gated content
- **No build step** — vanilla HTML/CSS/JS frontend, single-file Flask backend

## Requirements

- **Python 3.9+**
- **ffmpeg** — stream merging/muxing
- **deno** (or node) — JS runtime yt-dlp uses to solve YouTube's signature challenges (without it, YouTube throttles and drops high-quality formats)
- Python packages (auto-installed into a venv on first run): `flask`, `yt-dlp`, `gallery-dl`

## Quick Start

```bash
# macOS
brew install python ffmpeg deno

# Debian/Ubuntu
# sudo apt install python3 python3-venv ffmpeg
# curl -fsSL https://deno.land/install.sh | sh

git clone https://github.com/nirjharrrr/lumen.git
cd lumen
./lumen.sh
```

`lumen.sh` sets up a virtualenv, installs the Python dependencies, keeps the downloaders up to date in the background, and starts the server.

Open **http://localhost:8899**.

### Docker

```bash
docker build -t lumen .
docker run -p 8899:8899 lumen
```

The image bundles ffmpeg, deno, and all Python deps.

## Usage

1. Paste one or more URLs into the input box.
2. Choose **MP4**, **MP3**, or **ALL · ZIP**.
3. Click **Fetch** to load info and thumbnails.
4. Pick a resolution (MP4 mode) if offered.
5. Click **Download** — single files download directly; ALL mode produces a ZIP.

## Private / login-gated content (optional, free)

Public content needs no login. For content that's only visible when signed in
(private Instagram accounts, your feed/stories, age-gated videos), Lumen can
reuse a browser where **you are already logged in** — no paid service, no
credentials stored by Lumen. Pick one:

```bash
# Read cookies straight from your installed browser:
LUMEN_COOKIES_BROWSER=chrome ./lumen.sh   # or safari | firefox | edge | brave
```

…or export a `cookies.txt` (via a browser "cookies.txt" extension) to
`.cookies/cookies.txt`. Without either, public posts still work and gated ones
return a clear hint instead of failing silently.

> There is no way to download content that requires a login *without* a login —
> that's enforced by the platforms, not by Lumen.

## Supported Sites

Video from anything [yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md); images & galleries from anything [gallery-dl supports](https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md). Includes YouTube, Instagram, Pinterest, Twitter/X, Reddit, TikTok, Facebook, Vimeo, Twitch, Dailymotion, SoundCloud, Loom, Streamable, Tumblr, Threads, and many more.

## Stack

- **Backend:** Python + Flask (single file)
- **Frontend:** Vanilla HTML/CSS/JS (single file, no build step)
- **Engines:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) · [gallery-dl](https://github.com/mikf/gallery-dl) · [ffmpeg](https://ffmpeg.org/) · [deno](https://deno.land/)

## Deployment

Lumen is a long-running Flask server that shells out to `yt-dlp`, `gallery-dl`,
and `ffmpeg`, and writes files to disk. It needs a **container/VPS host**, not a
static or serverless platform. Use the included `Dockerfile` on Render, Railway,
Fly.io, Google Cloud Run, or any Docker host. (Static/JAMstack hosts like Netlify
or GitHub Pages cannot run it.)

> Note: cloud/datacenter IPs are frequently blocked or CAPTCHA-gated by YouTube
> and Instagram, so a hosted instance can be less reliable than running locally.

## Disclaimer

For personal use. Respect copyright and the terms of service of the platforms you
download from. The authors are not responsible for misuse.

## License

[MIT](LICENSE) — built on the open-source ReClip project.
