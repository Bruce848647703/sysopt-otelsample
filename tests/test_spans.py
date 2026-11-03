from __future__ import annotations

import pytest

from sysopt_otelsample.spans import Span, SpanError, aggregate_traces, parse_spans


def test_parse_spans_accepts_a_list_or_an_object():
    span = {
        "trace_id": "t1",
        "service": "api",
        "operation": "GET /",
        "duration_ms": 12.5,
        "status": "ok",
    }

    assert parse_spans([span])[0].trace_id == "t1"
    assert parse_spans({"spans": [span]})[0].trace_id == "t1"


def test_parse_spans_rejects_missing_fields():
    with pytest.raises(SpanError, match="trace_id"):
        parse_spans([{"service": "api", "operation": "GET /", "duration_ms": 1}])


def test_parse_spans_rejects_negative_duration():
    with pytest.raises(SpanError, match="negative"):
        parse_spans(
            [{"trace_id": "t", "service": "s", "operation": "o", "duration_ms": -1}]
        )


def test_parse_spans_rejects_empty_input():
    with pytest.raises(SpanError, match="non-empty"):
        parse_spans([])


def test_aggregate_uses_the_longest_span_and_any_error():
    spans = [
        Span("t1", "api", "GET /", 10.0),
        Span("t1", "db", "SELECT", 80.0),
        Span("t1", "api", "GET /", 5.0, status="error"),
        Span("t2", "api", "GET /", 20.0),
    ]

    traces = aggregate_traces(spans)

    assert [t.trace_id for t in traces] == ["t1", "t2"]
    first = traces[0]
    assert first.duration_ms == 80.0
    assert first.service == "db"
    assert first.has_error is True
    assert traces[1].has_error is False


def test_error_statuses_are_case_insensitive():
    span = Span("t1", "api", "GET /", 1.0, status="ERROR")

    assert span.is_error is True