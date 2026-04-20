# Phase 1 Experiment Design: Scene Generation Quality Evaluation & Ablation Study

## Context

Phase 1 is the static environment generation module of the Firefighter/Healthcare VR training system. It uses Groq LLM (Llama 4 Scout 17B) to generate 3D scenes in Unity via a multi-stage pipeline: plan generation -> room shell -> LLM interior placement -> reflection loop (5 parallel validation threads) -> iterative correction & filling. The goal is to rigorously evaluate generation quality and understand each component's contribution.

---

## Experiment 1: 10 Test Scenarios + Quality Metrics

### 1.1 Scenario Design Principles

Each scenario varies along key dimensions to stress-test different capabilities:
- **Room size**: small (4x4m) / medium (6x6m) / large (8x10m)
- **Object density**: sparse (5-10) / moderate (15-20) / dense (25-35)
- **Scene complexity**: simple (few object types) / complex (many categories)
- **Character count**: 0 / 1-2 / 3+
- **Spatial constraints**: open floor / wall-hugging / multi-zone layout
- **Semantic specificity**: generic room / highly specialized medical setup

### 1.2 The 10 Scenarios

Each scenario = one `experience.txt` file. Run each scenario **3 times** (different seeds / temperatures) for statistical reliability.

| # | Scene Name | Room Size | max_obj | Key Challenge | Description |
|---|-----------|-----------|---------|---------------|-------------|
| **S1** | Emergency Triage Room | 4x4m | 12 | Small space, collision avoidance | Cramped triage room: 1 stretcher, 1 exam table, 1 trolley cart, 1 monitor, 1 EMT, 1 patient. Tests tight-space placement without collisions. |
| **S2** | Hospital Ward (4-bed) | 8x6m | 25 | Repetitive layout, symmetry | 4 beds evenly spaced, each with bedside table + chair. 2 nurses, 2 patients. Tests whether LLM can produce symmetric/grid-like layouts. |
| **S3** | Physical Therapy Gym | 6x6m | 30 | High object diversity | Current default scenario (experience.txt baseline). Treadmill, parallel bars, wheelchair, trolley, desk, posters, etc. Tests broad prefab coverage. |
| **S4** | Surgery Prep Room | 5x5m | 20 | Precise surface placement | Surgery cart, instrument trays, autoclave, sterile supplies ON surfaces (y>0.7). Tests surface placement logic and prop stacking. |
| **S5** | Empty Waiting Room | 6x8m | 15 | Sparse room, filling behavior | Large room with only chairs, a reception desk, and magazines. Tests LLM fill loop: does it add reasonable objects without over-filling? |
| **S6** | ICU Single-Patient Room | 4x5m | 18 | Character-centric, equipment-dense | 1 bed, 1 patient, 1 nurse, monitoring equipment, IV stand, ventilator cart. Tests character-equipment spatial relationship. |
| **S7** | Medical Supply Closet | 3x3m | 20 | Extreme density, tiny room | Shelves, cabinets, boxes, bottles packed into a tiny closet. Tests collision detection under high density and small bounds. |
| **S8** | Rehabilitation Corridor | 3x10m | 15 | Elongated room, linear layout | Long narrow space with handrails along walls, wheelchair, walker. Tests placement along a single dominant axis. |
| **S9** | Doctor's Office | 5x5m | 15 | Functional zones (desk + exam) | Desk zone (computer, folders, chair) + exam zone (exam table, stool, equipment). Tests whether LLM creates logical spatial groupings. |
| **S10** | Nurse Station (Open) | 8x8m | 35 | Maximum scale + density | Large open nurse station: multiple desks, monitors, chairs, cabinets, 3 nurses, supply carts. Stress test at max capacity. |

### 1.3 Quality Metrics

#### A. Geometric Metrics (automated, computed from `scene_state.json`)

| Metric | Formula | What It Measures |
|--------|---------|------------------|
| **Collision Rate** | `# AABB-overlapping object pairs / total object pairs` | Physical plausibility: objects shouldn't intersect |
| **OOB Rate** | `# objects with center outside [x_min,x_max,z_min,z_max] / total objects` | Bounds compliance |
| **Mean Nearest-Neighbor Distance** | `avg(min distance to any other object) for each object` | Spatial distribution evenness |
| **Floor Coverage** | `union of object footprints / room floor area` | How well the room is utilized |
| **Height Violation Rate** | `# small props at y<0.3 (should be on surfaces) / total small props` | Surface placement correctness |
| **Min Pairwise Distance** | `min distance between any two object centers` | Worst-case crowding |

#### B. Semantic Metrics (automated + LLM-as-judge)

| Metric | Method | What It Measures |
|--------|--------|------------------|
| **Prefab Hit Rate** | `# successfully placed required objects / # required objects in description` | Does it follow the scene specification? |
| **Prefab Name Validity** | `# valid prefab names / total attempted placements` | Name resolution accuracy |
| **Scene-Type Coherence** (LLM-judge) | GPT-4 rates 1-5: "Given this is a [scene_type], rate how appropriate the object set is" | Semantic fit of objects to context |
| **Spatial Semantics** (LLM-judge) | GPT-4 rates 1-5: "Are objects placed in logical locations?" (e.g., clock on wall not floor) | Logical placement quality |
| **Duplicate Object Rate** | `# duplicate objectNames / total objects` | Unnecessary repetition |

#### C. Plan Completion Metrics (from `reflection.json` + logs)

| Metric | Source | What It Measures |
|--------|--------|------------------|
| **Step Completion Rate** | `# steps with step_complete="yes" / total plan steps` | Plan fulfillment |
| **Avg Iterations per Step** | `total reflection iterations / total steps` | Efficiency of convergence |
| **Correction Count** | `# MoveObject + DeleteObject commands from reflection` | How much fixing was needed |
| **Fill Commands Count** | `# PlaceObject commands added in fill loops` | Sparse-room handling |

#### D. Efficiency Metrics (from logs + timing)

| Metric | Source | What It Measures |
|--------|--------|------------------|
| **Total LLM Calls** | Count API calls in agent.log | Cost proxy |
| **Total Tokens** | Sum input+output tokens from Groq responses | Actual cost |
| **Wall-Clock Time** | Phase 1 start to end timestamp | End-to-end speed |
| **Screenshot Wait Time** | Time spent polling for Unity screenshots | Pipeline bottleneck |

### 1.4 Evaluation Protocol

```
For each scenario S_i (i = 1..10):
    For each trial t (t = 1..3):
        1. Write S_i's experience.txt
        2. Run: python Agent/groq_static_test.py --phase1-only
        3. Collect: scene_state.json, reflection.json, agent.log, unity_commands.json
        4. Compute geometric metrics from scene_state.json
        5. Compute semantic metrics (automated + LLM-judge)
        6. Record efficiency metrics from logs
    Aggregate: mean +/- std across 3 trials per scenario
Report: table of all metrics x 10 scenarios, plus overall averages
```

---

## Experiment 2: Ablation Study

### 2.1 Design Philosophy

Systematically remove or degrade **one component at a time** from the full pipeline to measure each component's marginal contribution. Use **S3 (Physical Therapy Gym)** and **S7 (Medical Supply Closet)** as the two test scenarios (medium complexity + high-density stress test). Run each condition **3 times**.

### 2.2 Ablation Conditions

| ID | Condition | What's Changed | Hypothesis |
|----|-----------|---------------|------------|
| **A0** | **Full Pipeline (baseline)** | Nothing removed | Best overall quality |
| **A1** | No Reflection Loop | Skip entire reflection + correction loop; use only initial LLM generation | Reflection is critical for collision reduction and plan completion |
| **A2** | No Screenshots (coordinate-only reflection) | Reflection runs but without screenshot images; LLM reasons from scene_state.json coordinates only | Visual grounding improves spatial reasoning significantly |
| **A3** | No Fuzzy Name Matching | Disable `_fix_prefab_names()`; require exact LLM-output names | Fuzzy matching rescues many otherwise-failed placements |
| **A4** | No Position Clamping | Disable `_clamp_positions()`; allow out-of-bounds objects | Clamping prevents a significant fraction of OOB violations |
| **A5** | No LLM Fill Loop | Reflection runs corrections but never adds new objects in sparse rooms | Fill loop is essential for reaching min_objects in sparse descriptions |
| **A6** | No Direct Corrections | Skip `_parse_alignment_commands()`; alignment_suggestions are ignored (only LLM fill runs) | Direct corrections are more precise than relying on LLM to self-correct |
| **A7** | Single Reflection Iteration | `phase1_max_iters=1` (one pass per plan step) | Diminishing returns after first iteration, or not? |
| **A8** | No Plan Structure | Replace structured plan.json with a single "place all objects" step | Structured plan guides LLM to better coverage and organization |
| **A9** | High Temperature (0.5/0.6) | Change initial temp from 0.1->0.5, loop temp from 0.2->0.6 | Low temperature is important for deterministic, reliable placement |

### 2.3 Metrics for Ablation

Use the **same metric set** from Experiment 1, focusing on:

**Primary metrics** (most sensitive to ablation):
1. **Collision Rate** - geometric quality
2. **OOB Rate** - bounds compliance
3. **Prefab Hit Rate** - specification adherence
4. **Step Completion Rate** - plan fulfillment
5. **Total LLM Calls** - cost efficiency

**Secondary metrics** (reported but not primary):
- Mean Nearest-Neighbor Distance
- Floor Coverage
- Scene-Type Coherence (LLM-judge)
- Wall-Clock Time

### 2.4 Analysis Plan

1. **Bar chart**: Each ablation condition vs. baseline on primary metrics (5 grouped bars)
2. **Delta table**: `metric(A_i) - metric(A0)` for each metric x condition, with significance (paired t-test across 3 trials x 2 scenarios = 6 data points)
3. **Radar/spider chart**: Overlay all conditions on a single radar chart (5 axes = 5 primary metrics) to visualize trade-offs
4. **Component importance ranking**: Sort components by average metric degradation when removed
5. **Cost-quality Pareto curve**: Plot (total LLM calls) vs. (average quality score) for each condition to identify the best quality/cost trade-off

### 2.5 Ablation Protocol

```
For each condition A_i (i = 0..9):
    For each scenario S in {S3, S7}:
        For each trial t (t = 1..3):
            1. Apply ablation modification to groq_static_test.py (via CLI flags)
            2. Write scenario S's experience.txt
            3. Run: python Agent/groq_static_test.py --phase1-only
            4. Collect all outputs
            5. Compute all metrics
        Aggregate: mean +/- std across 3 trials
    Report per-scenario and averaged results
Total runs: 10 conditions x 2 scenarios x 3 trials = 60 runs
```

---

## Implementation Plan

### Step 1: Create evaluation infrastructure
- **File**: `Agent/evaluation/evaluate_scene.py`
  - Load `scene_state.json`, compute all geometric metrics
  - Load `reflection.json` + `agent.log`, compute plan/efficiency metrics
  - Output: JSON report per run
- **File**: `Agent/evaluation/llm_judge.py`
  - Call GPT-4 for semantic coherence + spatial semantics scores
- **File**: `Agent/evaluation/aggregate_results.py`
  - Aggregate across trials, generate tables + charts

### Step 2: Create 10 scenario files
- **Directory**: `Agent/evaluation/scenarios/`
- 10 `experience_S{i}.txt` files with prose + `[room_config]` sections

### Step 3: Create ablation runner
- **File**: `Agent/evaluation/run_ablation.py`
  - Accepts `--condition A0..A9 --scenario S3|S7 --trial 1..3`
  - Applies the appropriate code modification (via config flags, not code edits)
  - Runs the pipeline and collects outputs to `Agent/evaluation/results/{condition}/{scenario}/{trial}/`

### Step 4: Add ablation flags to `groq_static_test.py`
- `--no-reflection` (A1)
- `--no-screenshots` (A2)
- `--no-fuzzy-names` (A3)
- `--no-clamping` (A4)
- `--no-fill` (A5)
- `--no-direct-corrections` (A6)
- `--temperature-initial 0.5 --temperature-loop 0.6` (A9)
- Already has: `--phase1-max-iters` (A7), plan structure can be overridden (A8)

### Step 5: Run experiments and generate report
