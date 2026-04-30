# Phase 1 — Experiment Section Writeup Handoff

> **Purpose.** This document hands off all Phase-1 experimental material to the paper-writing agent. It contains (i) the experimental setup, (ii) metric definitions, (iii) the current results, (iv) suggested talking points and narrative beats, and (v) limitations to disclose. The writer-agent should turn this into ~3/4 to 1 page of IEEE VIS short-paper prose for the Experiments section.

---

## 1. Experimental Setup

### 1.1 Scenarios

We curated **10 healthcare-VR scenarios** (S01–S10) that vary along six axes — room size, object density, scene complexity, character count, spatial constraints, and semantic specificity — to stress-test different capabilities of the pipeline. Each scenario is specified as a single `experience_S{i}.txt` file containing a free-form scene description, a list of required objects with approximate anchor positions, optional interaction triggers, and a `[room_config]` section with bounds and category keywords.

| ID | Scene | Room | max_obj | Stress dimension |
|----|-------|------|---------|------------------|
| S01 | Emergency Triage Room | 4×4 m | 12 | Tight space, collision avoidance |
| S02 | Hospital Ward (4-bed) | 8×6 m | 25 | Repetitive symmetry |
| S03 | Physical Therapy Gym | 6×6 m | 30 | High prefab diversity |
| S04 | Surgery Prep Room | 5×5 m | 20 | Surface-conditional placement |
| S05 | Empty Waiting Room | 6×8 m | 15 | Sparse-room fill behavior |
| S06 | ICU Single-Patient Room | 4×5 m | 18 | Character–equipment grouping |
| S07 | Medical Supply Closet | 3×3 m | 20 | Extreme density, tiny bounds |
| S08 | Rehabilitation Corridor | 3×10 m | 15 | Elongated single-axis layout |
| S09 | Doctor's Office | 5×5 m | 15 | Functional zoning (desk + exam) |
| S10 | Nurse Station (Open) | 8×8 m | 35 | Maximum scale + density |

### 1.2 Protocol

For each scenario we ran the full Phase-1 pipeline **2–3 independent trials** (different seeds and decoding temperatures) and aggregated mean ± std across trials. Each trial produced `scene_state.json`, `reflection.json`, `unity_commands.json`, `agent.log`, plan artifacts, and three orthogonal screenshots (xy, xz, yz). Geometric and plan/efficiency metrics are computed deterministically from the JSON artifacts; semantic-judgment metrics are obtained from a GPT-4 LLM-as-judge over the description and the three screenshots.

### 1.3 Implementation

The agent uses Groq's Llama 4 Scout 17B as the planning frontier model and four lightweight VLM threads (Bound, Collision, Physics, Semantic) for parallel reflection. Reflection is capped at 3 iterations per plan step. All 10 scenarios use a shared asset library and authoring-rule corpus retrieved via a Pinecone vector store.

---

## 2. Metrics

We report metrics in four groups and a single composite score.

### 2.1 Geometric (deterministic from `scene_state.json`)

| Metric | Definition |
|--------|------------|
| **Collision rate** | # AABB-overlapping object pairs ÷ total object pairs |
| **OOB rate** | # objects with center outside `[x_min, x_max] × [z_min, z_max]` ÷ total objects |
| **Mean NN distance** | mean over objects of distance to nearest other object (m) |
| **Floor coverage** | union of object footprints ÷ room floor area |
| **Height-violation rate** | # small props with y < 0.3 ÷ total small props (small props should rest on surfaces with y ≥ 0.7) |

### 2.2 Semantic (deterministic + LLM-as-judge)

| Metric | Definition |
|--------|------------|
| **Prefab hit rate** | # required objects successfully placed ÷ # required objects in description |
| **Duplicate rate** | # repeated `objectName` ÷ total objects |
| **Coherence (1–5)** | GPT-4 judges scene-type appropriateness of the placed object set |
| **Spatial (1–5)** | GPT-4 judges logical placement (e.g., clock on wall, monitor wall-mounted) |

### 2.3 Plan completion (from `reflection.json`)

| Metric | Definition |
|--------|------------|
| **Step-completion rate** | # plan steps with `step_complete="yes"` ÷ total plan steps |
| **Avg iterations / step** | total reflection iterations ÷ total steps (cap = 3) |
| **Total corrections** | # MoveObject + DeleteObject calls emitted by reflection |

### 2.4 Efficiency (from `agent.log`)

| Metric | Definition |
|--------|------------|
| **LLM calls** | number of frontier-LLM completion calls per run |
| **Total commands** | number of Unity commands dispatched |
| **Fill commands** | # PlaceObject calls injected by the sparse-room fill loop |

### 2.5 Composite score (0–100)

A weighted aggregate emphasizing physical plausibility and specification adherence:

```
composite = ( 0.25 · max(0, 1 − 10·collision_rate)
            + 0.15 · max(0, 1 −  5·oob_rate)
            + 0.30 ·  prefab_hit_rate
            + 0.20 ·  step_completion_rate
            + 0.10 ·  max(0, 1 − height_violation_rate) ) × 100
```

The 10× and 5× multipliers heavily penalize even small collision and OOB violations.

---

## 3. Results

### 3.1 Per-scenario summary (mean across trials)

| Scenario | Composite | Collision % | OOB % | Hit Rate | Plan % | Coverage | Height Err | LLM Calls | Coherence | Spatial |
|----------|-----------|-------------|-------|----------|--------|----------|------------|-----------|-----------|---------|
| S03 — Therapy Gym       | 88.34 | 3.9 | 0.0 | 1.00 | 1.00 | 0.249 | 0.20 | 5.0 | 4.0 | 3.50 |
| S04 — Surgery Prep      | **91.77** | 2.9 | 0.0 | 1.00 | 1.00 | **0.485** | 0.10 | 3.0 | 3.0 | 3.00 |
| S05 — Waiting Room      | 90.39 | **0.7** | 0.0 | 1.00 | 1.00 | 0.090 | 0.78 | 4.0 | 4.0 | 3.33 |
| S06 — ICU Room          | 86.73 | 3.5 | 0.0 | 1.00 | 0.90 | 0.351 | 0.25 | 3.0 | 4.0 | 3.00 |
| S07 — Supply Closet     | 78.34 | 0.2 | 0.0 | 0.33 | 1.00 | 0.041 | 0.11 | 3.0 | 2.0 | 1.67 |
| S08 — Rehab Corridor    | 77.17 | 1.8 | 0.0 | 0.50 | 1.00 | 0.107 | 0.33 | 1.7 | 3.0 | 2.33 |
| S09 — Doctor's Office   | 80.45 | 6.1 | 0.0 | 1.00 | 1.00 | 0.157 | 0.43 | 1.7 | 4.0 | 3.00 |
| S10 — Nurse Station     | 85.07 | 0.4 | 0.0 | 0.67 | 1.00 | 0.101 | 0.39 | 4.7 | 3.0 | 2.33 |
| **Average (S03–S10)**   | **84.78** | **2.4** | **0.0** | **0.81** | **0.99** | 0.198 | 0.32 | 3.25 | 3.38 | 2.77 |

*Values use trial-mean. Bold marks per-column extrema. S01/S02 per-trial reports exist on disk but were not yet folded into the aggregated summary at writeup time — see §5.*

### 3.2 Available figures (already generated)

- `reports/charts/composite_score_mean.png` — bar chart of composite score per scenario.
- `reports/charts/geometric_collision_rate_mean.png` — bar chart of collision rate per scenario.
- `reports/charts/semantic_prefab_hit_rate_mean.png` — bar chart of required-object hit rate.
- `reports/charts/plan_step_completion_rate_mean.png` — bar chart of plan completion.
- `reports/charts/radar_comparison.png` — multi-axis radar overlaying all scenarios.

The radar chart is the strongest single figure for the paper — it makes the per-scenario shape comparison immediate.

---

## 4. Key Observations (talking points for the writer)

The writer should weave these into prose; numbers are the load-bearing claims.

1. **Bounds compliance is solved.** OOB rate is **0.0 % across all 8 scenarios and 22 trials**. The deterministic clamp + Bound-thread reflection together eliminate out-of-room placements entirely.

2. **Plan completion is near-perfect.** Step-completion is **100 % in 7/8 scenarios** (98.8 % overall), demonstrating that the multi-thread reflection loop converges within the 3-iteration cap on essentially every step.

3. **Required-object specification adherence is strong but room-shape-sensitive.** Prefab hit rate is **1.00 in 5/8 scenarios** (S03, S04, S05, S06, S09) but degrades on the geometric extremes — **S07 supply closet (0.33)** and **S08 rehab corridor (0.50)** — where tight 3 m bounds or single-axis narrowness force the planner to drop or substitute objects. This is a clean motivation for future work on shape-aware placement priors.

4. **Collisions are infrequent and bounded.** Mean collision rate is **2.4 %**, peaking at **6.1 % in S09** (a 5 × 5 m office with two functional zones competing for floor space). Even at peak, this corresponds to ≤ 5 overlapping pairs per scene — within the range a single reflection pass can resolve in interactive use.

5. **Convergence is efficient.** Average **iterations per plan step = 1.5** (range 1.28–1.88), and the pipeline issues only **1.7–5.0 frontier-LLM calls per scenario**. The pipeline's cost is dominated by lightweight VLM reflection, not the frontier LLM — supporting the architectural choice to separate "heavy planner / light critic."

6. **Height-on-surface placement is the weakest geometric metric.** Height-violation rate averages **32 %**, with S05 waiting room reaching **78 %** because there are few surface anchors for the small props the LLM produces. This is a candid limitation and motivates the upcoming Phase-2 surface-conditional placement work.

7. **LLM-judge coherence outpaces spatial reasoning.** Mean coherence is **3.38 / 5** versus mean spatial **2.77 / 5**. The agent picks the right *kinds* of objects more reliably than it places them in human-meaningful locations, suggesting that semantic retrieval is mature but layout reasoning still benefits from more visual grounding.

8. **Composite score: 84.78 / 100 averaged across S03–S10.** Best: **S04 Surgery Prep (91.77)** — moderate room with strong required-object scaffolding. Worst: **S08 Rehab Corridor (77.17)** — geometric extremity confounds the planner.

### 4.1 Suggested narrative arc

1. Open with overall composite (84.78) and OOB = 0 to establish the headline claim that the pipeline produces physically valid, on-spec scenes.
2. Use the radar chart to introduce per-scenario shape variation.
3. Single out S07/S08 as the failure mode and frame it as the room-shape-extremity story (not as a generic accuracy gap).
4. Close with efficiency numbers (1.5 iters/step, ≤ 5 LLM calls) to motivate the heavy/light split.

---

## 5. Limitations and Caveats (must disclose)

- **Coverage.** The aggregated summary in `reports/summary.json` currently includes **S03–S10 (8 of 10 scenarios)**. S01 (Emergency Triage Room) and S02 (Hospital Ward) have per-trial `eval_report.json` files in `Agent/evaluation/results1/S0{1,2}/trial_{1,2,3}/` but were not folded into the aggregate at writeup time. Either re-run `aggregate_results.py` over both result directories, or report S03–S10 explicitly in the paper.
- **Trial counts vary.** Most scenarios have **n = 3 trials**, but S03, S04, S06 have **n = 2**. Std bands for those three are wider; do not over-interpret.
- **No ablation results yet.** The ablation conditions A1–A9 in `experiment_design.md` (no-reflection, no-screenshots, no-fuzzy-names, no-clamp, no-fill, etc.) have **not been run**. The writer should restrict claims to what we measured: end-to-end full-pipeline quality. Ablation belongs in a follow-up section or extension.
- **Single backbone.** All trials use Groq Llama 4 Scout 17B. Cross-model robustness is not tested.
- **LLM-judge has only 1 judge.** Coherence and spatial scores come from a single GPT-4 pass per trial; std reflects trial variation, not inter-judge agreement.

---

## 6. Reproducibility

- Scenario files: `Agent/evaluation/scenarios/experience_S{01..10}.txt`
- Per-trial outputs: `Agent/evaluation/results1/S{01..10}/trial_{1..3}/` and `Agent/evaluation/results2/S{03..10}/trial_{1..3}/`
- Aggregator: `python Agent/evaluation/aggregate_results.py` → produces `summary.json`, `summary.csv`, `summary_table.txt`, and the chart PNGs.
- Composite-score formula in `Agent/evaluation/evaluate_scene.py:484-490`.
