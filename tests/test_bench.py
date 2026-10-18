"""scripts/bench_models.py: Auswertung der Ereignisse eines Laufs, Tabellenzeile und Aufrufbau gegen synthetische Daten."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import bench_models as bench  # noqa: E402

SCHEMA = "1 validation error for call[add_holes]\nplane\n  Field required [type=missing] (missing)"


def call(name: str = "extrude") -> dict:
    return {"type": "tool_call", "name": name, "args": {}}


def result(name: str, ok: bool, text: str) -> dict:
    return {"type": "tool_result", "name": name, "ok": ok, "text": text}


EVENTS = [
    {"type": "phase", "phase": "read"},
    call("submit_spec"),
    result("submit_spec", True, '{"coerced": ["mark_near: [0,-24,10] → [[0,-24,10]]"], "ok": true}'),
    {"type": "phase", "phase": "build"},
    call("add_holes"),
    result("add_holes", False, SCHEMA),
    call("add_holes"),
    result("add_holes", False, "Input should be a valid number (float_parsing)"),
    call("add_holes"),
    result("add_holes", False, "op 1: Unknown dimension id 'x' in `refs`."),  # kein Schemafehler
    call("add_holes"),
    result("add_holes", True, '{"coerced": ["plane"], "ok": true}'),
    call("export_step"),
    result("export_step", True, '{"ok": true}'),
    {"type": "done", "ok": True, "part": "demo", "turns": 7, "cost_usd": 0.1234, "files": {}},
]


def test_summarize_counts():
    s = bench.summarize(EVENTS)
    assert s == {"ok": True, "turns": 7, "calls": 6, "schema_errors": 2, "coerced": 2, "cost_usd": 0.1234, "reason": None}


def test_summarize_without_done_event_is_a_failure():
    s = bench.summarize(EVENTS[:3])
    assert s["ok"] is False and s["turns"] == 0 and s["calls"] == 1 and "done" in s["reason"]


def test_row_shows_unknown_cost():
    s = bench.summarize([{"type": "done", "ok": True, "turns": 2, "cost_usd": None}]) | {"seconds": 5}
    assert bench.row("m", "p", "full", s).endswith("| n/a |")


def test_summarize_failed_run_keeps_reason():
    s = bench.summarize([call(), {"type": "done", "ok": False, "reason": "max_turns reached", "turns": 80, "cost_usd": 0}])
    assert (s["ok"], s["turns"], s["reason"]) == (False, 80, "max_turns reached")


@pytest.mark.parametrize(
    "text, hit",
    [
        ("Field required [type=missing] (missing)", True),
        ("Input should be a valid list (list_type)", True),
        ("Input should be a valid number (float_parsing)", True),
        ("Extra inputs are not permitted (extra_forbidden)", True),
        ("Input should be 'a' or 'b' (literal_error)", True),
        ("Input should be greater than 0 (greater_than)", True),
        ("Input should be greater than or equal to 0 (greater_than_equal)", True),
        ("Value error, x (value_error)", True),
        ("1 validation error for call[extrude]", True),
        ("op 0 (extrude): BuildError: Profil zu groß", False),
    ],
)
def test_schema_error_regex(text, hit):
    assert bool(bench.SCHEMA_ERROR.search(text)) is hit


def test_row_and_table():
    row = bench.row("qwen3.8:27b-mlx", "demo", "full", bench.summarize(EVENTS) | {"seconds": 612.4})
    assert row == "| qwen3.8:27b-mlx | demo | full | yes | 7 | 6 | 2 | 2 | 612 | $0.1234 |"
    table = bench.table([row])
    assert table.splitlines()[0] == "| Model | Part | Mode | ok | Turns | Calls | Schema errors | Coerced | Seconds | Cost |"
    assert table.splitlines()[1] == "|---|---|---|---|---|---|---|---|---|---|"
    assert table.splitlines()[2] == row


def test_row_failed_run_shows_no_and_free_cost():
    s = bench.summarize([{"type": "done", "ok": False, "reason": "x", "turns": 3, "cost_usd": 0}]) | {"seconds": 5}
    assert bench.row("m", "p", "build", s).startswith("| m | p | build | no | 3 | 0 | 0 | 0 | 5 | $0 |")


def test_is_local():
    assert [bench.is_local(m) for m in ("sonnet", "claude-opus-4", "haiku", "qwen3.8:27b-mlx")] == [False, False, False, True]


def test_context_check():
    ps = {"models": [{"name": "qwen3.8:27b-mlx", "context_length": 32768}]}
    assert "32768" in bench.context_problem(ps, "qwen3.8:27b-mlx", 64000)
    assert bench.context_problem({"models": [{"name": "m", "context_length": 64000}]}, "m", 64000) is None
    assert bench.context_problem({"models": []}, "m", 64000) is not None


def test_agent_command_full_local():
    cmd = bench.agent_command(
        "qwen3.8:27b-mlx",
        "demo",
        "full",
        pdf="demo_drawing.pdf",
        server="docker run x",
        events="e.jsonl",
        ollama_url="http://localhost:11435",
        read_model="sonnet",
        spec=None,
    )
    assert cmd[cmd.index("--model") + 1] == "qwen3.8:27b-mlx"
    assert cmd[cmd.index("--base-url") + 1] == "http://localhost:11435"
    assert "--build-model" not in cmd and "--spec" not in cmd
    assert cmd[cmd.index("--server") + 1] == "docker run x"


def test_agent_command_hybrid_reads_with_sonnet_builds_local():
    cmd = bench.agent_command(
        "qwen3.8:27b-mlx",
        "teil_a",
        "hybrid",
        pdf="teil_a/teil_a_dwg.pdf",
        server="s",
        events="e",
        ollama_url="http://localhost:11435",
        read_model="sonnet",
        spec=None,
    )
    assert cmd[cmd.index("--model") + 1] == "sonnet" and "--base-url" not in cmd
    assert cmd[cmd.index("--build-model") + 1] == "qwen3.8:27b-mlx"
    assert cmd[cmd.index("--build-base-url") + 1] == "http://localhost:11435"


def test_requires_paths(tmp_path, capsys):
    """Ohne --drawings und --results bricht argparse mit Rückkehrcode 2 ab; es gibt keine Standardpfade."""
    with pytest.raises(SystemExit) as exc:
        bench.main(["--models", "sonnet", "--parts", "demo", "--out", str(tmp_path)])
    assert exc.value.code == 2
    assert "--drawings" in capsys.readouterr().err


def test_agent_command_build_with_spec_and_claude_model():
    cmd = bench.agent_command(
        "sonnet", "demo", "build", pdf="p.pdf", server="s", events="e", ollama_url="u", read_model="sonnet", spec="/x/spec.json"
    )
    assert cmd[cmd.index("--spec") + 1] == "/x/spec.json" and "--base-url" not in cmd
