from __future__ import annotations

import pytest

from sysopt_otelsample.planner import (
    Options,
    PlanError,
    plan_sampling,
    render_yaml,
    select_traces,
)
from sysopt_otelsample.spans import Trace


def trace(trace_id, duration=10.0, error=False, service="api", operation="GET /"):
    return Trace(trace_id, service, operation, duration, error)


def test_errors_are_retained_first():
    traces = [trace(f"e{i}", error=True) for i in range(10)]
    traces += [trace(f"n{i}") for i in range(90)]

    plan = plan_sampling(traces, Options(budget=20, latency_threshold_ms=500))

    assert plan.error_retained == 10
    assert plan.slow_retained == 0
    assert plan.baseline_retained == 10
    assert plan.retained == 20
    assert plan.budget_used == 1.0


def test_budget_smaller_than_the_error_count_still_respects_the_budget():
    traces = [trace(f"e{i}", error=True) for i in range(10)]

    plan = plan_sampling(traces, Options(budget=5, latency_threshold_ms=500))

    assert plan.error_retained == 5
    assert plan.retained == 5


def test_slow_traces_are_retained_before_baseline():
    traces = [trace(f"s{i}", duration=900.0) for i in range(4)]
    traces += [trace(f"n{i}") for i in range(50)]

    plan = plan_sampling(traces, Options(budget=4, latency_threshold_ms=500))

    assert plan.slow_count == 4
    assert plan.slow_retained == 4
    assert plan.baseline_retained == 0


def test_max_rate_caps_baseline_sampling():
    traces = [trace(f"n{i}") for i in range(100)]

    plan = plan_sampling(traces, Options(budget=100, latency_threshold_ms=500, max_rate=0.1))

    assert plan.baseline_retained == 10
    assert all(group.rate <= 0.1 for group in plan.groups)


def test_groups_with_errors_get_more_baseline_budget():
    traces = [trace(f"a-e{i}", error=True, service="a") for i in range(20)]
    traces += [trace(f"a-n{i}", service="a") for i in range(80)]
    traces += [trace(f"b-n{i}", service="b") for i in range(100)]

    plan = plan_sampling(traces, Options(budget=45, latency_threshold_ms=500))
    groups = {group.service: group for group in plan.groups}

    assert groups["a"].rate > groups["b"].rate > 0
    assert plan.retained <= 45


def test_error_and_slow_rates_limit_priority_retention():
    traces = [trace(f"e{i}", error=True) for i in range(10)]
    traces += [trace(f"s{i}", duration=900.0) for i in range(10)]
    traces += [trace(f"n{i}") for i in range(80)]

    plan = plan_sampling(
        traces,
        Options(budget=100, latency_threshold_ms=500, error_rate=0.5, slow_rate=0.5),
    )

    assert plan.error_retained == 5
    assert plan.slow_retained == 5


def test_selection_is_deterministic_and_salt_dependent():
    traces = [trace(f"n{i}") for i in range(100)]

    first = [t.trace_id for t in select_traces(traces, 10, "one")]
    second = [t.trace_id for t in select_traces(traces, 10, "one")]
    other = [t.trace_id for t in select_traces(traces, 10, "two")]

    assert first == second
    assert first != other


def test_plan_is_deterministic():
    traces = [trace(f"n{i}", duration=float(i)) for i in range(50)]

    first = plan_sampling(traces, Options(budget=10, latency_threshold_ms=500)).to_dict()
    second = plan_sampling(traces, Options(budget=10, latency_threshold_ms=500)).to_dict()

    assert first == second


def test_explicit_threshold_overrides_the_percentile():
    traces = [trace(f"n{i}", duration=float(i)) for i in range(100)]

    plan = plan_sampling(
        traces, Options(budget=100, latency_threshold_ms=60.0, latency_percentile=99)
    )

    assert plan.threshold_ms == 60.0
    assert plan.slow_count == 40


def test_invalid_options_are_rejected():
    traces = [trace("t1")]

    with pytest.raises(PlanError, match="budget"):
        plan_sampling(traces, Options(budget=0))
    with pytest.raises(PlanError, match="max_rate"):
        plan_sampling(traces, Options(budget=1, max_rate=2))
    with pytest.raises(PlanError, match="no traces"):
        plan_sampling([], Options(budget=1))


def test_yaml_output_lists_policies_and_operations():
    traces = [trace("t1", service="api", operation="GET /")]
    traces += [trace("t2", service="api", operation="GET /")]
    traces += [trace("t3", service="db", operation="SELECT")]

    plan = plan_sampling(traces, Options(budget=2, latency_threshold_ms=500))
    rendered = render_yaml(plan)

    assert "tail_sampling:" in rendered
    assert "name: errors" in rendered
    assert 'api/GET /' in rendered
    assert 'db/SELECT' in rendered