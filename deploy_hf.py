#!/usr/bin/env python3
"""One-shot deploy of Lumen to a Hugging Face Docker Space.

Usage:
    HF_TOKEN=hf_xxx  python deploy_hf.py          # token from env
    python deploy_hf.py hf_xxx                     # token as arg

Get a token (free, 30s): https://huggingface.co/settings/tokens
  -> "New token" -> type: Write -> copy it.

This creates (or reuses) a Docker Space at <your-username>/lumen and uploads the
app. The Space builds the Dockerfile and goes live at:
    https://<your-username>-lumen.hf.space
"""
import os
import sys
from huggingface_hub import HfApi

# Hugging Face Space config lives in the README front-matter. app_port must match
# the port app.py listens on (8899). This README is uploaded over the repo README.
SPACE_README = """---
title: Lumen
emoji: 🎬
colorFrom: gray
colorTo: red
sdk: docker
app_port: 8899
pinned: false
license: mit
short_description: Multi-platform media downloader — video, audio & full-gallery ZIP
---

# Lumen

Paste a link from YouTube, Instagram, Pinterest, X, Loom, Reddit, TikTok and
1000+ sites — download as **MP4**, **MP3**, or **everything in the post as a ZIP**
(all photos + videos in a carousel, plus thumbnail, captions, and subtitles).

> Heads up: YouTube and Instagram often block cloud/datacenter IPs, so those two
> can be unreliable from this hosted Space. For the most reliable results, run
> Lumen locally — source: https://github.com/nirjharrrr/lumen

Built on the open-source ReClip project · MIT licensed.
"""

IGNORE = [
    "venv/*", "downloads/*", ".git/*", "__pycache__/*", "*.pyc",
    ".cookies/*", ".DS_Store", "assets/*", "deploy_hf.py",
]


def main():
    token = os.environ.get("HF_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not token:
        sys.exit("No token. Set HF_TOKEN=hf_xxx or pass it as an argument.\n"
                 "Get one at https://huggingface.co/settings/tokens (type: Write).")

    api = HfApi(token=token)
    user = api.whoami()["name"]
    repo_id = f"{user}/lumen"
    print(f"Deploying to Space: {repo_id}")

    api.create_repo(repo_id, repo_type="space", space_sdk="docker",
                    exist_ok=True, token=token)

    here = os.path.dirname(os.path.abspath(__file__))
    api.upload_folder(
        folder_path=here,
        repo_id=repo_id,
        repo_type="space",
        ignore_patterns=IGNORE,
        commit_message="Deploy Lumen",
    )
    # Overwrite README.md with the HF-config version (front-matter drives the Space).
    api.upload_file(
        path_or_fileobj=SPACE_README.encode(),
        path_in_repo="README.md",
        repo_id=repo_id,
        repo_type="space",
        commit_message="Add Space config",
    )

    handle = f"{user}-lumen".replace("_", "-").lower()
    print("\n✅ Deployed. The Space is now building (~3-5 min).")
    print(f"   Dashboard: https://huggingface.co/spaces/{repo_id}")
    print(f"   Live URL:  https://{handle}.hf.space")


if __name__ == "__main__":
    main()
