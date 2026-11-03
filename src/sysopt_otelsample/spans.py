"""Span parsing and trace aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ERROR_STATUSES = {"error", "failed", "failure", "status_code_error"}


class SpanError(ValueError):
    """Raised when span input cannot be parsed."""


@dataclass(frozen=True)
class Span:
    trace_id: str
    service: str
    operation: str
    duration_ms: float
    status: str = "ok"
    timestamp: float = 0.0

    @property
    def is_error(self) -> bool:
        return self.status.strip().lower() in ERROR_STATUSES


@dataclass(frozen=True)
class Trace:
    """One trace, reduced to the fields that affect the sampling decision."""

    trace_id: str
    service: str
    operation: str
    duration_ms: float
    has_error: bool

    @property
    def group(self) -> tuple[str, str]:
        return self.service, self.operation


def parse_spans(payload: Any) -> list[Span]:
    if isinstance(payload, dict):
        raw = payload.get("spans")
        if raw is None:
            raise SpanError("input must contain a 'spans' list")
    else:
        raw = payload
    if not isinstance(raw, list) or not raw:
        raise SpanError("input must contain a non-empty list of spans")

    spans: list[Span] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SpanError(f"span {index} must be an object")
        trace_id = item.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            raise SpanError(f"span {index} needs a non-empty 'trace_id'")
        service = item.get("service")
        if not isinstance(service, str) or not service:
            raise SpanError(f"span {index} needs a non-empty 'service'")
        operation = item.get("operation")
        if not isinstance(operation, str) or not operation:
            raise SpanError(f"span {index} needs a non-empty 'operation'")
        duration = item.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise SpanError(f"span {index} needs a numeric 'duration_ms'")
        if duration < 0:
            raise SpanError(f"span {index} has a negative duration")
        spans.append(
            Span(
                trace_id=trace_id,
                service=service,
                operation=operation,
                duration_ms=float(duration),
                status=str(item.get("status") or "ok"),
                timestamp=float(item.get("timestamp") or 0.0),
            )
        )
    return spans


def aggregate_traces(spans: list[Span]) -> list[Trace]:
    """Collapse spans into one row per trace.

    A trace is marked slow if any span is slow, and error if any span failed.
    The representative service/operation is the span with the longest duration,
    which is the one a tail sampler would look at first.
    """
    buckets: dict[str, list[Span]] = {}
    for span in spans:
        buckets.setdefault(span.trace_id, []).append(span)

    traces: list[Trace] = []
    for trace_id, items in buckets.items():
        representative = max(items, key=lambda s: (s.duration_ms, s.service, s.operation))
        traces.append(
            Trace(
                trace_id=trace_id,
                service=representative.service,
                operation=representative.operation,
                duration_ms=max(span.duration_ms for span in items),
                has_error=any(span.is_error for span in items),
            )
        )
    traces.sort(key=lambda t: t.trace_id)
    return traces