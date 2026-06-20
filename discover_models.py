#!/usr/bin/env python3

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ANSI_ESCAPE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
MODEL_LINE = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")
CONFIG_FILE = "config.json"


def parse_models(output: str) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()

    for raw_line in output.splitlines():
        line = ANSI_ESCAPE.sub("", raw_line).strip()
        if not line:
            continue
        if not MODEL_LINE.match(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        models.append(line)

    return models


def load_provider_whitelist(config_path: Path) -> set[str]:
    if not config_path.exists():
        return set()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Warning: invalid JSON in {config_path}: {exc}", file=sys.stderr)
        return set()

    raw = data.get("provider_whitelist", [])
    if not isinstance(raw, list):
        return set()

    return {str(p).strip().lower() for p in raw if str(p).strip()}


def main() -> int:
    command = ["opencode", "models", "--refresh"]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        print("Error: 'opencode' command not found in PATH.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print("Error running OpenCode command:", file=sys.stderr)
        print(exc.stderr or exc.stdout, file=sys.stderr)
        return exc.returncode

    models = parse_models(result.stdout)
    if not models:
        print("Error: no models found in command output.", file=sys.stderr)
        return 2

    provider_whitelist = load_provider_whitelist(Path.cwd() / CONFIG_FILE)
    if provider_whitelist:
        models = [
            model
            for model in models
            if model.split("/", 1)[0].lower() in provider_whitelist
        ]

    if not models:
        print(
            "Error: no models matched provider_whitelist from config.json.",
            file=sys.stderr,
        )
        return 3

    now = datetime.now()
    file_name = f"bulk_models_{now.day:02d}_{now.month:02d}_{now.year}.json"
    output_path = Path.cwd() / file_name

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "command": "opencode models --refresh",
        "provider_whitelist": sorted(provider_whitelist),
        "count": len(models),
        "models": models,
    }

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(models)} models to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
