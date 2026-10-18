import importlib
import shutil

import pytest


def test_imports():
    # Wird über die bearbeitbare Installation aufgelöst, auch wenn das Repository unter /app eingehängt ist.
    for name in ("cadquery", "mcp.server.mcpserver", "d2c"):
        importlib.import_module(name)


def test_poppler_available():
    # Poppler ist nur der optionale Rückfall (pymupdf zeichnet und liest allein): ohne Poppler wird übersprungen, nicht gescheitert.
    if shutil.which("pdftoppm") is None:
        pytest.skip("Poppler is optional and not installed")
    assert shutil.which("pdftotext") is not None


def test_pyproject_metadata():
    import tomllib
    from pathlib import Path

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["description"].isascii()  # englisch, ohne Umlaute
    assert "-p no:cacheprovider" in project["tool"]["pytest"]["ini_options"]["addopts"]  # schreibgeschützter Container
