#!/usr/bin/env python3

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ANSI_ESCAPE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
DEFAULT_PROMPT = "Reply with exactly: OK"


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def find_latest_bulk_file(root: Path) -> Path:
    candidates = sorted(root.glob("bulk_models_*.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError("No bulk_models_*.json file found in project root.")
    return candidates[-1]


def load_models(input_path: Path) -> list[str]:
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {input_path}: {exc}") from exc

    models = payload.get("models")
    if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
        raise ValueError(f"Expected 'models' as a list of strings in {input_path}.")

    unique_models: list[str] = []
    seen: set[str] = set()
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        unique_models.append(model)

    if not unique_models:
        raise ValueError(f"No models found in {input_path}.")

    return unique_models


def extract_error_message(event: dict[str, Any]) -> str:
    error = event.get("error")
    if not isinstance(error, dict):
        return "error event returned"

    data = error.get("data")
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

    message = error.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return "error event returned"


def parse_json_events(raw_output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_line in raw_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def probe_model(model: str, prompt: str, timeout: int) -> tuple[bool, str]:
    command = ["opencode", "run", "--format", "json", "--model", model, prompt]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, "'opencode' command not found in PATH"
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"

    stdout_text = strip_ansi(result.stdout or "")
    stderr_text = strip_ansi(result.stderr or "")
    events = parse_json_events(stdout_text)

    for event in events:
        if event.get("type") == "error":
            return False, extract_error_message(event)

    if result.returncode != 0:
        detail = (stderr_text or stdout_text).strip()
        if not detail:
            detail = f"exit code {result.returncode}"
        return False, detail

    if "Error:" in stderr_text or "Error:" in stdout_text:
        detail = (stderr_text or stdout_text).strip()
        return False, detail or "CLI reported error"

    if not events:
        return True, "ok (empty stream)"

    has_step_finish = any(event.get("type") == "step_finish" for event in events)
    if has_step_finish:
        return True, "ok"

    return True, "ok (partial stream)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check which models from bulk_models JSON are currently working."
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Path to bulk_models_*.json (default: latest in current directory)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for each model check (default: 60)",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help=f"Prompt used for health check (default: {DEFAULT_PROMPT!r})",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of models to check in parallel (default: 1)",
    )
    parser.add_argument(
        "--chain-step3",
        choices=["ask", "yes", "no"],
        default="ask",
        help=(
            "After a successful check, run step 3 (classify_models.py): "
            "'ask' prompts when interactive, 'yes' runs it, 'no' skips (default: ask)."
        ),
    )
    return parser


def maybe_run_step3(working_file: Path, mode: str) -> None:
    """Optionally chain into step 3 after a successful availability check."""
    script = Path(__file__).resolve().parent / "classify_models.py"
    if not script.exists():
        return

    if mode == "no":
        return
    if mode == "ask":
        if not sys.stdin.isatty():
            return
        try:
            answer = input(
                "\nProceed to Step 3 (task-fit classification -> vault)? [y/N] "
            ).strip().lower()
        except EOFError:
            return
        if answer not in {"y", "yes"}:
            print("Skipping step 3.")
            return

    print("\nRunning step 3 (classify_models.py)...")
    subprocess.run(
        [sys.executable, str(script), "--input", str(working_file)],
        check=False,
    )


def main() -> int:
    args = build_parser().parse_args()
    root = Path.cwd()

    if args.parallel < 1:
        print("Error: --parallel must be >= 1.", file=sys.stderr)
        return 4

    try:
        input_path = args.input.resolve() if args.input else find_latest_bulk_file(root)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        models = load_models(input_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    total = len(models)
    indexed_results: dict[int, tuple[bool, str]] = {}

    print(f"Using input file: {input_path}")
    print(f"Checking {total} model(s) with parallel={args.parallel}...")

    if args.parallel == 1:
        for index, model in enumerate(models, start=1):
            ok, detail = probe_model(model, args.prompt, args.timeout)
            indexed_results[index - 1] = (ok, detail)
            status = "OK" if ok else "FAIL"
            print(f"[{index:02d}/{total:02d}] {status}  {model}")
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(probe_model, model, args.prompt, args.timeout): (index, model)
                for index, model in enumerate(models, start=1)
            }
            for done, future in enumerate(as_completed(futures), start=1):
                index, model = futures[future]
                ok, detail = future.result()
                indexed_results[index - 1] = (ok, detail)
                status = "OK" if ok else "FAIL"
                print(f"[{done:02d}/{total:02d}] {status}  {model}")

    working_models: list[str] = []
    failed_models: list[tuple[str, str]] = []
    for zero_based_index in range(total):
        model = models[zero_based_index]
        ok, detail = indexed_results[zero_based_index]
        if ok:
            working_models.append(model)
        else:
            failed_models.append((model, detail))

    now = datetime.now()
    output_name = f"working_models_list_{now.day:02d}_{now.month:02d}_{now.year}.json"
    output_path = root / output_name

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "source_file": str(input_path),
        "check_command": 'opencode run --format json --model "<provider/model>" "Reply with exactly: OK"',
        "timeout_seconds": args.timeout,
        "parallel_workers": args.parallel,
        "count": len(working_models),
        "models": working_models,
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\nSaved {len(working_models)} working model(s) to {output_path}")
    if failed_models:
        print(f"Failed: {len(failed_models)}")
        for model, reason in failed_models:
            print(f"  - {model}: {reason}")
    else:
        print("Failed: 0")

    if working_models:
        maybe_run_step3(output_path, args.chain_step3)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
