"""Einstellungen aus den D2C_*-Umgebungsvariablen.

Jede Funktion liest die Umgebung bei jedem Aufruf, nie beim Import; so wirkt eine geänderte Variable sofort.
"""

import os
from pathlib import Path

RUNS = "runs"  # Ordner der Laufprotokolle in results/<teil>/
VERSIONS = "versions"  # Ordner früherer Stände in results/<teil>/: versions/<Zeitstempel>/
DEFAULT_TIMEOUT = 60.0  # Sekunden Laufzeit des Aufbau-Workers
DEFAULT_MAX_USD = 3.0  # Kostenobergrenze eines Agentenlaufs in USD
DEFAULT_MAX_MINUTES = 20.0  # Zeitobergrenze eines Agentenlaufs in Minuten
DEFAULT_MAX_RUNS = 2  # gleichzeitige Läufe der Web-Oberfläche
DEFAULT_LOG_LEVEL = "info"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def results_root() -> Path:
    """Der Ergebnisordner (D2C_RESULTS); absolut, weil der Aufbau-Worker in einem anderen Arbeitsverzeichnis läuft."""
    return Path(os.environ.get("D2C_RESULTS", "/app/results")).absolute()


def data_root() -> Path:
    """Der Ordner, aus dem Zeichnungen gelesen werden (D2C_DATA)."""
    return Path(os.environ.get("D2C_DATA", "/data"))


def tables_dir() -> Path:
    """Der Ordner mit den Toleranztabellen (D2C_TABLES)."""
    return Path(os.environ.get("D2C_TABLES", "/tables"))


def worker_timeout() -> float:
    """Zeitlimit des Aufbau-Workers in Sekunden (D2C_TIMEOUT). Ein Wert, der keine Zahl ist, löst ValueError aus."""
    return float(os.environ.get("D2C_TIMEOUT", DEFAULT_TIMEOUT))


def report_full() -> bool:
    """Ob die Werkzeuge den vollen Worker-Bericht liefern (D2C_REPORT=full)."""
    return os.environ.get("D2C_REPORT") == "full"


def log_level() -> str:
    """Der Protokollgrad in Großbuchstaben (D2C_LOG): DEBUG, INFO, WARNING oder ERROR."""
    return os.environ.get("D2C_LOG", DEFAULT_LOG_LEVEL).upper()


def ollama_url() -> str:
    """Die Adresse des lokalen Ollama (D2C_OLLAMA_URL) ohne abschließenden Schrägstrich."""
    return (os.environ.get("D2C_OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")


def mcp_url() -> str:
    """Der MCP-Server des Agents (D2C_MCP_URL): "stdio", eine http(s)-Adresse oder eine Kommandozeile."""
    return os.environ.get("D2C_MCP_URL") or "stdio"


def llm_settings() -> dict[str, str | None]:
    """Die Sprachmodell-Einstellungen aus D2C_LLM_*; ein leerer Wert gilt als nicht gesetzt (None)."""
    names = {
        "model": "D2C_LLM_MODEL",
        "build_model": "D2C_LLM_BUILD_MODEL",
        "base_url": "D2C_LLM_BASE_URL",
        "build_base_url": "D2C_LLM_BUILD_BASE_URL",
        "api_key": "D2C_LLM_API_KEY",
        "context": "D2C_LLM_CONTEXT",
        "thinking": "D2C_LLM_THINKING",
    }
    return {key: os.environ.get(name) or None for key, name in names.items()}


def _limit(value: float | None, var: str, default: float) -> float | None:
    """Ein Limit aus dem Parameter, sonst aus der Umgebungsvariablen, sonst der Standard; None (unbegrenzt) bei 0."""
    raw = os.environ.get(var, "").strip()
    try:
        value = value if value is not None else float(raw) if raw else default
    except ValueError:
        raise ValueError(f"{var} must be a number, not {raw!r}") from None
    if not value >= 0:  # auch NaN
        raise ValueError(f"{var} must not be negative")
    return value or None


def max_usd(value: float | None = None) -> float | None:
    """Die Kostenobergrenze eines Agentenlaufs in USD: `value`, sonst D2C_MAX_USD, sonst 3; 0 hebt sie auf (None)."""
    return _limit(value, "D2C_MAX_USD", DEFAULT_MAX_USD)


def max_minutes(value: float | None = None) -> float | None:
    """Die Zeitobergrenze eines Agentenlaufs in Minuten: `value`, sonst D2C_MAX_MINUTES, sonst 20; 0 hebt sie auf (None)."""
    return _limit(value, "D2C_MAX_MINUTES", DEFAULT_MAX_MINUTES)


LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


def allowed_hosts() -> list[str]:
    """Hosts, die die Web-App in Anfragen akzeptiert (Schutz vor DNS-Rebinding): Loopback plus D2C_ALLOWED_HOSTS (kommagetrennt)."""
    extra = [h.strip().lower() for h in os.environ.get("D2C_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return [*LOCAL_HOSTS, *extra]


def max_runs() -> int:
    """Die Zahl gleichzeitiger Läufe der Web-Oberfläche (D2C_MAX_RUNS), mindestens 1."""
    return max(1, int(os.environ.get("D2C_MAX_RUNS") or DEFAULT_MAX_RUNS))
