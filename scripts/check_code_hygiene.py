#!/usr/bin/env python3
# scripts/check_code_hygiene.py
# date created: 2026-09-06 18:45:00
#
"""
Deterministic Code Hygiene & Wiring Verification Runner.

Executes a three-stage mechanical gate:
1. Ruff Linter & Syntax Analysis (flake8, bugbear, async safety, modern syntax)
2. AST Config-Wiring Pytest (asserts all evelyn_config.py constants have active consumers)
3. Vulture Dead-Code Inspection (detects uncalled functions, unused classes/variables)

Exit Codes:
  0: All hygiene and wiring gates PASSED.
  1: One or more gates FAILED.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTEST = WORKSPACE_ROOT / "venv" / "bin" / "pytest"
VENV_RUFF = WORKSPACE_ROOT / "venv" / "bin" / "ruff"
VENV_VULTURE = WORKSPACE_ROOT / "venv" / "bin" / "vulture"


# ANSI Terminal Colors
class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def run_stage(title: str, cmd: list[str], env: dict | None = None) -> bool:
    """Execute a single hygiene stage and stream formatted output."""
    print(f"\n{Colors.BOLD}{Colors.OKBLUE}=== Stage: {title} ==={Colors.ENDC}")
    print(f"{Colors.OKCYAN}Command:{Colors.ENDC} {' '.join(cmd)}")

    run_env = os.environ.copy()
    run_env["PYTHONPATH"] = str(WORKSPACE_ROOT)
    if env:
        run_env.update(env)

    result = subprocess.run(
        cmd,
        cwd=str(WORKSPACE_ROOT),
        env=run_env,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(f"{Colors.WARNING}{result.stderr.strip()}{Colors.ENDC}")

    if result.returncode == 0:
        print(f"{Colors.OKGREEN}✔ {title} PASSED{Colors.ENDC}")
        return True
    else:
        print(f"{Colors.FAIL}✖ {title} FAILED (exit code: {result.returncode}){Colors.ENDC}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evelyn Deterministic Code Hygiene & Wiring Verification Runner"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Instruct Ruff to automatically fix safe lint violations.",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=70,
        help="Minimum confidence threshold for Vulture (default: 70).",
    )
    parser.add_argument(
        "--skip-ruff",
        action="store_true",
        help="Skip Ruff linting stage.",
    )
    parser.add_argument(
        "--skip-vulture",
        action="store_true",
        help="Skip Vulture dead-code stage.",
    )
    parser.add_argument(
        "--skip-config-test",
        action="store_true",
        help="Skip AST config-wiring pytest stage.",
    )
    args = parser.parse_args()

    # Verify executables
    pytest_bin = str(VENV_PYTEST) if VENV_PYTEST.exists() else "pytest"
    ruff_bin = str(VENV_RUFF) if VENV_RUFF.exists() else "ruff"
    vulture_bin = str(VENV_VULTURE) if VENV_VULTURE.exists() else "vulture"

    stages_passed = []

    # 1. Ruff Lint Stage
    if not args.skip_ruff:
        ruff_cmd = [ruff_bin, "check", "."]
        if args.fix:
            ruff_cmd.append("--fix")
        passed = run_stage("1. Ruff Static Linting & Pattern Check", ruff_cmd)
        stages_passed.append(("Ruff Linting", passed))
        if not passed and not args.fix:
            print(f"{Colors.WARNING}Tip: Run with --fix to automatically resolve autofixable lints.{Colors.ENDC}")

    # 2. AST Config Wiring Pytest Stage
    if not args.skip_config_test:
        test_cmd = [
            pytest_bin,
            "-v",
            "Evelyn/tests/test_config_wiring.py",
        ]
        passed = run_stage("2. AST Config-Wiring Verification (test_config_wiring.py)", test_cmd)
        stages_passed.append(("AST Config-Wiring Pytest", passed))

    # 3. Vulture Dead Code Stage
    if not args.skip_vulture:
        vulture_cmd = [vulture_bin, f"--min-confidence={args.min_confidence}"]
        passed = run_stage("3. Vulture Compiler-Level Dead-Code Detection", vulture_cmd)
        stages_passed.append(("Vulture Dead-Code Inspection", passed))

    # Summary
    print(f"\n{Colors.BOLD}{Colors.HEADER}================ SUMMARY ================{Colors.ENDC}")
    all_passed = True
    for name, passed in stages_passed:
        status = f"{Colors.OKGREEN}PASSED{Colors.ENDC}" if passed else f"{Colors.FAIL}FAILED{Colors.ENDC}"
        print(f"  • {name:<35} : {status}")
        if not passed:
            all_passed = False

    print(f"{Colors.BOLD}{Colors.HEADER}========================================={Colors.ENDC}")
    if all_passed:
        print(f"\n{Colors.BOLD}{Colors.OKGREEN}🎉 All code hygiene & wiring gates PASSED successfully!{Colors.ENDC}\n")
        return 0
    else:
        print(f"\n{Colors.BOLD}{Colors.FAIL}❌ Code hygiene gate failed. Please resolve the reported issues above.{Colors.ENDC}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
