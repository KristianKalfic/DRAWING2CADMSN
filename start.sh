#!/usr/bin/env bash
# Starts Drawing2CAD on this machine. The web interface runs locally and uses the Claude Code login,
# no key or token is needed. The CAD tool (MCP server) runs per run in a Docker container without network.
# Requirements: Docker and uv (https://docs.astral.sh/uv/). Then open http://localhost:8080.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p drawings results tables
echo "Building the Docker image for the CAD tool ..."
docker build -q -t d2c:latest . >/dev/null
export D2C_DATA="$PWD/drawings" D2C_RESULTS="$PWD/results" D2C_TABLES="$PWD/tables"
D2C_MCP_URL="docker run -i --rm --network none --read-only --tmpfs /tmp --user $(id -u):$(id -g) -v $(printf %q "$D2C_DATA"):/data:ro -v $(printf %q "$D2C_TABLES"):/tables:ro"
D2C_MCP_URL+=" -v $(printf %q "$D2C_RESULTS"):/app/results d2c:latest"
export D2C_MCP_URL
echo "Checking the Python environment (packages are downloaded on the first run, this takes a few minutes) ..."
uv sync
# Pass optional settings from .env (language model endpoints, keys) on to the web interface; uv reads the
# file as assignments and does not run its values as shell commands.
env_file=()
[ -f .env ] && env_file=(--env-file .env)
echo "Drawing2CAD starting on http://localhost:${PORT:-8080}"
exec uv run --no-sync --python 3.12 ${env_file[@]+"${env_file[@]}"} python -m d2c.web --host 127.0.0.1 --port "${PORT:-8080}"
