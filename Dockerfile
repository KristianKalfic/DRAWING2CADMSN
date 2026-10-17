FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Dependencies first, against an empty package: a code change then reuses this layer.
COPY pyproject.toml /app/
RUN mkdir -p /app/d2c && touch /app/d2c/__init__.py && pip install --no-cache-dir -e "/app[dev]"
COPY . /app
CMD ["python", "-m", "d2c.server"]
