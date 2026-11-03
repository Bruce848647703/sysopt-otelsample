# sysopt-otelsample

Plan **tail-based sampling** for OpenTelemetry traces under a fixed retention
budget, without losing the traces you actually debug with.

Head sampling throws away the data before you know a request failed. Tail
sampling keeps interesting traces, but "keep everything" blows up the bill.
This tool takes an offline span sample and produces the per-operation sampling
rates that fit a budget while preserving errors and latency outliers.

```text
$ sysopt-otelsample examples/spans.json --budget 120
traces:           600
budget:           120
latency threshold: 378.0ms (p99)
retained:         120 (100.0% of budget)

errors:   48/48 (100.0%)
slow:     10/10 (100.0%)
baseline: 62/542 (11.4%)

operations by baseline volume:
  catalog/GET /items                         n=140     retained=12     rate=8.6%
  search/GET /query                          n=115     retained=23     rate=20.0%
  checkout/POST /pay                         n=107     retained=12     rate=11.2%
  checkout/GET /cart                         n=100     retained=8      rate=8.0%
  auth/POST /token                           n=80      retained=7      rate=8.8%
```

## Install

```bash
pip install -e .
sysopt-otelsample examples/spans.json --budget 120

# or without installing
PYTHONPATH=src python -m sysopt_otelsample.cli examples/spans.json --budget 120
```

## Input

```json
{
  "spans": [
    {
      "trace_id": "trace-00001",
      "service": "checkout",
      "operation": "POST /pay",
      "duration_ms": 118.4,
      "status": "ok",
      "timestamp": 1760000000
    }
  ]
}
```

Multiple spans may share a `trace_id`. They are aggregated into one trace:

- duration is the longest span
- the trace is an error if **any** span status is `error` / `failed` / `failure`
- the representative service/operation is the longest span

`status` and `timestamp` are optional (`ok` and `0` by default).

## How the budget is allocated

1. Compute the slow threshold: an explicit `--latency-threshold-ms`, or the
   configured percentile (default P99) of all trace durations.
2. Build three tiers:
   - **errors** — traces with any failed span
   - **slow** — non-error traces at or above the threshold
   - **baseline** — everything else
3. Spend the budget in that order. Errors are retained first, then slow traces,
   then the remaining budget goes to baseline traffic.
4. Baseline budget is split across `service/operation` groups proportional to
   `volume x weight`, where `weight = 1 + 4 x error_ratio + 2 x slow_ratio`.
   Operations that fail or run slow more often get more of the remaining budget.
5. Traces are selected by a salted SHA-256 ranking of `trace_id`, so the
   selection is deterministic and reproducible across runs.

`--error-rate` and `--slow-rate` cap how much of the priority tiers is kept
(useful when even errors exceed the budget). `--max-rate` caps baseline
retention per operation.

## Usage

```bash
# default: text report, P99 threshold
sysopt-otelsample spans.json --budget 500

# explicit threshold and priority caps
sysopt-otelsample spans.json --budget 500 \
  --latency-threshold-ms 750 \
  --error-rate 1.0 \
  --slow-rate 0.5 \
  --max-rate 0.25

# machine-readable, or collector config
sysopt-otelsample spans.json --budget 500 --format json
sysopt-otelsample spans.json --budget 500 --format yaml
```

```yaml
tail_sampling:
  latency_threshold_ms: 378.0
  policies:
    - name: errors
      rate: 1.0000
    - name: slow
      rate: 1.0000
    - name: baseline
      per_operation:
        "checkout/POST /pay": 0.1121
        "search/GET /query": 0.2000
```

Exit codes: `0` success, `2` invalid input or options.

## Design notes

- The planner is offline by design. Run it against a representative window
  (typically 24h), then apply the rates to your collector or SDK config.
- The budget is a hard cap on retained traces. Priority tiers are taken first;
  if errors alone exceed the budget, errors are sampled at
  `budget / error_count`.
- Deterministic selection means the same trace is kept or dropped consistently
  for a given salt, which makes the plan reproducible and comparable.
- Rates are per operation, not per service, because a single service often has
  one hot endpoint and several cheap ones.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

## License

MIT