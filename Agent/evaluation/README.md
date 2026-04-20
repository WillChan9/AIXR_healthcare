# Evaluation: Phase 1 Scene Generation Quality

Automated evaluation framework for testing the Phase 1 static environment generation pipeline across 10 medical/healthcare scenarios.

## Directory Structure

```
evaluation/
  scenarios/                  # 10 experience files (S01-S10)
    experience_S01.txt        # Emergency Triage Room (4x4m, 12 obj)
    experience_S02.txt        # Hospital Ward 4-Bed (8x6m, 25 obj)
    experience_S03.txt        # Physical Therapy Gym (6x6m, 30 obj)  [baseline]
    experience_S04.txt        # Surgery Prep Room (5x5m, 20 obj)
    experience_S05.txt        # Waiting Room (6x8m, 15 obj)
    experience_S06.txt        # ICU Single-Patient (4x5m, 18 obj)
    experience_S07.txt        # Medical Supply Closet (3x3m, 20 obj)
    experience_S08.txt        # Rehabilitation Corridor (3x10m, 15 obj)
    experience_S09.txt        # Doctor's Office (5x5m, 15 obj)
    experience_S10.txt        # Nurse Station (8x8m, 35 obj)
  results/                    # Auto-created: per-scenario/trial outputs
    S01/trial_1/              # scene_state.json, reflection.json, etc.
    S01/trial_2/
    ...
  reports/                    # Auto-created: aggregated results
    summary.json
    summary.csv
    summary_table.txt
    charts/                   # Bar charts + radar chart (requires matplotlib)
  run_experiment.py           # Main orchestrator
  evaluate_scene.py           # Geometric + semantic + plan + efficiency metrics
  llm_judge.py                # LLM-as-judge (coherence + spatial scores)
  aggregate_results.py        # Cross-trial aggregation, tables, charts
  experiment_design.md        # Full experiment design document
```

## Prerequisites

- Python environment with `groq`, `python-dotenv` installed
- `GROQ_API_KEY` set in environment (or `.env` file in `Agent/`)
- Unity running with `AgentNetworkBridge` + `ScreenshotLoopSender` active (for full runs)
- Optional: `matplotlib` for chart generation
- Optional: `OPENAI_API_KEY` if using `--judge-provider openai`

## Quick Start

### Run all 10 scenarios (3 trials each = 30 runs)

```bash
cd Agent
python evaluation/run_experiment.py
```

### Run specific scenarios

```bash
# Only S01 and S03, 2 trials each
python evaluation/run_experiment.py --scenarios S01,S03 --trials 2

# Quick single test
python evaluation/run_experiment.py --scenarios S03 --trials 1 --skip-judge
```

### Evaluate existing results (no Phase 1 re-generation)

```bash
python evaluation/run_experiment.py --eval-only
```

### Aggregate only (just regenerate reports from existing eval_report.json files)

```bash
python evaluation/run_experiment.py --aggregate-only
```

## run_experiment.py Options

| Flag | Default | Description |
|------|---------|-------------|
| `--scenarios` | all 10 | Comma-separated IDs: `S01,S03,S07` |
| `--trials` | 3 | Repetitions per scenario |
| `--timeout` | 300 | Max seconds per Phase 1 run |
| `--clear-wait` | 5.0 | Seconds to wait after `ClearScene` for Unity |
| `--skip-judge` | off | Skip LLM-as-judge evaluation (faster) |
| `--judge-provider` | groq | LLM for judge: `groq` or `openai` |
| `--eval-only` | off | Only evaluate existing results, no generation |
| `--aggregate-only` | off | Only aggregate existing evaluation reports |

## What Happens Per Trial

1. **ClearScene** -- sends `ClearScene` command to Unity, waits for old objects to be destroyed
2. **Clear api_files** -- resets `reflection.json`, `agent.log`, `scene_state.json`
3. **Copy scenario** -- copies `experience_S{XX}.txt` to `api_files/experience.txt`
4. **Phase 1** -- runs `groq_static_test.py --phase1-only`
5. **Collect artifacts** -- copies all outputs to `results/{scenario}/trial_{N}/`
6. **Evaluate** -- runs `evaluate_scene.py`, writes `eval_report.json`
7. **LLM Judge** -- runs `llm_judge.py`, writes `llm_judge_report.json`

After all trials: runs aggregation, prints summary table, generates charts.

## Using Individual Scripts

### evaluate_scene.py

Compute all automated metrics from a single run's output:

```bash
python evaluation/evaluate_scene.py \
  --results-dir evaluation/results/S03/trial_1 \
  --experience evaluation/scenarios/experience_S03.txt \
  --output evaluation/results/S03/trial_1/eval_report.json
```

### llm_judge.py

Rate a scene via LLM (coherence 1-5, spatial 1-5):

```bash
python evaluation/llm_judge.py \
  --results-dir evaluation/results/S03/trial_1 \
  --experience evaluation/scenarios/experience_S03.txt \
  --provider groq \
  --output evaluation/results/S03/trial_1/llm_judge_report.json
```

### aggregate_results.py

Aggregate all results and generate reports:

```bash
python evaluation/aggregate_results.py \
  --results-root evaluation/results \
  --output-dir evaluation/reports
```

## Metrics

### Geometric (from scene_state.json)

| Metric | Description |
|--------|-------------|
| Collision Rate | AABB-overlapping object pairs / total pairs |
| OOB Rate | Objects with center outside room bounds / total |
| Mean NN Distance | Average nearest-neighbor distance (XZ plane) |
| Min Pairwise Distance | Closest any two objects get |
| Floor Coverage | Sum of object footprints / room floor area |
| Height Violation Rate | Small props on floor (y<0.3) that should be on surfaces |

### Semantic (automated + LLM)

| Metric | Description |
|--------|-------------|
| Prefab Hit Rate | Required objects actually placed / required objects listed |
| Prefab Name Validity | Valid prefab names / total placements |
| Duplicate Rate | Duplicate object names / total objects |
| Coherence Score (LLM) | 1-5: object selection appropriateness for scene type |
| Spatial Score (LLM) | 1-5: logical placement quality |

### Plan Completion (from reflection.json)

| Metric | Description |
|--------|-------------|
| Step Completion Rate | Steps marked complete / total plan steps |
| Avg Iterations/Step | Reflection iterations / plan steps |
| Total Corrections | MoveObject + DeleteObject from reflection |

### Efficiency (from agent.log)

| Metric | Description |
|--------|-------------|
| LLM Calls | Total Groq API calls |
| Dropped Prefabs | PlaceObject commands dropped (name not found) |
| Fill Commands | Objects added by the sparse-room fill loop |

### Composite Score (0-100)

Weighted combination: `collision(25%) + OOB(15%) + hit_rate(30%) + plan_completion(20%) + height(10%)`

## Scenarios at a Glance

| ID | Scene | Size | Objects | Tests |
|----|-------|------|---------|-------|
| S01 | Emergency Triage | 4x4m | 12 | Small space, collision avoidance |
| S02 | Hospital Ward | 8x6m | 25 | Symmetric/grid layout |
| S03 | Physical Therapy Gym | 6x6m | 30 | High object diversity (baseline) |
| S04 | Surgery Prep | 5x5m | 20 | Surface placement (y>0.7) |
| S05 | Waiting Room | 6x8m | 15 | Sparse room fill behavior |
| S06 | ICU Single-Patient | 4x5m | 18 | Character-equipment relationship |
| S07 | Supply Closet | 3x3m | 20 | Extreme density, tiny room |
| S08 | Rehab Corridor | 3x10m | 15 | Elongated linear layout |
| S09 | Doctor's Office | 5x5m | 15 | Functional zone grouping |
| S10 | Nurse Station | 8x8m | 35 | Max scale + density stress test |
