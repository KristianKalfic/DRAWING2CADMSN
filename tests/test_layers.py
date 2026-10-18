"""Schichtregeln des Pakets: Wer darf wen importieren, und wo liegt CadQuery. Geprüft wird nur der Quelltext (AST)."""

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "d2c"
CORE = frozenset({"config", "log", "spec", "oppath", "errors"})  # Module im Paketstamm, die zum Kern zählen
CORE_THIRD_PARTY = frozenset({"pydantic"})  # Der Kern braucht außer der Standardbibliothek nur pydantic.

# Einheit -> Einheiten, die sie importieren darf (Kern und die Einheit selbst sind immer erlaubt).
ALLOWED = {
    "drawing": {"drawing"},
    "cad": {"cad"},
    "quality": {"quality"},
    "session": {"session", "cad", "quality"},
    "server": {"server", "drawing", "cad", "quality", "session"},
    "agent": {"agent"},
    "web": {"web", "drawing", "agent"},
    "demo": {"demo", "quality", "session"},
}
# Einziger Ort mit Import im Funktionskörper: die Webansicht lädt den Agenten erst bei Bedarf.
FUNCTION_IMPORTS_ALLOWED = {"web/app.py"}


def sources() -> list[tuple[str, ast.Module]]:
    """Alle Quelldateien unter d2c/ als (Pfad relativ zu d2c/, Syntaxbaum)."""
    files = sorted(PACKAGE.rglob("*.py"))
    return [(path.relative_to(PACKAGE).as_posix(), ast.parse(path.read_text(encoding="utf-8"))) for path in files]


def unit_of(relative: str) -> str:
    """Die Einheit einer Datei: ihr Unterpaket, sonst ihr Modulname."""
    first = relative.split("/")[0]
    return first.removesuffix(".py")


def imported_names(tree: ast.Module) -> list[str]:
    """Voll qualifizierte Modulnamen aller Importe, auch solcher im Funktionskörper."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relativer Import in Zeile {node.lineno}"
            names += [f"{node.module}.{alias.name}" if node.module == "d2c" else node.module for alias in node.names]
    return names


def test_cadquery_only_in_cad():
    offenders = [
        (path, name)
        for path, tree in sources()
        for name in imported_names(tree)
        if name.split(".")[0] in {"cadquery", "OCP"} and unit_of(path) != "cad"
    ]
    assert offenders == []


def violations(files: list[tuple[str, ast.Module]]) -> list[str]:
    found = []
    for path, tree in files:
        unit = unit_of(path)
        allowed = CORE if unit in CORE or path == "__init__.py" else CORE | ALLOWED[unit]
        for name in imported_names(tree):
            parts = name.split(".")
            if parts[0] == "d2c":
                target = parts[1] if len(parts) > 1 else ""
                if target not in allowed:
                    found.append(f"{path} importiert d2c.{target}")
            elif unit in CORE and parts[0] not in sys.stdlib_module_names | CORE_THIRD_PARTY:
                found.append(f"{path} (Kern) importiert {parts[0]}")
    return found


def test_layer_rules():
    assert violations(sources()) == []


@pytest.mark.parametrize(
    "path, code, message",
    [
        ("quality/x.py", "from d2c.cad.ops import build", "quality/x.py importiert d2c.cad"),
        ("spec.py", "from d2c import session", "spec.py importiert d2c.session"),
        ("spec.py", "import numpy", "spec.py (Kern) importiert numpy"),
        ("agent/x.py", "import d2c.server.tools", "agent/x.py importiert d2c.server"),
        ("agent/batch.py", "from d2c.session import Session", "agent/batch.py importiert d2c.session"),
        ("quality/iso_tables.py", "from d2c.cad.ops import build", "quality/iso_tables.py importiert d2c.cad"),
    ],
)
def test_layer_rules_detect_violation(path, code, message):
    assert violations([(path, ast.parse(code))]) == [message]


def test_every_unit_has_a_rule():
    units = {unit_of(path) for path, _ in sources() if path != "__init__.py"}
    assert units <= CORE | ALLOWED.keys()


@pytest.mark.parametrize("path", ["agent/batch.py", "quality/iso_tables.py"])
def test_cadquery_free_modules_exist_and_import_none(path):
    """Der Stapellauf und die Normtabellen laufen ohne CadQuery: Sie gehören zu agent/ und quality/ und importieren es nie."""
    tree = dict(sources())[path]
    assert not {name.split(".")[0] for name in imported_names(tree)} & {"cadquery", "OCP"}


def test_no_function_level_imports():
    found = []
    for path, tree in sources():
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found += [(path, node.lineno) for node in ast.walk(function) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert [(path, line) for path, line in found if path not in FUNCTION_IMPORTS_ALLOWED] == []
