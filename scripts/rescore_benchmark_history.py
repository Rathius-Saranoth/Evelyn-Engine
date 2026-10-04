#!/usr/bin/env python3
# rescore_benchmark_history.py
# date created: 2026-10-04 10:13:16
# date modified: 2026-10-04 10:14:47
# tags:

# rescore_benchmark_history.py — Rescore historical benchmark runs with condition checkpoints
# date created: 2026-10-04 10:15:00
# tags: #benchmark, #rescore, #conditions, #history

"""
rescore_benchmark_history.py — Upgrades legacy benchmark history and pre-computed
matrix files to condition-level scoring checkpoints.

Inspects raw replies and tool invocations from previous benchmark runs in
data/benchmark_history.json and reference/behavior_benchmark_matrix.json,
evaluates each case against the v2 condition specifications in
reference/behavior_benchmark_cases.json, and writes enriched condition checkpoints
and updated summaries.
"""

from __future__ import annotations

import json
from pathlib import Path

from Evelyn.tools.benchmark_conditions import (
    TurnContext,
    case_passed,
    load_suite,
    primary_category,
)
from scripts.benchmark_behavior import summarise

BASE_DIR = Path(__file__).resolve().parent.parent
CASES_FILE = BASE_DIR / "reference" / "behavior_benchmark_cases.json"
HISTORY_FILE = BASE_DIR / "data" / "benchmark_history.json"
MATRIX_FILE = BASE_DIR / "reference" / "behavior_benchmark_matrix.json"


def rescore_results(results: list[dict], cases_by_id: dict[str, dict], shared: dict) -> list[dict]:
    """Rescore a list of case result dicts against condition definitions."""
    from Evelyn.tools.benchmark_conditions import evaluate_conditions

    updated = []
    for r in results:
        cid = str(r.get("id", ""))
        c_def = cases_by_id.get(cid)
        if not c_def:
            updated.append(r)
            continue

        called = r.get("called", []) or []
        writes = r.get("writes", []) or []
        reply = r.get("reply", "") or ""

        calls = [
            {
                "name": name,
                "args": {},
                "response": '{"status": "ok"}',
                "write": name in writes,
            }
            for name in called
        ]
        ctx = TurnContext(reply=reply, calls=calls, shared=shared)
        cond_results = evaluate_conditions(c_def, ctx)
        c_pass = case_passed(cond_results)

        r_new = dict(r)
        r_new["conditions"] = cond_results
        r_new["passed"] = c_pass
        r_new["category"] = primary_category(c_def)
        updated.append(r_new)
    return updated


def main() -> None:
    if not CASES_FILE.exists():
        print(f"Error: {CASES_FILE} not found")
        return

    shared, cases = load_suite(str(CASES_FILE))
    cases_by_id = {c["id"]: c for c in cases}
    print(f"Loaded {len(cases)} test cases from {CASES_FILE.name}")

    # 1. Rescore data/benchmark_history.json
    if HISTORY_FILE.exists():
        print(f"\nRescoring {HISTORY_FILE}...")
        with open(HISTORY_FILE, encoding="utf-8") as f:
            hist_data = json.load(f)

        total_runs_rescored = 0
        for partition in ("live", "template", "probes"):
            runs = hist_data.get(partition, [])
            for run in runs:
                orig_res = run.get("results", [])
                rescored_res = rescore_results(orig_res, cases_by_id, shared)
                run["results"] = rescored_res
                run["summary"] = summarise(rescored_res)
                total_runs_rescored += 1
                s = run["summary"]
                strict = s.get("strict_cases", {})
                print(
                    f"  [{partition}] {run.get('run_id')}: "
                    f"{s.get('passed')}/{s.get('total')} conds ({(s.get('pass_rate', 0)*100):.1f}%) | "
                    f"strict: {strict.get('passed')}/{strict.get('total')}"
                )

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist_data, f, indent=2)
        print(f"✔ Successfully rescored {total_runs_rescored} runs in {HISTORY_FILE.name}")

    # 2. Rescore reference/behavior_benchmark_matrix.json
    if MATRIX_FILE.exists():
        print(f"\nRescoring {MATRIX_FILE}...")
        with open(MATRIX_FILE, encoding="utf-8") as f:
            mat_data = json.load(f)

        details = mat_data.get("details", {})
        summaries = mat_data.get("summaries", {})

        for model_name, case_runs in details.items():
            rescored_res = rescore_results(case_runs, cases_by_id, shared)
            details[model_name] = rescored_res
            summaries[model_name] = summarise(rescored_res)
            s = summaries[model_name]
            strict = s.get("strict_cases", {})
            print(
                f"  [matrix] {model_name}: "
                f"{s.get('passed')}/{s.get('total')} conds ({(s.get('pass_rate', 0)*100):.1f}%) | "
                f"strict: {strict.get('passed')}/{strict.get('total')}"
            )

        with open(MATRIX_FILE, "w", encoding="utf-8") as f:
            json.dump(mat_data, f, indent=2)
        print(f"✔ Successfully rescored {len(details)} models in {MATRIX_FILE.name}")


if __name__ == "__main__":
    main()
