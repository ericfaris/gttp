FROM python:3.12-slim

WORKDIR /app

# Chromium is driven headed under a virtual display (Reddit blocks headless /
# raw-HTTP clients), so install Xvfb. Browsers live at a shared, world-readable
# path so the container can run as a non-root user (uid 1000) and still launch
# Chromium.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/* \
    # Xvfb writes its socket here; make it world-writable for the non-root user.
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Install Chromium + its OS libraries (runs as root at build), then make the
# browser tree readable/executable for the uid-1000 runtime user.
RUN playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY fixtures/ ./fixtures/
COPY books.yaml ./books.yaml
COPY covers/ ./covers/
COPY static/ ./static/
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

ENV PORT=8100 \
    POLL_INTERVAL_HOURS=168 \
    DISPLAY=:99 \
    HOME=/tmp

EXPOSE 8100

ENTRYPOINT ["./entrypoint.sh"]
