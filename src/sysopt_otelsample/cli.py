"""Command line entry point for sysopt-otelsample."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .planner import Options, PlanError, SamplingPlan, plan_sampling, render_yaml
from .spans import SpanError, aggregate_traces, parse_spans


def _load(path: str):
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"error: cannot read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in {path}: {exc}") from exc


def _render_text(plan: SamplingPlan, top: int) -> str:
    lines = [
        f"traces:           {plan.total_traces}",
        f"budget:           {plan.options.budget}",
        f"latency threshold: {plan.threshold_ms:.1f}ms (p{plan.options.latency_percentile:g})",
        f"retained:         {plan.retained} ({plan.budget_used:.1%} of budget)",
        "",
        (
            f"errors:   {plan.error_retained}/{plan.error_count} "
            f"({plan.error_rate:.1%})"
        ),
        (
            f"slow:     {plan.slow_retained}/{plan.slow_count} "
            f"({plan.slow_rate:.1%})"
        ),
        (
            f"baseline: {plan.baseline_retained}/{plan.baseline_count} "
            f"({plan.baseline_rate:.1%})"
        ),
        "",
        "operations by baseline volume:",
    ]
    for group in sorted(plan.groups, key=lambda g: (-g.traces, g.key))[:top]:
        lines.append(
            f"  {group.key:<42} n={group.traces:<7} "
            f"retained={group.retained:<6} rate={group.rate:.1%}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sysopt-otelsample",
        description="Plan tail-based trace sampling under a retention budget.",
    )
    parser.add_argument("spans", help="span JSON file, or '-' for stdin")
    parser.add_argument("-b", "--budget", type=int, required=True, help="maximum retained traces")
    parser.add_argument(
        "--latency-percentile",
        type=float,
        default=99.0,
        help="percentile used as the slow threshold (default: 99)",
    )
    parser.add_argument(
        "--latency-threshold-ms",
        type=float,
        default=None,
        help="explicit slow threshold in milliseconds",
    )
    parser.add_argument(
        "--error-rate", type=float, default=1.0, help="sampling rate for error traces"
    )
    parser.add_argument(
        "--slow-rate", type=float, default=1.0, help="sampling rate for slow traces"
    )
    parser.add_argument(
        "--max-rate", type=float, default=1.0, help="upper bound for baseline sampling"
    )
    parser.add_argument("--salt", default="", help="salt for deterministic trace selection")
    parser.add_argument(
        "--format", choices=("text", "json", "yaml"), default="text", help="output format"
    )
    parser.add_argument(
        "--top", type=int, default=10, help="operations to list in text output (default: 10)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = _load(args.spans)

    try:
        traces = aggregate_traces(parse_spans(payload))
        plan = plan_sampling(
            traces,
            Options(
                budget=args.budget,
                latency_percentile=args.latency_percentile,
                latency_threshold_ms=args.latency_threshold_ms,
                error_rate=args.error_rate,
                slow_rate=args.slow_rate,
                max_rate=args.max_rate,
                salt=args.salt,
            ),
        )
    except (SpanError, PlanError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(plan.to_dict(), indent=2))
    elif args.format == "yaml":
        print(render_yaml(plan))
    else:
        print(_render_text(plan, args.top))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())