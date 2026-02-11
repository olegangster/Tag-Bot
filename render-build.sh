#!/usr/bin/env bash
set -e

FFDIR=/opt/render/project/.render/ffmpeg
mkdir -p "$FFDIR"

if [ ! -f "$FFDIR/ffmpeg" ]; then
  echo "Downloading static ffmpeg..."
  cd /opt/render/project/.render
  curl -L -o ffmpeg.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
  tar -xf ffmpeg.tar.xz
  rm ffmpeg.tar.xz
  FOLDER=$(find . -maxdepth 1 -type d -name "ffmpeg-*-amd64-static" | head -n 1)
  mv "$FOLDER/ffmpeg" "$FFDIR/ffmpeg"
  mv "$FOLDER/ffprobe" "$FFDIR/ffprobe"
  rm -rf "$FOLDER"
  chmod +x "$FFDIR/ffmpeg" "$FFDIR/ffprobe"
else
  echo "Using cached ffmpeg..."
fi

pip install -r requirements.txt
