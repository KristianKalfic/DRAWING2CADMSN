"""Einstellungen aus den D2C_*-Umgebungsvariablen: Standardwerte, Grenzfälle und Fehler; gelesen wird bei jedem Aufruf."""

from pathlib import Path

import pytest

from d2c import config

VARIABLES = (
    "D2C_RESULTS",
    "D2C_DATA",
    "D2C_TABLES",
    "D2C_TIMEOUT",
    "D2C_REPORT",
    "D2C_LOG",
    "D2C_OLLAMA_URL",
    "D2C_MCP_URL",
    "D2C_LLM_MODEL",
    "D2C_LLM_BUILD_MODEL",
    "D2C_LLM_BASE_URL",
    "D2C_LLM_BUILD_BASE_URL",
    "D2C_LLM_API_KEY",
    "D2C_LLM_CONTEXT",
    "D2C_LLM_THINKING",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Keine D2C_*-Variable gesetzt (conftest setzt sonst drei davon)."""
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_results_root_is_absolute(monkeypatch):
    monkeypatch.setenv("D2C_RESULTS", "out")
    root = config.results_root()
    assert root.is_absolute() and root == Path.cwd() / "out"


def test_values_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("D2C_DATA", "/zeichnungen")
    monkeypatch.setenv("D2C_TABLES", "/tabellen")
    monkeypatch.setenv("D2C_TIMEOUT", "2.5")
    monkeypatch.setenv("D2C_REPORT", "full")
    monkeypatch.setenv("D2C_LOG", "debug")
    assert (config.data_root(), config.tables_dir(), config.worker_timeout()) == (Path("/zeichnungen"), Path("/tabellen"), 2.5)
    assert config.report_full() is True and config.log_level() == "DEBUG"


def test_defaults_without_variables(clean_env):
    assert config.results_root() == Path("/app/results")
    assert (config.data_root(), config.tables_dir()) == (Path("/data"), Path("/tables"))
    assert config.worker_timeout() == 60.0
    assert config.report_full() is False and config.log_level() == "INFO"
    assert (config.ollama_url(), config.mcp_url()) == ("http://localhost:11434", "stdio")
    assert set(config.llm_settings().values()) == {None}


def test_report_full_only_for_full(monkeypatch):
    monkeypatch.setenv("D2C_REPORT", "kurz")
    assert config.report_full() is False


def test_ollama_url_strips_slash_and_ignores_empty(monkeypatch):
    monkeypatch.setenv("D2C_OLLAMA_URL", "http://ollama:11434/")
    assert config.ollama_url() == "http://ollama:11434"
    monkeypatch.setenv("D2C_OLLAMA_URL", "")
    assert config.ollama_url() == "http://localhost:11434"


def test_empty_mcp_url_means_stdio(monkeypatch):
    monkeypatch.setenv("D2C_MCP_URL", "")
    assert config.mcp_url() == "stdio"
    monkeypatch.setenv("D2C_MCP_URL", "http://mcp:8765/mcp")
    assert config.mcp_url() == "http://mcp:8765/mcp"


def test_llm_settings_treat_empty_as_unset(clean_env, monkeypatch):
    monkeypatch.setenv("D2C_LLM_MODEL", "m")
    monkeypatch.setenv("D2C_LLM_API_KEY", "")
    monkeypatch.setenv("D2C_LLM_CONTEXT", "32000")
    settings = config.llm_settings()
    assert (settings["model"], settings["api_key"], settings["context"]) == ("m", None, "32000")


def test_invalid_timeout_raises_value_error(monkeypatch):
    monkeypatch.setenv("D2C_TIMEOUT", "abc")
    with pytest.raises(ValueError, match="could not convert string to float: 'abc'"):
        config.worker_timeout()


def test_changes_after_import_take_effect(monkeypatch):
    monkeypatch.setenv("D2C_TIMEOUT", "1")
    assert config.worker_timeout() == 1.0
    monkeypatch.setenv("D2C_TIMEOUT", "7")
    assert config.worker_timeout() == 7.0
