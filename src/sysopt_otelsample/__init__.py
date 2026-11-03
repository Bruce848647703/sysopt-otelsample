"""Public API for sysopt-otelsample."""

from .planner import GroupPlan, Options, PlanError, SamplingPlan, plan_sampling, render_yaml
from .spans import Span, SpanError, Trace, aggregate_traces, parse_spans

__all__ = [
    "GroupPlan",
    "Options",
    "PlanError",
    "SamplingPlan",
    "Span",
    "SpanError",
    "Trace",
    "aggregate_traces",
    "parse_spans",
    "plan_sampling",
    "render_yaml",
]

__version__ = "0.1.0"