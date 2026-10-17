#!/usr/bin/env bash
# Startet Drawing2CAD auf diesem Rechner. Die Web-Oberfläche läuft lokal und nutzt die Anmeldung von Claude Code,
# ein Schlüssel oder Token ist nicht nötig. Das CAD-Werkzeug (MCP-Server) läuft je Lauf in einem Docker-Container ohne Netz.
# Voraussetzungen: Docker und uv (https://docs.astral.sh/uv/). Danach http://localhost:8080 öffnen.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p drawings results tables
echo "Baue das Docker-Image für das CAD-Werkzeug ..."
docker build -q -t d2c:latest . >/dev/null
export D2C_DATA="$PWD/drawings" D2C_RESULTS="$PWD/results" D2C_TABLES="$PWD/tables"
D2C_MCP_URL="docker run -i --rm --network none -v $(printf %q "$D2C_DATA"):/data:ro -v $(printf %q "$D2C_TABLES"):/tables:ro"
D2C_MCP_URL+=" -v $(printf %q "$D2C_RESULTS"):/app/results d2c:latest"
export D2C_MCP_URL
echo "Prüfe die Python-Umgebung (beim ersten Mal werden Pakete geladen, das dauert einige Minuten) ..."
uv sync
echo "Drawing2CAD startet auf http://localhost:${PORT:-8080}"
exec uv run --no-sync --python 3.12 python -m d2c.web --host 127.0.0.1 --port "${PORT:-8080}"
