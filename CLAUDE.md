# Benchmarks (bench/ponytail_agentic)

- Never rerun an arm that already has results for the same code. Before launching run.py, check `~/bench-runs/ponytail/*/results.json` (arms, models, tasks) and compare each arm's file mtimes against the run dir stamp; run only the missing arm (e.g. only `baseline` when frugalv vs frugal is already done) and combine the dirs. A scorer-only change is rescored from the kept workspaces, with no API.
- Comparison arms: baseline, frugal, frugalv, ponytail. Caveman is out (2026-11-27: least code reduction, more Haiku failures, +30% cost on Sonnet); do not run it again.
- When combining batches from different times, measure drift with an arm present in both, and give the cost comparison within each batch.
