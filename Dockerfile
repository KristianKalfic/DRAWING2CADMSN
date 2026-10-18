# python:3.12-slim (multi-arch index, linux/amd64 and linux/arm64), pinned by digest
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Dependencies first from the lock file (exported from uv.lock), so source changes reuse this layer.
COPY requirements.lock /app/
ENV PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir -r requirements.lock
COPY . /app
RUN pip install --no-cache-dir --no-deps -e .
# Do not run as root: a script that breaks out must not be able to write outside /app/results and /tmp.
# The image is meant for `--read-only --tmpfs /tmp`; HOME is in /tmp, bytecode is not written.
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/results && chown app:app /app/results
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER app
CMD ["python", "-m", "d2c.server"]
