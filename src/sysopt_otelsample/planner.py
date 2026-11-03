"""Budget-aware tail-sampling planner."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .spans import Trace


class PlanError(ValueError):
    """Raised when the planning inputs are inconsistent."""


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise PlanError("cannot compute a quantile without samples")
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _score(trace_id: str, salt: str) -> int:
    digest = hashlib.sha256(f"{salt}:{trace_id}".encode()).hexdigest()
    return int(digest[:16], 16)


@dataclass
class Options:
    budget: int
    latency_percentile: float = 99.0
    latency_threshold_ms: float | None = None
    error_rate: float = 1.0
    slow_rate: float = 1.0
    max_rate: float = 1.0
    salt: str = ""


@dataclass
class GroupPlan:
    service: str
    operation: str
    traces: int
    errors: int
    slow: int
    retained: int

    @property
    def rate(self) -> float:
        return self.retained / self.traces if self.traces else 0.0

    @property
    def key(self) -> str:
        return f"{self.service}/{self.operation}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "operation": self.operation,
            "traces": self.traces,
            "errors": self.errors,
            "slow": self.slow,
            "retained": self.retained,
            "rate": round(self.rate, 6),
        }


@dataclass
class SamplingPlan:
    options: Options
    total_traces: int
    threshold_ms: float
    error_count: int
    slow_count: int
    baseline_count: int
    error_retained: int
    slow_retained: int
    baseline_retained: int
    groups: list[GroupPlan] = field(default_factory=list)

    @property
    def retained(self) -> int:
        return self.error_retained + self.slow_retained + self.baseline_retained

    @property
    def error_rate(self) -> float:
        return self.error_retained / self.error_count if self.error_count else 0.0

    @property
    def slow_rate(self) -> float:
        return self.slow_retained / self.slow_count if self.slow_count else 0.0

    @property
    def baseline_rate(self) -> float:
        return self.baseline_retained / self.baseline_count if self.baseline_count else 0.0

    @property
    def budget_used(self) -> float:
        return self.retained / self.options.budget if self.options.budget else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.options.budget,
            "total_traces": self.total_traces,
            "retained": self.retained,
            "budget_used": round(self.budget_used, 6),
            "latency_threshold_ms": round(self.threshold_ms, 6),
            "tiers": {
                "errors": {
                    "count": self.error_count,
                    "retained": self.error_retained,
                    "rate": round(self.error_rate, 6),
                },
                "slow": {
                    "count": self.slow_count,
                    "retained": self.slow_retained,
                    "rate": round(self.slow_rate, 6),
                },
                "baseline": {
                    "count": self.baseline_count,
                    "retained": self.baseline_retained,
                    "rate": round(self.baseline_rate, 6),
                },
            },
            "groups": [group.to_dict() for group in self.groups],
        }


def select_traces(traces: list[Trace], count: int, salt: str) -> list[Trace]:
    if count <= 0:
        return []
    ranked = sorted(traces, key=lambda t: (_score(t.trace_id, salt), t.trace_id))
    return ranked[:count]


def _allocate(
    sizes: dict[tuple[str, str], int],
    weights: dict[tuple[str, str], float],
    budget: int,
    max_rate: float,
) -> dict[tuple[str, str], int]:
    """Allocate an integer budget proportionally to size x weight."""
    allocation = dict.fromkeys(sizes, 0)
    budget = min(budget, sum(sizes.values()))
    while budget > 0:
        candidates = [
            key
            for key, size in sizes.items()
            if allocation[key] < min(size, int(size * max_rate))
        ]
        if not candidates:
            break
        total_weight = sum(sizes[key] * weights[key] for key in candidates)
        added = 0
        if total_weight > 0:
            for key in sorted(candidates):
                share = budget * sizes[key] * weights[key] / total_weight
                capacity = min(sizes[key], int(sizes[key] * max_rate)) - allocation[key]
                increment = min(int(share), capacity)
                if increment > 0:
                    allocation[key] += increment
                    added += increment
        if added == 0:
            key = max(candidates, key=lambda k: (weights[k], sizes[k], k))
            allocation[key] += 1
            added = 1
        budget -= added
    return allocation


def plan_sampling(traces: list[Trace], options: Options) -> SamplingPlan:
    if options.budget < 1:
        raise PlanError("budget must be at least 1")
    if not traces:
        raise PlanError("no traces to plan")
    if not 0.0 <= options.error_rate <= 1.0:
        raise PlanError("error_rate must be between 0 and 1")
    if not 0.0 <= options.slow_rate <= 1.0:
        raise PlanError("slow_rate must be between 0 and 1")
    if not 0.0 <= options.max_rate <= 1.0:
        raise PlanError("max_rate must be between 0 and 1")

    durations = [trace.duration_ms for trace in traces]
    threshold = (
        options.latency_threshold_ms
        if options.latency_threshold_ms is not None
        else _quantile(durations, options.latency_percentile / 100.0)
    )

    errors = [trace for trace in traces if trace.has_error]
    slow = [
        trace for trace in traces if not trace.has_error and trace.duration_ms >= threshold
    ]
    baseline = [
        trace for trace in traces if not trace.has_error and trace.duration_ms < threshold
    ]

    remaining = min(options.budget, len(traces))
    error_take = min(len(errors), int(len(errors) * options.error_rate), remaining)
    remaining -= error_take
    slow_take = min(len(slow), int(len(slow) * options.slow_rate), remaining)
    remaining -= slow_take
    baseline_take = remaining

    sizes: dict[tuple[str, str], int] = {}
    group_errors: dict[tuple[str, str], int] = {}
    group_slow: dict[tuple[str, str], int] = {}
    group_baseline: dict[tuple[str, str], int] = {}
    for trace in traces:
        key = trace.group
        sizes[key] = sizes.get(key, 0) + 1
        if trace.has_error:
            group_errors[key] = group_errors.get(key, 0) + 1
        elif trace.duration_ms >= threshold:
            group_slow[key] = group_slow.get(key, 0) + 1
        else:
            group_baseline[key] = group_baseline.get(key, 0) + 1

    weights: dict[tuple[str, str], float] = {}
    for key, size in sizes.items():
        error_ratio = group_errors.get(key, 0) / size
        slow_ratio = group_slow.get(key, 0) / size
        weights[key] = 1.0 + 4.0 * error_ratio + 2.0 * slow_ratio

    allocation = _allocate(group_baseline, weights, baseline_take, options.max_rate)

    groups: list[GroupPlan] = []
    for key in sorted(group_baseline):
        groups.append(
            GroupPlan(
                service=key[0],
                operation=key[1],
                traces=group_baseline[key],
                errors=group_errors.get(key, 0),
                slow=group_slow.get(key, 0),
                retained=allocation.get(key, 0),
            )
        )

    return SamplingPlan(
        options=options,
        total_traces=len(traces),
        threshold_ms=threshold,
        error_count=len(errors),
        slow_count=len(slow),
        baseline_count=len(baseline),
        error_retained=len(select_traces(errors, error_take, options.salt)),
        slow_retained=len(select_traces(slow, slow_take, options.salt)),
        baseline_retained=sum(allocation.values()),
        groups=groups,
    )


def render_yaml(plan: SamplingPlan) -> str:
    lines = [
        "tail_sampling:",
        f"  latency_threshold_ms: {plan.threshold_ms:.1f}",
        "  policies:",
        "    - name: errors",
        f"      rate: {plan.error_rate:.4f}",
        "    - name: slow",
        f"      rate: {plan.slow_rate:.4f}",
        "    - name: baseline",
        "      per_operation:",
    ]
    for group in plan.groups:
        lines.append(f'        "{group.key}": {group.rate:.4f}')
    return "\n".join(lines)