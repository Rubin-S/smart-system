#!/usr/bin/env bash
# Publish a looped MP4 on rtsp://127.0.0.1:8554/cam1 via MediaMTX and ffmpeg.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p media/clips .run bin

if [[ ! -x bin/mediamtx ]]; then
  arch="$(uname -m)"
  case "$arch" in
    x86_64) mtx_arch=amd64 ;;
    aarch64) mtx_arch=arm64 ;;
    *) echo "unsupported arch $arch" >&2; exit 1 ;;
  esac
  tmp="$(mktemp)"
  curl -fsSL -o "$tmp" "https://github.com/bluenviron/mediamtx/releases/download/v1.21.2/mediamtx_v1.21.2_linux_${mtx_arch}.tar.gz"
  tar -xzf "$tmp" -C bin mediamtx
  chmod +x bin/mediamtx
  rm -f "$tmp"
fi

if [[ ! -f media/clips/cam1.mp4 ]]; then
  # This ffmpeg build has libopenh264, not libx264. The publish step still
  # uses ffmpeg -re -stream_loop and copies the H.264 elementary stream.
  ffmpeg -y -f lavfi -i "testsrc=size=640x480:rate=8" -t 4 \
    -pix_fmt yuv420p -c:v libopenh264 -b:v 800k -g 8 \
    media/clips/cam1.mp4
fi

if command -v fuser >/dev/null 2>&1; then
  fuser -k 8554/tcp >/dev/null 2>&1 || true
fi
sleep 0.3

nohup bin/mediamtx mediamtx/mediamtx.yml > .run/mediamtx.log 2>&1 &
echo $! > .run/mediamtx.pid
for _ in $(seq 1 30); do
  if grep -Eq "listener opened|listeners on :8554" .run/mediamtx.log 2>/dev/null; then
    break
  fi
  sleep 0.2
done

nohup ffmpeg -loglevel warning -re -stream_loop -1 -i media/clips/cam1.mp4 \
  -c copy -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cam1 \
  > .run/ffmpeg.log 2>&1 &
echo $! > .run/ffmpeg.pid
echo "RTSP rtsp://127.0.0.1:8554/cam1"
