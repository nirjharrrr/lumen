#!/bin/bash
set -e
cd "$(dirname "$0")"

# Check prerequisites
missing=""

if ! command -v python3 &> /dev/null; then
    missing="$missing python3"
fi

if ! command -v yt-dlp &> /dev/null; then
    missing="$missing yt-dlp"
fi

if ! command -v ffmpeg &> /dev/null; then
    missing="$missing ffmpeg"
fi

# A JS runtime is required for yt-dlp to solve YouTube's signature/n challenges.
# Without it, downloads get throttled and high-quality formats go missing.
if ! command -v deno &> /dev/null && ! command -v node &> /dev/null; then
    missing="$missing deno"
fi

if [ -n "$missing" ]; then
    echo "Missing required tools:$missing"
    echo ""
    if command -v brew &> /dev/null; then
        echo "Install with:  brew install$missing"
    elif command -v apt &> /dev/null; then
        echo "Install with:  sudo apt install$missing"
    else
        echo "Please install:$missing"
    fi
    exit 1
fi

# Set up venv and install Python deps
if [ ! -d "venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -q flask yt-dlp gallery-dl
else
    source venv/bin/activate
fi

# gallery-dl powers image / carousel / static-content downloads. Install on demand
# for venvs created before it was added as a dependency.
if ! python3 -c "import gallery_dl" 2>/dev/null; then
    echo "Installing gallery-dl (image & carousel support)..."
    pip install -q gallery-dl
fi

# Keep yt-dlp & gallery-dl current — sites (esp. YouTube/Instagram) change their
# challenge/signature/extraction schemes often, and stale tools get throttled or
# break outright. Run in the background so startup isn't blocked, and never let it
# fail the launch (offline use).
echo "Checking for downloader updates in the background..."
( pip install -q --upgrade yt-dlp gallery-dl > /dev/null 2>&1 || true ) &

PORT="${PORT:-8899}"
export PORT

echo ""
echo "  Lumen is running at http://localhost:$PORT"
echo ""
python3 app.py
