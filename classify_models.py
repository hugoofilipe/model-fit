#!/usr/bin/env python3

"""Step 3 - Task-fit classification.

Runs the model-fit master-table prompt against two models (by default
``openai/gpt-5.5`` first, then ``github-copilot/claude-opus-4.8``), then merges
both answers into a single concise, provider-neutral document and writes it to
the Obsidian vault.

Source of truth for available models is always the *latest*
``working_models_list_*.json`` produced by step 2, unless ``--input`` is given.
"""

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ANSI_ESCAPE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
CONFIG_FILE = "config.json"

DEFAULT_MODELS = ["openai/gpt-5.5", "github-copilot/claude-opus-4.8"]
DEFAULT_MERGE_MODEL = "github-copilot/claude-opus-4.8"
DEFAULT_TIMEOUT = 1200


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def find_latest_working_file(root: Path) -> Path:
    candidates = sorted(
        root.glob("working_models_list_*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(
            "No working_models_list_*.json file found. Run step 2 first."
        )
    return candidates[-1]


def load_models(input_path: Path) -> list[str]:
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {input_path}: {exc}") from exc

    models = payload.get("models")
    if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
        raise ValueError(f"Expected 'models' as a list of strings in {input_path}.")
    if not models:
        raise ValueError(f"No models found in {input_path}.")
    return models


def load_use_cases(config_path: Path) -> list[dict]:
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Warning: invalid JSON in {config_path}: {exc}", file=sys.stderr)
        return []
    use_cases = data.get("use_cases", [])
    return use_cases if isinstance(use_cases, list) else []


def load_vault_path(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    raw = data.get("vault_path")
    if raw and isinstance(raw, str):
        return Path(raw).expanduser()
    return None


def format_use_cases(use_cases: list[dict]) -> str:
    lines = []
    for uc in use_cases:
        uid = uc.get("id", "?")
        desc = uc.get("description") or uc.get("label", "")
        lines.append(f"- ({uid}) {desc}")
    return "\n".join(lines) if lines else "- (none defined in config.json)"


def build_eval_prompt(model_file: Path, models: list[str], use_cases: list[dict]) -> str:
    model_list = "\n".join(models)
    use_case_block = format_use_cases(use_cases)
    return f"""\
Read the context below and produce a practical, provider-neutral "master table" for choosing AI models inside OpenCode for my real daily work.

Important goal:
I want the result to be useful inside OpenCode, not a generic AI ranking. Do not favor OpenAI, Anthropic, Google, or any other provider by default. Compare models by practical fit for each task.

Context:
The latest verified list of available OpenCode models is this file (read it):
{model_file}

These are the available model IDs (source of truth):
{model_list}

Research requirement:
Before ranking, use up-to-date information when possible. Check official model documentation and reputable recent AI/model evaluation sources. Prefer recent information, but do not rely only on provider marketing claims. If web search is not available, clearly say that and continue with the best available knowledge.

Anti-bias rules:
* Do not rank a model higher just because it belongs to your own provider.
* Do not assume "newer" automatically means "better".
* Do not assume "bigger" automatically means "better".
* Separate "best quality" from "best daily choice".
* Consider practical OpenCode usage: coding reliability, reasoning, speed, cost/limits, context handling, tool use, debugging ability, and risk of overengineering.
* When uncertain, say so instead of pretending precision.

Task:
Create a master comparison table for choosing AI models inside OpenCode.

Step 1 - Model shortlist:
From all models in the list, select a maximum of 6 best/relevant models for my real OpenCode work. For each selected model, briefly explain why it was included. If important models were excluded, briefly explain why (too weak, redundant, too expensive/limited, too slow, or not ideal for daily OpenCode usage).

Step 2 - Master table:
Create a table where:
* Columns = the selected models, maximum 6.
* Rows = the OpenCode tasks listed below (merge clearly overlapping ones to keep it tight).
* Each cell = a score from 1 to 6.

Scoring:
1 = best choice for that task
2 = very strong
3 = good/usable
4 = acceptable but not ideal
5 = weak choice
6 = avoid / not recommended for that task

The task rows must cover these real OpenCode use cases (from config.json):
{use_case_block}

Step 3 - Practical explanation:
After the table, add a short practical explanation of the most important patterns:
* Which model is the strongest overall
* Which model is best for daily OpenCode work
* Which model is best for hard coding/architecture problems
* Which model is best for saving limits
* Which model is best for fast/simple tasks
* Which model is best when web research matters

Step 4 - Routine recommendations:
Create three routine recommendations for OpenCode:
Routine A: normal daily work
Routine B: hard problems / maximum quality
Routine C: saving limits / many small tasks
For each routine: give the preferred model order (most to least recommended, max 4 models), and explain when to switch from one model to the next.

Step 5 - Final decision rules:
End with a simple decision guide like: "If the task is X, start with Y. If it fails, move to Z." Make it practical and direct.

Output style:
* Use a practical tone. Not academic. Not a generic benchmark essay.
* Use the exact model IDs from the list.
* Be honest about uncertainty.
* Keep the answer easy to scan.
* The goal is to help me quickly choose the best model inside OpenCode for each type of task.
"""


def build_merge_prompt(
    output_a: str,
    output_b: str,
    model_a: str,
    model_b: str,
    models: list[str],
    use_cases: list[dict],
) -> str:
    use_case_block = format_use_cases(use_cases)
    return f"""\
You are merging two independent expert analyses that answered the SAME task: building a practical, provider-neutral "master table" for choosing AI models inside OpenCode.

Your job: produce ONE final document that is concise, compact, and fair. Reconcile the two analyses into a single source of truth.

Merge rules:
* Do NOT favor either analysis or any provider. Be neutral.
* Where the two agree, state it confidently.
* Where they disagree on a table score, reconcile it: choose the better-justified value, or average and round. Do not invent precision. If a disagreement is large (>= 2 points), keep the more conservative practical score.
* Keep only the strongest shortlist (max 6 models). Use the exact model IDs.
* Keep the master table tight (merge overlapping tasks). One score 1-6 per cell.
* Cut filler, academic tone, and redundancy. Keep it scannable.
* Preserve the 5-part structure: (1) shortlist + exclusions, (2) master table, (3) practical patterns, (4) three routines A/B/C, (5) decision rules.
* If the two sources conflict on a factual claim and you cannot resolve it, say so briefly instead of picking arbitrarily.

Scoring legend (keep this): 1 = best practical starting choice ... 6 = avoid for that task.

The task rows should cover these OpenCode use cases (merge overlapping):
{use_case_block}

Output ONLY the final merged markdown document. No preamble, no "here is the merge", no commentary about the merging process. Start directly with the document body (begin at "## Summary").

=== ANALYSIS A (from {model_a}) ===
{output_a}

=== ANALYSIS B (from {model_b}) ===
{output_b}
"""


def run_model(model: str, prompt: str, timeout: int) -> str:
    command = ["opencode", "run", "--model", model, prompt]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("'opencode' command not found in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timeout after {timeout}s for model {model}")

    stdout_text = strip_ansi(result.stdout or "").strip()
    stderr_text = strip_ansi(result.stderr or "").strip()

    if result.returncode != 0:
        detail = stderr_text or stdout_text or f"exit code {result.returncode}"
        raise RuntimeError(f"{model} failed: {detail}")
    if not stdout_text:
        raise RuntimeError(f"{model} returned empty output")
    return stdout_text


def build_vault_note(merged_md: str, model_file: Path, models: list[str], merge_models: list[str]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    sources = " + ".join(merge_models)
    frontmatter = f"""\
---
type: projeto
title: "Model Fit - AI Model Selection Guide for OpenCode"
date: "{today}"
updated: "{today}"
status: em curso
description: "Master table and practical guide for choosing AI models inside OpenCode for daily work. Auto-merged from {sources}."
local_folder: "/home/hugoofilipe/drive/projects/model-fit"
pessoas_relacionadas: []
temas: []
google_drive_folder_url: ""
project_resume_url: ""
calendar_event_link: ""
calendar_event_id: ""
calendar_slug: ""
last_context_review: ""
tags:
  - ai
  - opencode
  - modelos
  - referencia
---

# Model Fit - AI Model Selection Guide for OpenCode

> Auto-generated by `classify_models.py` (step 3).
> Model list: `{model_file.name}` ({len(models)} verified models).
> Merged from: {sources}. Generated {today}.

"""
    return frontmatter + merged_md.strip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Step 3: run two models on the model-fit prompt, merge, and write to the vault."
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Path to working_models_list_*.json (default: latest in current directory)",
    )
    parser.add_argument(
        "--models",
        nargs=2,
        metavar=("MODEL_A", "MODEL_B"),
        default=DEFAULT_MODELS,
        help=f"The two models to run, in order (default: {DEFAULT_MODELS}).",
    )
    parser.add_argument(
        "--merge-model",
        default=DEFAULT_MERGE_MODEL,
        help=f"Model used to merge the two answers (default: {DEFAULT_MERGE_MODEL}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Destination vault note (default: vault_path from config.json / 'Model Fit.md').",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds for each model run (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run the two models concurrently instead of sequentially (faster).",
    )
    parser.add_argument(
        "--reuse-runs",
        type=Path,
        metavar="DIR",
        help=(
            "Reuse existing a_output.md / b_output.md from a prior runs/step3_* "
            "directory and skip straight to the merge."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the merged note to the project dir instead of the vault.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path.cwd()

    try:
        input_path = args.input.resolve() if args.input else find_latest_working_file(root)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        models = load_models(input_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    use_cases = load_use_cases(root / CONFIG_FILE)
    vault_path = load_vault_path(root / CONFIG_FILE)

    model_a, model_b = args.models    for m in (model_a, model_b, args.merge_model):
        if m not in models:
            print(
                f"Warning: '{m}' is not in the verified working list ({input_path.name}).",
                file=sys.stderr,
            )

    now = datetime.now()
    runs_dir = root / "runs" / f"step3_{now.strftime('%Y%m%d_%H%M%S')}"
    runs_dir.mkdir(parents=True, exist_ok=True)

    print(f"Using model list: {input_path}")
    print(f"Use cases: {len(use_cases)}")
    print(f"Run order: {model_a} -> {model_b}; merge with {args.merge_model}")
    print(f"Mode: {'parallel' if args.parallel else 'sequential'}"
          f"{'; reusing prior runs' if args.reuse_runs else ''}")
    print(f"Artifacts: {runs_dir}")

    eval_prompt = build_eval_prompt(input_path, models, use_cases)

    # Phase 1+2: obtain the two model answers (reuse, parallel, or sequential).
    if args.reuse_runs:
        reuse_dir = args.reuse_runs.resolve()
        a_path = reuse_dir / "a_output.md"
        b_path = reuse_dir / "b_output.md"
        if not a_path.exists() or not b_path.exists():
            print(
                f"Error: --reuse-runs needs a_output.md and b_output.md in {reuse_dir}",
                file=sys.stderr,
            )
            return 3
        out_a = a_path.read_text(encoding="utf-8")
        out_b = b_path.read_text(encoding="utf-8")
        print(f"\n[1-2/3] Reusing answers from {reuse_dir}")
        print(f"  {model_a}: {len(out_a)} chars | {model_b}: {len(out_b)} chars")
    elif args.parallel:
        print(f"\n[1-2/3] Running {model_a} + {model_b} in parallel (timeout {args.timeout}s each)...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(run_model, model_a, eval_prompt, args.timeout)
            fut_b = executor.submit(run_model, model_b, eval_prompt, args.timeout)
            errors = []
            try:
                out_a = fut_a.result()
            except RuntimeError as exc:
                out_a = None
                errors.append(str(exc))
            try:
                out_b = fut_b.result()
            except RuntimeError as exc:
                out_b = None
                errors.append(str(exc))
        if errors:
            for e in errors:
                print(f"Error: {e}", file=sys.stderr)
            return 3
    else:
        print(f"\n[1/3] Running {model_a} (timeout {args.timeout}s)...")
        try:
            out_a = run_model(model_a, eval_prompt, args.timeout)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 3
        print(f"  done ({len(out_a)} chars)")

        print(f"\n[2/3] Running {model_b} (timeout {args.timeout}s)...")
        try:
            out_b = run_model(model_b, eval_prompt, args.timeout)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 3
        print(f"  done ({len(out_b)} chars)")

    (runs_dir / "a_output.md").write_text(out_a, encoding="utf-8")
    (runs_dir / "b_output.md").write_text(out_b, encoding="utf-8")

    # Phase 3: merge
    print(f"\n[3/3] Merging with {args.merge_model} (timeout {args.timeout}s)...")
    merge_prompt = build_merge_prompt(out_a, out_b, model_a, model_b, models, use_cases)
    try:
        merged = run_model(args.merge_model, merge_prompt, args.timeout)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3
    (runs_dir / "merged.md").write_text(merged, encoding="utf-8")
    print(f"  done ({len(merged)} chars)")

    note = build_vault_note(merged, input_path, models, [model_a, model_b])

    if args.dry_run:
        output_path = root / "Model Fit.md"
    elif args.output:
        output_path = args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
    elif vault_path:
        output_path = vault_path / "Model Fit.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        print("Error: no output path — set vault_path in config.json or use --output", file=sys.stderr)
        return 4

    output_path.write_text(note, encoding="utf-8")
    print(f"\nSaved merged note to: {output_path}")
    print(f"Raw artifacts kept in: {runs_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
