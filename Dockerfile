# Used by the Celery worker/beat services in docker-compose.yml (Phase 6).
# Celery's default worker pool doesn't work on native Windows, so those
# run in Linux containers instead - the same reason Postgres/Redis do.
# The FastAPI app still runs directly on the Windows host during
# development (see README) - this image is not currently used for it.
FROM python:3.12-slim

WORKDIR /app

# See certs/README.md - installs any extra CA certs the build needs to
# trust (harmless no-op if certs/ is empty, e.g. on a machine without a
# TLS-inspecting antivirus). PIP_CERT/SSL_CERT_FILE matter here: pip
# vendors its own certifi bundle by default and ignores the system trust
# store update-ca-certificates updates unless explicitly pointed at it.
COPY certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir -e .

# The browser fallback (app/ingestion/adapters/browser.py) renders
# JS-only career pages in headless Chromium. The browser and its system
# libraries are installed here, as root, into a path the non-root user
# below can read (the default would be root's home). Playwright's
# downloader is Node, which trusts only its own CA bundle unless told
# about the extra certs above - same reason PIP_CERT is set.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
RUN playwright install --with-deps chromium

# Nothing here needs root at runtime (Celery warns loudly if it gets it).
# The model cache dir is created *as this user* so a fresh named volume
# mounted there inherits writable ownership - Docker copies the mount
# point's ownership into a new volume, and a root-owned one would make
# the first model download fail with EACCES.
RUN useradd --create-home --uid 1000 app

# Chromium keeps its own per-user trust store (NSS), so the extra CA
# certs from certs/ are registered there too, or the browser fallback
# refuses every page on a machine with a TLS-inspecting antivirus.
# A no-op when certs/ is empty.
RUN apt-get update && apt-get install -y --no-install-recommends libnss3-tools \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /home/app/.pki/nssdb \
    && certutil -d sql:/home/app/.pki/nssdb -N --empty-password \
    && for cert in /usr/local/share/ca-certificates/*.crt; do \
         [ -e "$cert" ] || continue; \
         certutil -d sql:/home/app/.pki/nssdb -A -t "C,," -n "$(basename "$cert")" -i "$cert"; \
       done \
    && chown -R app:app /home/app/.pki

USER app
ENV FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed
RUN mkdir -p /home/app/.cache/fastembed

# No CMD: docker-compose.yml's worker/beat services each set their own
# `command` (celery worker vs. celery beat) from this same image.
