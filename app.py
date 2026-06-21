import os
import re
import sys
import time
import uuid
import glob
import json
import shutil
import hashlib
import zipfile
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, render_template


def _tool(name):
    """Resolve a CLI tool, preferring the venv this app runs in (gallery-dl is
    venv-only) and falling back to whatever is on PATH. Keeps the app working
    whether launched via lumen.sh (venv activated) or `python app.py` directly."""
    venv_bin = os.path.join(os.path.dirname(sys.executable), name)
    return venv_bin if os.path.exists(venv_bin) else name

app = Flask(__name__)
BASE_DIR = os.path.dirname(__file__)
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
INFO_CACHE_DIR = os.path.join(DOWNLOAD_DIR, ".info")
COOKIES_FILE = os.path.join(BASE_DIR, ".cookies", "cookies.txt")
LUMEN_DIR = os.path.join(BASE_DIR, ".lumen")
HISTORY_FILE = os.path.join(LUMEN_DIR, "history.json")
COLLECTIONS_FILE = os.path.join(LUMEN_DIR, "collections.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(INFO_CACHE_DIR, exist_ok=True)
os.makedirs(LUMEN_DIR, exist_ok=True)

jobs = {}
_store_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Persistence: completed downloads (history) and user collections survive
# restarts in small JSON files under .lumen/.
# ---------------------------------------------------------------------------
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _domain(url):
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return ""


def _load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _history_all():
    return _load(HISTORY_FILE, [])


def _history_add(entry):
    with _store_lock:
        items = _load(HISTORY_FILE, [])
        items.insert(0, entry)
        _save(HISTORY_FILE, items[:1000])


def _record_history(job_id, type_, fmt):
    """Append a completed download to history (best-effort — never break a job)."""
    try:
        job = jobs.get(job_id, {})
        path = job.get("file")
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        _history_add({
            "id": job_id,
            "title": job.get("title") or job.get("filename") or "Untitled",
            "url": job.get("url", ""),
            "source": _domain(job.get("url", "")),
            "type": type_,
            "format": fmt,
            "resolution": job.get("resolution", ""),
            "thumbnail": job.get("thumbnail", ""),
            "size": size,
            "filename": job.get("filename", ""),
            "date": _now_iso(),
            "collection": None,
            "tags": [],
        })
    except Exception:
        pass

# YouTube (and others) now require solving JS signature / "n" challenges to avoid
# throttling and missing formats. yt-dlp does this via the EJS solver, which must be
# explicitly enabled and needs a JS runtime (deno) on PATH. Without this, downloads
# are throttled to a crawl and high-quality formats silently disappear.
YTDLP_BASE = [_tool("yt-dlp"), "--no-playlist", "--remote-components", "ejs:github"]

# Download speed flags:
#  - m3u8:ffmpeg  -> pull HLS streams (X/Twitter, TikTok, Insta) in a single ffmpeg
#    connection instead of fetching fragments one-by-one, which stalls badly. This
#    alone took a sample X clip from ~78s to ~4s of actual transfer.
#  - -N 8         -> download DASH/fragmented formats (YouTube) 8 fragments in parallel.
#  - socket/retries -> don't let one stalled fragment hang the whole job.
SPEED_FLAGS = [
    "--downloader", "m3u8:ffmpeg",
    "-N", "8",
    "--socket-timeout", "20",
    "--retries", "5",
    "--fragment-retries", "10",
]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".gifv"}
SUB_EXTS = {".vtt", ".srt", ".ass", ".ssa"}


# ---------------------------------------------------------------------------
# Free auth: cookies. No paid API anywhere — to reach private/login-gated or
# rate-limited content we reuse the user's own browser session, for free.
#   1. A cookies.txt file dropped at .cookies/cookies.txt  (export from a browser
#      extension), OR
#   2. LUMEN_COOKIES_BROWSER=chrome|safari|firefox|edge|brave  -> read cookies
#      straight from the installed browser (yt-dlp & gallery-dl both support this).
# Both tools accept the same flags, so one helper covers both.
# ---------------------------------------------------------------------------
def cookie_flags():
    if os.path.exists(COOKIES_FILE):
        return ["--cookies", COOKIES_FILE]
    browser = os.environ.get("LUMEN_COOKIES_BROWSER", "").strip()
    if browser:
        return ["--cookies-from-browser", browser]
    return []


def _info_cache_path(url):
    return os.path.join(INFO_CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".json")


def _safe_name(text, limit=40):
    cleaned = "".join(c for c in (text or "") if c not in r'\/:*?"<>|').strip()
    return cleaned[:limit].strip()


def _friendly_login_hint(stderr):
    low = (stderr or "").lower()
    needs_auth = any(s in low for s in [
        "login required", "log in", "private", "not available", "rate-limit",
        "rate limit", "429", "authentication", "sign in", "requested content is not",
    ])
    if needs_auth and not cookie_flags():
        return ("This post may be private or rate-limited. Add a free login: set "
                "LUMEN_COOKIES_BROWSER=chrome (or safari/firefox) before launching, "
                "or drop a cookies.txt at .cookies/cookies.txt")
    return None


_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d)?)%")


def _stream_run(cmd, job, timeout=300):
    """Run a command, parse download percentages live into job['progress'], and
    enforce a timeout with a watchdog. Returns (returncode, tail_output)."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    tail = deque(maxlen=25)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            m = _PCT_RE.search(line)
            if m:
                try:
                    job["progress"] = min(99.0, float(m.group(1)))
                except ValueError:
                    pass
    finally:
        timer.cancel()
        proc.wait()
    return proc.returncode, "\n".join(tail)


# ===========================================================================
# Single-file video / audio download (fast path).
# ===========================================================================
def run_download(job_id, url, format_choice, format_id):
    job = jobs[job_id]
    job["progress"] = 0
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = YTDLP_BASE + SPEED_FLAGS + cookie_flags() + ["--newline", "-o", out_template]

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
    elif format_id:
        cmd += ["-f", f"{format_id}+bestaudio/best", "--merge-output-format", "mp4"]
    else:
        cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]

    # Reuse the metadata already extracted by /api/info so slow extractors (X, Instagram,
    # TikTok — guest tokens, GraphQL, manifests) aren't re-run here. Saves ~20s on X.
    # The signed media URLs are seconds old, so they're still valid. If anything goes
    # wrong with the cached info, we fall back to a normal extraction below.
    info_path = _info_cache_path(url)
    used_info_json = os.path.exists(info_path)
    attempt = cmd + (["--load-info-json", info_path, url] if used_info_json else [url])

    try:
        rc, out = _stream_run(attempt, job)
        # Cached info can have stale/expired URLs — retry once with a fresh extraction.
        if rc != 0 and used_info_json:
            job["progress"] = 0
            rc, out = _stream_run(cmd + [url], job)
        if rc != 0:
            job["status"] = "error"
            job["error"] = _friendly_login_hint(out) or (out.strip().split("\n")[-1] if out else "Download failed")
            return

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            return

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
        else:
            target = [f for f in files if f.endswith(".mp4")]
        chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        ext = os.path.splitext(chosen)[1]
        safe_title = _safe_name(job.get("title", ""), 40)
        job["file"] = chosen
        job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(chosen)
        job["progress"] = 100
        job["status"] = "done"
        _record_history(job_id, "audio" if format_choice == "audio" else "video",
                        "MP3" if format_choice == "audio" else "MP4")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


# ===========================================================================
# "Everything" bundle — grabs ALL media (photos + videos) from a post or
# carousel across platforms, plus thumbnail / subtitles / metadata, and zips it.
#   - gallery-dl  : best for images, photo carousels, Pinterest, mixed posts.
#   - yt-dlp      : best for video platforms (YouTube, Loom, Vimeo) + subs/meta.
# We run both (best-effort), dedupe by content hash, then build a tidy ZIP.
# ===========================================================================
def _collect_metadata(workdir, url, title):
    """Scan any .json sidecars (gallery-dl per-file + yt-dlp .info.json) and pull
    the most useful, platform-agnostic fields into a single readable file."""
    caption = author = date = found_title = ""
    for jf in glob.glob(os.path.join(workdir, "*.json")):
        try:
            with open(jf) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(d, list):
            d = d[0] if d and isinstance(d[0], dict) else {}
        caption = caption or d.get("content") or d.get("description") or ""
        found_title = found_title or d.get("title") or d.get("fulltitle") or ""
        a = d.get("author") or d.get("uploader") or d.get("user")
        if isinstance(a, dict):
            a = a.get("nick") or a.get("name") or a.get("username") or a.get("full_name") or ""
        author = author or (a or "")
        date = date or str(d.get("date") or d.get("upload_date") or d.get("timestamp") or "")
    lines = [
        f"Title:   {title or found_title}",
        f"Author:  {author}",
        f"Date:    {date}",
        f"Source:  {url}",
        "",
        "Caption:",
        caption or "(none)",
    ]
    with open(os.path.join(workdir, "metadata.txt"), "w") as fh:
        fh.write("\n".join(lines))


def run_bundle(job_id, url, opts):
    """opts: dict with booleans media/thumbnail/metadata/subtitles."""
    job = jobs[job_id]
    job["progress"] = 5
    workdir = os.path.join(DOWNLOAD_DIR, f"{job_id}_work")
    if os.path.exists(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    want_subs = opts.get("subtitles", True)
    want_thumb = opts.get("thumbnail", True)
    want_meta = opts.get("metadata", True)
    errors = []

    try:
        # --- 1. gallery-dl: every image + video in the post / carousel ---------
        gdl = [_tool("gallery-dl")] + cookie_flags() + ["-D", workdir]
        if want_meta:
            gdl += ["--write-metadata"]
        gdl += [url]
        try:
            r = subprocess.run(gdl, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                errors.append(("gallery-dl", r.stderr))
        except subprocess.TimeoutExpired:
            errors.append(("gallery-dl", "timed out"))
        job["progress"] = 45

        # Did gallery-dl already pull the media? If so, yt-dlp must NOT re-download the
        # video (the two tools encode differently, so the bytes differ and content-hash
        # dedup can't catch it — you'd get two near-identical copies). When media already
        # exists, yt-dlp runs with --skip-download to add ONLY thumbnail/subs/metadata.
        # When gallery-dl got nothing (YouTube, Loom, Vimeo...), yt-dlp downloads it.
        gdl_got_media = any(
            os.path.splitext(p)[1].lower() in (IMAGE_EXTS | VIDEO_EXTS)
            for p in glob.glob(os.path.join(workdir, "*"))
        )

        # --- 2. yt-dlp: video platforms + subtitles + thumbnail + metadata -----
        # Separate output templates keep thumbnails/subs identifiable vs. media.
        ydl = YTDLP_BASE + SPEED_FLAGS + cookie_flags() + [
            "-o", os.path.join(workdir, "v-%(id)s.%(ext)s"),
            "-o", "thumbnail:" + os.path.join(workdir, "thumb-%(id)s.%(ext)s"),
            "-o", "subtitle:" + os.path.join(workdir, "sub-%(id)s.%(ext)s"),
            "-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4",
        ]
        if gdl_got_media:
            ydl += ["--skip-download"]
        if want_thumb:
            ydl += ["--write-thumbnail"]
        if want_subs:
            ydl += ["--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en"]
        if want_meta:
            ydl += ["--write-info-json"]
        ydl += [url]
        try:
            r = subprocess.run(ydl, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                errors.append(("yt-dlp", r.stderr))
        except subprocess.TimeoutExpired:
            errors.append(("yt-dlp", "timed out"))
        job["progress"] = 80

        # --- 3. Build readable metadata, then drop raw json sidecars -----------
        if want_meta:
            _collect_metadata(workdir, url, job.get("title", ""))
        for jf in glob.glob(os.path.join(workdir, "*.json")):
            os.remove(jf)

        # --- 4. Classify, dedupe, and tidy the workdir -------------------------
        thumbs, subs, media = [], [], []
        for path in sorted(glob.glob(os.path.join(workdir, "*"))):
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            if name == "metadata.txt":
                continue
            if name.startswith("thumb-") and ext in IMAGE_EXTS:
                thumbs.append(path)
            elif name.startswith("sub-") or ext in SUB_EXTS:
                subs.append(path)
            elif ext in IMAGE_EXTS or ext in VIDEO_EXTS:
                media.append(path)

        # Dedupe media by content hash (yt-dlp & gallery-dl may grab the same video).
        seen, unique_media = set(), []
        for path in media:
            try:
                with open(path, "rb") as fh:
                    h = hashlib.md5(fh.read()).hexdigest()
            except OSError:
                continue
            if h in seen:
                os.remove(path)
                continue
            seen.add(h)
            unique_media.append(path)

        if not unique_media and not (want_thumb and thumbs):
            job["status"] = "error"
            hint = None
            for _, err in errors:
                hint = _friendly_login_hint(err)
                if hint:
                    break
            job["error"] = hint or "No downloadable media found at that link"
            return

        # --- 5. Rename for a clean ZIP -----------------------------------------
        final = []
        width = max(2, len(str(len(unique_media))))
        for i, path in enumerate(sorted(unique_media), 1):
            ext = os.path.splitext(path)[1].lower()
            dest = os.path.join(workdir, f"{str(i).zfill(width)}{ext}")
            if path != dest:
                os.rename(path, dest)
            final.append(dest)
        if want_thumb and thumbs:
            t_ext = os.path.splitext(thumbs[0])[1].lower()
            t_dest = os.path.join(workdir, f"thumbnail{t_ext}")
            os.rename(thumbs[0], t_dest)
            final.append(t_dest)
            for extra in thumbs[1:]:
                os.remove(extra)
        elif not want_thumb:
            for t in thumbs:
                os.remove(t)
        if want_subs:
            final += [s for s in subs if os.path.exists(s)]
        else:
            for s in subs:
                os.remove(s)
        if want_meta and os.path.exists(os.path.join(workdir, "metadata.txt")):
            final.append(os.path.join(workdir, "metadata.txt"))

        # Classify the bundle: all-images -> "images", otherwise "bundle".
        all_images = all(os.path.splitext(p)[1].lower() in IMAGE_EXTS for p in unique_media)
        kind = "images" if (unique_media and all_images) else "bundle"

        # --- 6. Single media item + nothing else? skip the zip -----------------
        media_only = want_thumb is False and want_subs is False and want_meta is False
        if len(final) == 1 and media_only:
            job["file"] = final[0]
            safe_title = _safe_name(job.get("title", ""), 40)
            ext = os.path.splitext(final[0])[1]
            job["filename"] = f"{safe_title}{ext}" if safe_title else os.path.basename(final[0])
            job["progress"] = 100
            job["status"] = "done"
            _record_history(job_id, kind, ext.lstrip(".").upper() or "FILE")
            return

        # --- 7. Zip everything -------------------------------------------------
        job["progress"] = 90
        zip_path = os.path.join(DOWNLOAD_DIR, f"{job_id}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in final:
                if os.path.exists(path):
                    zf.write(path, os.path.basename(path))

        safe_title = _safe_name(job.get("title", ""), 40) or "lumen"
        job["file"] = zip_path
        job["filename"] = f"{safe_title}.zip"
        job["item_count"] = len(unique_media)
        job["progress"] = 100
        job["status"] = "done"
        _record_history(job_id, kind, "ZIP")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ===========================================================================
# Info — drive the card. yt-dlp first; fall back to gallery-dl for image-only
# posts (Instagram photos, Pinterest) that yt-dlp can't describe.
# ===========================================================================
def _gallerydl_info(url):
    try:
        r = subprocess.run(
            [_tool("gallery-dl")] + cookie_flags() + ["-j", url],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return None, _friendly_login_hint(r.stderr) or r.stderr.strip().split("\n")[-1]
        data = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        return None, "Timed out fetching info"
    except (ValueError, Exception):
        return None, None

    # gallery-dl -j emits [[type, url, metadata], ...]; count media, grab a preview.
    meta, thumb, count = {}, "", 0
    for entry in data if isinstance(data, list) else []:
        if isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[1], str):
            if entry[1].startswith("http"):
                count += 1
                thumb = thumb or entry[1]
            if len(entry) >= 3 and isinstance(entry[2], dict):
                meta = meta or entry[2]
    if count == 0:
        return None, None
    title = meta.get("content") or meta.get("description") or meta.get("title") or ""
    author = meta.get("author")
    if isinstance(author, dict):
        author = author.get("nick") or author.get("name") or author.get("username") or ""
    return {
        "title": (title or "").split("\n")[0][:120],
        "thumbnail": thumb if os.path.splitext(thumb)[1].lower() in IMAGE_EXTS else "",
        "duration": None,
        "uploader": author or "",
        "formats": [],
        "count": count,
        "is_gallery": count > 1,
    }, None


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    cmd = YTDLP_BASE + cookie_flags() + ["-j", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            # yt-dlp can't describe image-only posts — try gallery-dl before failing.
            info, err = _gallerydl_info(url)
            if info:
                return jsonify(info)
            hint = _friendly_login_hint(result.stderr)
            return jsonify({"error": hint or err or result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)

        # Persist the raw extraction so the subsequent download can skip re-extracting
        # (big win for slow extractors like X/Instagram). Best-effort — never block info.
        try:
            with open(_info_cache_path(url), "w") as fh:
                fh.write(result.stdout)
        except OSError:
            pass

        # Carousel / multi-item detection (Instagram carousels arrive as playlists).
        count = info.get("playlist_count") or (len(info["entries"]) if info.get("entries") else 1)

        # Build quality options — keep best format per resolution
        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = [
            {"id": f["format_id"], "label": f"{height}p", "height": height}
            for height, f in best_by_height.items()
        ]
        formats.sort(key=lambda x: x["height"], reverse=True)

        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
            "count": count,
            "is_gallery": bool(count and count > 1),
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {
        "status": "downloading", "url": url, "title": title, "progress": 0,
        "thumbnail": data.get("thumbnail", ""), "resolution": data.get("resolution", ""),
        "format_choice": format_choice,
    }

    if format_choice == "all":
        opts = data.get("bundle") or {
            "media": True, "thumbnail": True, "metadata": True, "subtitles": True,
        }
        target, args = run_bundle, (job_id, url, opts)
    else:
        target, args = run_download, (job_id, url, format_choice, format_id)

    thread = threading.Thread(target=target, args=args)
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
        "item_count": job.get("item_count"),
        "progress": job.get("progress", 0),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if job and job.get("status") == "done":
        return send_file(job["file"], as_attachment=True, download_name=job["filename"])
    # Fallback: the in-memory job is gone (server restarted) but the file remains.
    matches = [m for m in glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
               if not m.endswith((".part", ".ytdl", ".tmp"))]
    if matches:
        name = next((e["filename"] for e in _history_all() if e["id"] == job_id), None)
        return send_file(matches[0], as_attachment=True,
                         download_name=name or os.path.basename(matches[0]))
    return jsonify({"error": "File not ready"}), 404


# ===========================================================================
# Workspace data — history (activity) and collections.
# ===========================================================================
@app.route("/api/history")
def api_history():
    return jsonify({"items": _history_all()})


@app.route("/api/history/<item_id>", methods=["DELETE"])
def api_history_delete(item_id):
    with _store_lock:
        items = _load(HISTORY_FILE, [])
        items = [e for e in items if e["id"] != item_id]
        _save(HISTORY_FILE, items)
    # Best-effort remove the file from disk too.
    for m in glob.glob(os.path.join(DOWNLOAD_DIR, f"{item_id}.*")):
        try:
            os.remove(m)
        except OSError:
            pass
    return jsonify({"ok": True})


@app.route("/api/history/<item_id>/collection", methods=["POST"])
def api_assign_collection(item_id):
    name = (request.json or {}).get("collection")
    with _store_lock:
        items = _load(HISTORY_FILE, [])
        for e in items:
            if e["id"] == item_id:
                e["collection"] = name or None
                break
        _save(HISTORY_FILE, items)
        if name:
            cols = _load(COLLECTIONS_FILE, [])
            if name not in [c["name"] for c in cols]:
                cols.append({"name": name, "created": _now_iso()})
                _save(COLLECTIONS_FILE, cols)
    return jsonify({"ok": True})


@app.route("/api/collections", methods=["GET", "POST"])
def api_collections():
    if request.method == "POST":
        name = (request.json or {}).get("name", "").strip()
        if not name:
            return jsonify({"error": "Name required"}), 400
        with _store_lock:
            cols = _load(COLLECTIONS_FILE, [])
            if name not in [c["name"] for c in cols]:
                cols.append({"name": name, "created": _now_iso()})
                _save(COLLECTIONS_FILE, cols)
        return jsonify({"ok": True})

    cols = _load(COLLECTIONS_FILE, [])
    history = _history_all()
    counts = {}
    for e in history:
        if e.get("collection"):
            counts[e["collection"]] = counts.get(e["collection"], 0) + 1
    out = [{"name": c["name"], "count": counts.get(c["name"], 0), "created": c.get("created")}
           for c in cols]
    return jsonify({"items": out})


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/app")
def dashboard():
    return render_template("app.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port)
