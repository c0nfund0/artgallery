# syntax=docker/dockerfile:1
# Base image tag is tracked by Dependabot; the image is also rebuilt weekly
# (see .github/workflows/release.yml) so OS security patches land automatically.
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# Apply any Debian security updates published since the base image was built.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# pip is only needed at build time; removing it drops its vendored libraries
# (and their CVEs) from the runtime image.
RUN pip install -r requirements.txt \
 && python -m pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3*/ensurepip

COPY app ./app
COPY wsgi.py gunicorn.conf.py ./

RUN useradd --system --uid 10001 --home /app gallery \
 && mkdir -p /data && chown gallery:gallery /data
USER gallery
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; port=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=4).status == 200 else 1)"

# Some deploy hosts assign a port at runtime via $PORT rather than the fixed
# 8000 this image otherwise defaults to (see BIND in gunicorn.conf.py) —
# gunicorn's CLI --bind takes precedence over the config file, so this
# overrides it without touching gunicorn.conf.py's own default.
CMD ["sh", "-c", "exec gunicorn -c gunicorn.conf.py --bind 0.0.0.0:${PORT:-8000} wsgi:app"]
