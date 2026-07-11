#!/bin/sh
# Serves site/ over HTTP while rebuilding it on a timer. Live Reddit search runs
# through a headed Chromium (Reddit blocks headless / raw-HTTP clients), so a
# virtual display (Xvfb) is brought up first. All of this runs as the non-root
# container user.
set -e

export DISPLAY="${DISPLAY:-:99}"
DNUM="${DISPLAY#:}"

# Clear state left over from a previous boot/crash so startup is idempotent:
#  - a stale X lock/socket makes Xvfb refuse to start ("already active")
#  - a stale Chromium SingletonLock makes the browser refuse to launch
# This container is the sole user of the profile, so removing them is safe.
echo "[gttp] clearing stale X + Chromium locks"
rm -f "/tmp/.X${DNUM}-lock" "/tmp/.X11-unix/X${DNUM}" 2>/dev/null || true
rm -f /app/.cache/browser-profile/Singleton* 2>/dev/null || true

echo "[gttp] starting Xvfb on $DISPLAY"
Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &

# Wait for the X socket before launching anything that needs the display.
for i in $(seq 1 50); do
    [ -e "/tmp/.X11-unix/X${DNUM}" ] && break
    sleep 0.1
done

rebuild() {
    echo "[gttp] build starting $(date -Iseconds)"
    gttp build || echo "[gttp] build failed, keeping last good site/"
    echo "[gttp] build finished $(date -Iseconds)"
}

mkdir -p site
[ -f site/index.html ] || rebuild
python3 -m http.server "$PORT" --directory site &
SERVER_PID=$!

trap 'kill $SERVER_PID' TERM INT

while true; do
    sleep "$(( POLL_INTERVAL_HOURS * 3600 ))"
    rebuild
done &
LOOP_PID=$!

wait $SERVER_PID $LOOP_PID
