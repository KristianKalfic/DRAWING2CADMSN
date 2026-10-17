"""Runs the build worker in an isolated process and checks scripts before they run.

The real boundary is the container (no network) and the worker process: wall-clock timeout, resource
limits and a scratch directory. The script check only gives early, readable errors.
"""
import ast
import functools
import json
import math
import os
import resource
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT = 60.0  # seconds of wall clock, D2C_TIMEOUT overrides it
MEMORY_LIMIT = 4 * 2**30  # bytes of address space
FILE_LIMIT = 200 * 2**20  # bytes of any file the worker writes
STDERR_FILE = "stderr.txt"  # in the scratch directory; the child cannot write more than FILE_LIMIT to it
STDERR_TAIL = 500  # bytes of the worker's stderr kept for a crash message
# Names that open files, run code or reach into objects. Names and attributes starting with "_" are rejected too.
FORBIDDEN_NAMES = frozenset(
    "open exec eval compile __import__ globals locals vars getattr setattr delattr input breakpoint".split())


def error_report(error: str) -> dict:
    """The worker report of a run that made no model: `error` is set, everything else is empty."""
    return {"ok": False, "error": error, "solid": None, "dims": [], "build_notes": [], "roundtrip": None}


def _lookups(node: ast.AST) -> list[str]:
    """The attribute names and variable names this node looks up."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.MatchClass):  # `case Point(__class__=c)` reads an attribute too
        return node.kwd_attrs
    return []


def check_script(code: str) -> None:
    """Raise ValueError if a script imports, uses a forbidden name or reaches for a private attribute."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # nothing can run; the worker reports the syntax error together with its op index
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("imports are not allowed in scripts (cq and math are already available)")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ValueError(f"{node.id!r} is not allowed in scripts")
        for name in _lookups(node):
            if name.startswith("_"):
                raise ValueError(f"names and attributes starting with an underscore are not allowed: {name!r}")


def _limits(cpu_seconds: int) -> None:
    """Runs in the child before it executes: caps address space, file size and CPU time."""
    for kind, limit in ((resource.RLIMIT_AS, MEMORY_LIMIT), (resource.RLIMIT_FSIZE, FILE_LIMIT),
                        (resource.RLIMIT_CPU, cpu_seconds)):
        resource.setrlimit(kind, (limit, limit))


def run_worker(payload: dict, timeout: float | None = None) -> dict:
    """Build and check the ops of the payload in a fresh worker process and return its report.

    The payload is {"ops": [...], "expected": {...} | None, "marks": [...], "export_path": str | None}.
    timeout is the wall clock limit in seconds, by default D2C_TIMEOUT (60). The result always has the
    keys ok, error, solid, dims, build_notes and roundtrip; a timeout, a crash or a rejected script
    is a report with `error` set.
    """
    limit = timeout if timeout is not None else float(os.environ.get("D2C_TIMEOUT", DEFAULT_TIMEOUT))
    for index, op in enumerate(payload["ops"]):
        code = op.get("code")
        if op.get("op") == "script" and isinstance(code, str):  # other types: the worker's validation reports them
            try:
                check_script(code)
            except ValueError as e:
                return error_report(f"ValueError: op {index} (script): {e}")
    # CPU time is only the fallback limit: import cadquery alone takes about 2 s of CPU in 1.2 s of wall clock
    cpu_seconds = 4 * math.ceil(limit) + 10
    with tempfile.TemporaryDirectory() as scratch, open(Path(scratch) / STDERR_FILE, "w+b") as stderr:
        # own process group, so a timeout kills whatever the worker started; stderr in a file, not in our memory
        worker = subprocess.Popen(
            [sys.executable, "-I", "-m", "d2c.worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            preexec_fn=functools.partial(_limits, cpu_seconds), env={"PATH": os.environ["PATH"]}, cwd=scratch,
            start_new_session=True)
        try:
            stdout, _ = worker.communicate(json.dumps(payload).encode(), timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(worker.pid, signal.SIGKILL)
            worker.communicate()
            return error_report(f"timeout after {limit:g}s")
        stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - STDERR_TAIL))
        crash = stderr.read().decode(errors="replace")
    try:
        return json.loads(stdout)  # a finished report counts even if the process died at exit
    except ValueError:
        pass
    if worker.returncode in (-signal.SIGKILL, -signal.SIGXCPU):
        return error_report(f"timeout after {limit:g}s (cpu limit)")
    return error_report("worker crashed: " + crash)
