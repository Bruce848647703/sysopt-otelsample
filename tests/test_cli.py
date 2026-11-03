from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from sysopt_otelsample.cli import main


def span(trace_id, service, operation, duration_ms, status="ok"):
    return {
        "trace_id": trace_id,
        "service": service,
        "operation": operation,
        "duration_ms": duration_ms,
        "status": status,
    }


SPANS = {
    "spans": [
        span("e1", "api", "GET /pay", 12, "error"),
        span("s1", "api", "GET /pay", 900),
        span("n1", "api", "GET /pay", 20),
        span("n2", "api", "GET /pay", 25),
        span("n3", "db", "SELECT", 8),
        span("n4", "db", "SELECT", 9),
    ]
}


def write(tmp_path: Path, payload) -> str:
    path = tmp_path / "spans.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def args(tmp_path: Path, budget: str, *extra: str) -> list[str]:
    return [
        write(tmp_path, SPANS),
        "--budget",
        budget,
        "--latency-threshold-ms",
        "500",
        *extra,
    ]


def test_text_output(tmp_path, capsys):
    code = main(args(tmp_path, "3"))

    output = capsys.readouterr().out
    assert code == 0
    assert "traces:" in output
    assert "errors:   1/1" in output
    assert "slow:     1/1" in output


def test_json_output(tmp_path, capsys):
    code = main(args(tmp_path, "3", "--format", "json"))

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["budget"] == 3
    assert payload["retained"] <= 3
    assert payload["tiers"]["errors"]["retained"] == 1
    assert payload["tiers"]["slow"]["retained"] == 1


def test_yaml_output(tmp_path, capsys):
    code = main(args(tmp_path, "2", "--format", "yaml"))

    output = capsys.readouterr().out
    assert code == 0
    assert "tail_sampling:" in output
    assert "name: errors" in output


def test_stdin_is_supported(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(SPANS)))

    code = main(["-", "--budget", "2", "--latency-threshold-ms", "500"])

    assert code == 0
    assert "retained:" in capsys.readouterr().out


def test_empty_input_returns_two(tmp_path, capsys):
    code = main([write(tmp_path, {"spans": []}), "--budget", "2"])

    assert code == 2
    assert "non-empty" in capsys.readouterr().err


def test_invalid_budget_returns_two(tmp_path, capsys):
    code = main([write(tmp_path, SPANS), "--budget", "0"])

    assert code == 2
    assert "budget" in capsys.readouterr().err


def test_invalid_json_exits(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(SystemExit, match="invalid JSON"):
        main([str(path), "--budget", "1"])