# Evaluation Harness

Automated experiment harness for the VR healthcare scene-generation pipeline
(`Agent/groq_static_test.py`). It runs a set of healthcare scenarios through the
pipeline several times each, scores every generated scene with automated metrics
and an LLM judge, and aggregates the results into tables and charts.

- **Phase 1** = static environment generation (objects placed in the Unity room).
- **Phase 2** = interaction layer: an *interaction map* (per-object ambient
  responses such as animations and speech bubbles) and a *story chain* (scripted
  beats triggered by the player).

The study design and results write-up are in
[`experiment_design.md`](experiment_design.md) and
[`phase1_experiment_writeup.md`](phase1_experiment_writeup.md).

## Contents

```
evaluation/
  scenarios/               15 scenario descriptions: experience_S01.txt ... experience_S15.txt
  run_experiment.py        Orchestrator: generate -> collect -> evaluate -> judge -> aggregate
  evaluate_scene.py        Phase 1 automated metrics (geometric, semantic, plan, efficiency)
  evaluate_phase2.py       Phase 2 automated metrics (coverage, validity, chain, runtime)
  llm_judge.py             LLM-as-judge ratings for Phase 1 and Phase 2 (1-5 scales)
  aggregate_results.py     Mean/std across trials, summary table, CSV, charts
  run_experiment.bat       Windows launcher for run_experiment.py (edit settings inside)
  replay_trial.bat         Windows launcher to replay a saved trial and play Phase 2 in Unity
  experiment_design.md     Experiment design document
  phase1_experiment_writeup.md
```

Generated at run time:

```
evaluation/
  results/                 (default; change with --results-dir)
    S01/trial_1/           Per-trial artifacts and reports (see "Outputs" below)
    S01/trial_2/
    ...
  reports/                 Aggregated outputs
    summary.json
    summary.csv
    summary_table.txt
    charts/                Bar charts + radar chart (matplotlib)
```

## Prerequisites

- Python 3 with the packages in [`Agent/requirements.txt`](../requirements.txt)
  (`groq`, `openai`, `python-dotenv`; `matplotlib` and `numpy` for charts).
- API keys in the environment or in `Agent/.env`:
  - `GROQ_API_KEY` (pipeline and the default `groq` judge)
  - `OPENAI_API_KEY` (frontier model in the pipeline, and `--judge-provider openai`)
- For generation runs: the Unity project open in the Editor, in Play mode, with
  `AgentNetworkBridge` and `ScreenshotLoopSender` active. The harness talks to
  Unity through `firefighter project/Assets/Scripts/unity_commands.json` and
  `Agent/api_files/`. Unity is **not** needed for `--eval-only` or
  `--aggregate-only`.

## Reproducing the experiment

All commands are run from the `Agent/` directory.

```bash
cd Agent

# Full run: all 15 scenarios x 3 trials, Groq judge
python evaluation/run_experiment.py

# Subset of scenarios / trials
python evaluation/run_experiment.py --scenarios S01,S03 --trials 2

# Quick smoke test without the LLM judge
python evaluation/run_experiment.py --scenarios S03 --trials 1 --skip-judge

# Choose the pipeline's frontier model and use an OpenAI judge
python evaluation/run_experiment.py --frontier-model gpt-5.4-mini \
    --judge-provider openai --judge-model gpt-5.4-mini --timeout 300

# Re-score existing results without regenerating (no Unity needed)
python evaluation/run_experiment.py --eval-only

# Rebuild reports from existing per-trial reports
python evaluation/run_experiment.py --aggregate-only
```

On Windows you can instead edit the settings block at the top of
`run_experiment.bat` and double-click it; it builds the same command line.

### What happens per trial

1. **Clear scene**: write a `ClearScene` command for Unity and wait for
   confirmation (`--clear-wait` x `--clear-retries` seconds, then `--stabilize`).
   After 3 consecutive unconfirmed clears the run aborts and aggregates what it has.
2. **Reset `api_files/`**: `reflection.json`, `actions.json`, `agent.log`,
   `reflection_log.log`, `scene_state.json`.
3. **Generate**: copy `scenarios/experience_SXX.txt` to `api_files/experience.txt`
   and run `groq_static_test.py --phase1-only` (or, with `--include-phase2`,
   without `--phase1-only` so the interactive Phase 2 loop runs too).
4. **Collect**: wait for fresh Unity screenshots, then copy artifacts into
   `results/SXX/trial_N/`.
5. **Evaluate**: `evaluate_scene.py` -> `eval_report.json`;
   `evaluate_phase2.py` -> `phase2_eval_report.json` (if Phase 2 artifacts exist).
6. **Judge**: `llm_judge.py` -> `llm_judge_report.json` (unless `--skip-judge`).

After all trials, results are aggregated into `evaluation/reports/`.

Before the first trial, a `DebugLog` probe checks that Unity is responding;
if not, the run stops (skipped with `--eval-only`).

### Outputs

Per trial (`<results-dir>/SXX/trial_N/`):

| File | Source |
|------|--------|
| `scene_state.json` | Objects present in Unity after generation (positions, bounds) |
| `unity_commands.json` | Commands sent to Unity |
| `reflection.json`, `plan.json`, `agent.log` | Pipeline planning/reflection records and log |
| `interaction_map.json`, `story_chain.json` | Phase 2 artifacts |
| `experience.txt` | Experience file as used by the pipeline |
| `scene_screenshot_{xz,xy,yz}.png` | Orthographic screenshots of the final scene |
| `eval_report.json` | `evaluate_scene.py` |
| `phase2_eval_report.json` | `evaluate_phase2.py` |
| `llm_judge_report.json` | `llm_judge.py` |

Aggregated (`evaluation/reports/`; when run via `run_experiment.py` this location
is fixed even if `--results-dir` is changed):

| File | Content |
|------|---------|
| `summary.json` | Per-scenario `<metric>_mean` / `<metric>_std` for every aggregated metric |
| `summary.csv` | Same data, one row per scenario |
| `summary_table.txt` | Fixed-width table of key metrics plus an AVERAGE row |
| `charts/*.png` | Bar charts (composite, collision rate, hit rate, plan completion) and a radar chart |

## Script reference

### `run_experiment.py`

| Flag | Default | Description |
|------|---------|-------------|
| `--scenarios` | all (S01-S15) | Comma-separated IDs, e.g. `S01,S03,S07` |
| `--trials` | 3 | Trials per scenario |
| `--timeout` | 300 | Max seconds per pipeline run |
| `--clear-wait` | 8.0 | Seconds per ClearScene polling attempt |
| `--clear-retries` | 3 | ClearScene polling attempts (total wait = wait x retries) |
| `--stabilize` | 3.0 | Seconds to wait after ClearScene is confirmed |
| `--skip-judge` | off | Skip the LLM judge |
| `--judge-provider` | `groq` | Judge provider: `groq` or `openai` |
| `--judge-model` | provider default | Judge model override (e.g. `gpt-5.5`) |
| `--frontier-model` | pipeline default (`gpt-5.5`) | Forwarded to `groq_static_test.py --frontier-model`; `''` disables the frontier model |
| `--include-phase2` | off | Also run the interactive Phase 2 loop (requires player input in Unity) |
| `--phase2-max-iterations` | 0 (no cap) | Phase 2 loop iteration cap; only with `--include-phase2` |
| `--results-dir` | `evaluation/results` | Where per-trial results are written/read |
| `--eval-only` | off | Evaluate existing results only (no generation, no Unity) |
| `--aggregate-only` | off | Only aggregate existing reports |
| `--abort-no-unity` | off | Accepted but currently has no effect |

### `evaluate_scene.py` (Phase 1 metrics)

```bash
python evaluation/evaluate_scene.py \
    --results-dir evaluation/results/S03/trial_1 \
    --experience evaluation/scenarios/experience_S03.txt \
    --output evaluation/results/S03/trial_1/eval_report.json
```

| Flag | Required | Description |
|------|----------|-------------|
| `--results-dir` | yes | Trial directory with `scene_state.json`, `reflection.json`, `agent.log`, `unity_commands.json` |
| `--experience` | no | Experience file; its `[room_config]` section supplies room bounds and `scene_type` |
| `--output` | no | Write the JSON report here (it is always printed) |

### `evaluate_phase2.py` (Phase 2 metrics)

```bash
python evaluation/evaluate_phase2.py \
    --results-dir evaluation/results/S03/trial_1 \
    --experience evaluation/scenarios/experience_S03.txt \
    --output evaluation/results/S03/trial_1/phase2_eval_report.json
```

Same flags as `evaluate_scene.py`; reads `interaction_map.json`,
`story_chain.json`, `scene_state.json` (and `agent.log` /
`unity_commands.json` for runtime metrics).

### `llm_judge.py`

```bash
python evaluation/llm_judge.py \
    --results-dir evaluation/results/S03/trial_1 \
    --experience evaluation/scenarios/experience_S03.txt \
    --provider groq \
    --output evaluation/results/S03/trial_1/llm_judge_report.json
```

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--results-dir` | yes | | Trial directory |
| `--experience` | yes | | Experience file used for the run |
| `--provider` | no | `groq` | `groq` (`meta-llama/llama-4-scout-17b-16e-instruct`) or `openai` (`gpt-5.5`) |
| `--model` | no | provider default | Model override |
| `--output` | no | | Write the JSON ratings here |

### `aggregate_results.py`

```bash
python evaluation/aggregate_results.py \
    --results-root evaluation/results \
    --output-dir evaluation/reports
```

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--results-root` | yes | | Directory laid out as `<root>/<scenario>/<trial>/` |
| `--output-dir` | no | `<results-root>/../reports` | Where summaries and charts are written |

Trials without an `eval_report.json` are skipped.

### `replay_trial.bat` (Windows)

Rebuilds a saved trial's scene in Unity (`groq_static_test.py --replay-trial`)
and enters the interactive Phase 2 loop so you can play it in the XR Device
Simulator. Edit `TRIAL_DIR` (relative to `Agent/`) inside the file, make sure
Unity is in Play mode, run it, and press Ctrl+C when done; Phase 2 runtime
metrics are then written to the trial's `phase2_eval_report.json`.

## Metrics

### Phase 1: geometric (`scene_state.json`)

Structural `Cube` objects (walls, floor, ceiling) are excluded.

| Metric | Description |
|--------|-------------|
| `collision_rate` | Object pairs with overlapping AABBs / all pairs |
| `oob_rate` | Objects whose center is outside the room bounds / all objects |
| `mean_nn_distance` | Mean nearest-neighbor distance on the XZ plane |
| `min_pairwise_distance` | Smallest XZ distance between any two objects |
| `floor_coverage` | Sum of object XZ footprints / room floor area (capped at 1.0) |
| `height_violation_rate` | Small props (keyword match) with y < 0.3 / all small props |

### Phase 1: semantic (automated)

| Metric | Description |
|--------|-------------|
| `prefab_hit_rate` | Objects listed under "REQUIRED OBJECTS" that were placed / listed (1.0 if none are listed) |
| `prefab_name_validity` | Placements using a valid prefab name / all placements (only when a prefab list is supplied) |
| `duplicate_rate` | Duplicate object names / all objects |
| `character_count` | Placed objects whose name starts with a character prefix (doctor, nurse, patient, ...) |

### Phase 1: plan completion (`reflection.json`)

| Metric | Description |
|--------|-------------|
| `step_completion_rate` | Plan steps with a reflection marked `step_complete: "yes"` / all steps |
| `avg_iterations_per_step` | Reflection entries / plan steps |
| `total_corrections` | `MoveObject` + `DeleteObject` mentions in reflection suggestions |

### Phase 1: efficiency (`agent.log`, `unity_commands.json`)

| Metric | Description |
|--------|-------------|
| `llm_calls` | Groq responses logged (`GROQ RAW RESPONSE`) |
| `dropped_prefabs` | Placement commands dropped because the prefab was not found |
| `fill_commands_total` | Objects added by the sparse-room fill loop |
| `phase1_duration_seconds` | Wall-clock Phase 1 duration from log timestamps |
| `total_commands`, `place_object_count`, ... | Command counts by action type |

### Phase 1 composite score (0-100)

```
composite = 100 * ( 0.25 * max(0, 1 - 10 * collision_rate)
                  + 0.15 * max(0, 1 - 5 * oob_rate)
                  + 0.30 * prefab_hit_rate
                  + 0.20 * step_completion_rate
                  + 0.10 * (1 - height_violation_rate) )
```

### Phase 2 (`interaction_map.json`, `story_chain.json`)

| Group | Metrics |
|-------|---------|
| Coverage | `ambient_object_coverage` (placed objects with a map entry), `multi_event_pct`, `bubble_density`, `targeted_animation_pct` |
| Validity | `map_controller_validity_rate`, `map_state_prefix_validity`, `map_target_validity_rate`, `map_bubble_on_validity` (references resolve to real controllers / placed objects) |
| Chain | `chain_length`, `chain_beat_validity_rate`, `chain_trigger_validity_rate`, `chain_action_richness`, `chain_unique_action_types`, `chain_walk_coord_validity`, `chain_state_prefix_validity`, `chain_char_coverage` |
| Runtime | Only populated when Phase 2 actually ran: `phase2_ran`, `chain_beats_fired`, `chain_progression`, `time_to_first_beat_seconds`, `command_execution_rate`, `unmapped_event_rate`, ... |

Phase 2 composite (0-100) = 30% coverage + 30% validity + 40% chain quality
(each a weighted mean of its group's metrics; see `compute_composite` in
`evaluate_phase2.py`). Runtime metrics are not part of the composite.

### LLM judge (1-5 each)

| Phase | Ratings |
|-------|---------|
| Phase 1 | `coherence_score` (object selection fits the scene type), `spatial_score` (logical placement) |
| Phase 2 | `bubble_informativeness`, `narrative_coherence`, `character_voice_consistency`, `bubble_object_fit` |

## Scenarios

| ID | Scenario |
|----|----------|
| S01 | Emergency Triage Room |
| S02 | Hospital Ward (4-Bed) |
| S03 | Physical Therapy Gym |
| S04 | Surgery Prep Room |
| S05 | Dental Office |
| S06 | ICU Single-Patient Room |
| S07 | Medical Supply Closet |
| S08 | Rehabilitation Corridor |
| S09 | Doctor's Office |
| S10 | Nurse Station (Open) |
| S11 | Radiology Imaging Suite |
| S12 | Maternity / Neonatal Ward |
| S13 | Ophthalmology Exam Room |
| S14 | Clinical Laboratory |
| S15 | Cosmetic Treatment Room |

Each file starts with a one-line title and free-text description, followed by an
`[interactions]` section describing the desired Phase 2 behavior.
