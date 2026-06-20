# model-fit

Automates discovery, validation, and classification of all available OpenCode models,
then writes a provider-neutral "Model Fit" routing table to an Obsidian note.

## Pipeline

```
step 1: discover_models.py      → bulk_models_<date>.json
step 2: check_working_models.py → working_models_list_<date>.json
step 3: classify_models.py      → vault: "Model Fit.md"
```

Each step reads the latest output of the previous one automatically.

## Quick start

```bash
# Full pipeline (interactive, confirms before each step)
python3 run_pipeline.py

# Full pipeline, unattended
python3 run_pipeline.py --yes --parallel-classify

# Skip discovery + check (models already verified today), re-classify only
python3 run_pipeline.py --skip-discover --skip-check --yes

# Re-merge only (tweak prompt without re-running the expensive model calls)
python3 classify_models.py --reuse-runs runs/step3_<timestamp> --merge-model github-copilot/claude-sonnet-4.6
```

## Configuration — `config.json`

```json
{
  "provider_whitelist": ["github-copilot", "openai"],
  "use_cases": [
    { "id": 1, "label": "programming_new_feature", "description": "Programming a new feature" }
  ]
}
```

| Key | Effect |
|---|---|
| `provider_whitelist` | Restrict step 1 discovery to these providers. Empty = all providers. |
| `use_cases` | Task categories injected as table rows into the step 3 prompt. |

## Scripts

### `discover_models.py` — step 1

Runs `opencode models --refresh`, filters by `provider_whitelist`, writes `bulk_models_DD_MM_YYYY.json`.

```bash
python3 discover_models.py
```

### `check_working_models.py` — step 2

Reads the latest `bulk_models_*.json`, probes each model with a lightweight call,
writes `working_models_list_DD_MM_YYYY.json` with only the confirmed-working models.

```bash
python3 check_working_models.py [--input FILE] [--timeout 60] [--parallel 6]
                                [--prompt "Reply with exactly: OK"]
                                [--chain-step3 ask|yes|no]
```

`--chain-step3` — after a successful check, prompt to continue into step 3
(`ask` when interactive, `yes` always, `no` never). Default: `ask`.

### `classify_models.py` — step 3

Runs the master-table prompt against two models, merges both answers via a third
model call, writes the result to the Obsidian vault note `Model Fit.md`.
Raw per-model outputs are archived in `runs/step3_<timestamp>/`.

```bash
python3 classify_models.py [--input FILE] [--parallel] [--reuse-runs DIR]
                           [--models A B] [--merge-model ID]
                           [--timeout 1200] [--output PATH] [--dry-run]
```

| Flag | Default | What it does |
|---|---|---|
| `--parallel` | off | Run the two models concurrently (saves ~50% wall time) |
| `--reuse-runs DIR` | — | Skip model runs A+B; go straight to merge using existing outputs |
| `--models A B` | `openai/gpt-5.5 github-copilot/claude-opus-4.8` | The two models to evaluate |
| `--merge-model` | `github-copilot/claude-opus-4.8` | Model used to synthesise the final table |
| `--dry-run` | off | Write to project dir instead of vault |

Typical durations: ~2 min (gpt-5.5) + ~6 min (opus-4.8) sequential, or ~6 min parallel.
Re-merge only with `--reuse-runs`: ~1 min.

### `run_pipeline.py` — end-to-end runner

```bash
python3 run_pipeline.py [--yes] [--skip-discover] [--skip-check]
                        [--parallel-check N] [--parallel-classify]
                        [--merge-model ID] [--dry-run]
```

| Flag | What it does |
|---|---|
| `--yes` | Skip all confirmation prompts (unattended) |
| `--skip-discover` | Reuse latest `bulk_models_*.json` |
| `--skip-check` | Reuse latest `working_models_list_*.json` |
| `--parallel-check N` | Parallel workers for step 2 |
| `--parallel-classify` | Run the two step 3 models concurrently |

Non-interactive without `--yes` stops before step 3 to prevent unintended vault writes.

## Output files

| File | Produced by | Contents |
|---|---|---|
| `bulk_models_DD_MM_YYYY.json` | step 1 | All discovered model IDs (filtered by whitelist) |
| `working_models_list_DD_MM_YYYY.json` | step 2 | Confirmed-working model IDs only |
| `runs/step3_<timestamp>/a_output.md` | step 3 | Raw output from model A |
| `runs/step3_<timestamp>/b_output.md` | step 3 | Raw output from model B |
| `runs/step3_<timestamp>/merged.md` | step 3 | Merged result before frontmatter |
| `vault: Model Fit.md` | step 3 | Final note written to Obsidian |

## Roadmap

- Historical tracking: archive versioned vault snapshots to compare recommendations over time.
- Cost/speed weighting: factor in real token prices and response times into scores.
- Scheduled refresh: cron/systemd timer to re-run the pipeline when models list changes.
