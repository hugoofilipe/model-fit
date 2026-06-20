#!/usr/bin/env python3

"""End-to-end model-fit pipeline.

Chains the three steps in order:

    1. discover_models.py      -> bulk_models_<date>.json
    2. check_working_models.py -> working_models_list_<date>.json
    3. classify_models.py      -> merged "Model Fit" note in the vault

Each step runs as a subprocess so the individual scripts stay the single source
of truth for their own logic. By default the pipeline asks for confirmation
before each step; use --yes to run unattended.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STEP1 = ROOT / "discover_models.py"
STEP2 = ROOT / "check_working_models.py"
STEP3 = ROOT / "classify_models.py"


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        # Non-interactive without --yes: be safe and stop.
        print(f"{prompt} -> no TTY and --yes not set; stopping.", file=sys.stderr)
        return False
    try:
        answer = input(f"{prompt} [Y/n] ").strip().lower()
    except EOFError:
        return False
    return answer in {"", "y", "yes"}


def run_step(label: str, command: list[str]) -> int:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    print("$ " + " ".join(command))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(f"\n{label} failed (exit {result.returncode}).", file=sys.stderr)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the full model-fit pipeline (discover -> check -> classify)."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run all steps without asking for confirmation.",
    )
    parser.add_argument(
        "--skip-discover",
        action="store_true",
        help="Skip step 1; reuse the latest bulk_models_*.json.",
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip step 2; reuse the latest working_models_list_*.json.",
    )
    parser.add_argument(
        "--parallel-check",
        type=int,
        default=1,
        help="Parallel workers for step 2 availability check (default: 1).",
    )
    parser.add_argument(
        "--parallel-classify",
        action="store_true",
        help="Run the two step-3 models concurrently.",
    )
    parser.add_argument(
        "--merge-model",
        help="Override the step-3 merge model.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Step 3 writes the merged note to the project dir, not the vault.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    for script in (STEP1, STEP2, STEP3):
        if not script.exists():
            print(f"Error: missing {script.name}", file=sys.stderr)
            return 1

    # Step 1 - discover
    if args.skip_discover:
        print("Step 1 (discover): skipped, reusing latest bulk_models_*.json")
    elif confirm("Run step 1 (discover models)?", args.yes):
        if run_step("STEP 1 - discover models", [sys.executable, str(STEP1)]) != 0:
            return 1
    else:
        print("Stopped before step 1.")
        return 0

    # Step 2 - availability check (do not let step 2 chain into step 3 itself)
    if args.skip_check:
        print("Step 2 (check): skipped, reusing latest working_models_list_*.json")
    elif confirm("Run step 2 (availability check)?", args.yes):
        cmd = [
            sys.executable,
            str(STEP2),
            "--parallel",
            str(args.parallel_check),
            "--chain-step3",
            "no",
        ]
        if run_step("STEP 2 - availability check", cmd) != 0:
            return 1
    else:
        print("Stopped before step 2.")
        return 0

    # Step 3 - classify + merge + vault
    if confirm("Run step 3 (classify, merge, write to vault)?", args.yes):
        cmd = [sys.executable, str(STEP3)]
        if args.parallel_classify:
            cmd.append("--parallel")
        if args.merge_model:
            cmd += ["--merge-model", args.merge_model]
        if args.dry_run:
            cmd.append("--dry-run")
        if run_step("STEP 3 - classify and merge", cmd) != 0:
            return 1
    else:
        print("Stopped before step 3.")
        return 0

    print("\nPipeline complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
