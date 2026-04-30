"""
Aggregate evaluation results across scenarios and trials.

Reads per-run JSON reports from evaluate_scene.py + llm_judge.py,
computes mean/std across trials, generates summary tables and charts.

Usage:
    python aggregate_results.py --results-root Agent/evaluation/results --output-dir Agent/evaluation/reports
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return None
    return json.loads(text)


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def collect_reports(results_root: Path) -> Dict[str, List[Dict]]:
    """
    Collect evaluation reports grouped by scenario.

    Expected directory structure:
        results_root/
            S01/
                trial_1/
                    eval_report.json
                    llm_judge_report.json
                trial_2/
                    ...
            S02/
                ...
    """
    grouped: Dict[str, List[Dict]] = defaultdict(list)

    for scenario_dir in sorted(results_root.iterdir()):
        if not scenario_dir.is_dir():
            continue
        scenario_name = scenario_dir.name

        for trial_dir in sorted(scenario_dir.iterdir()):
            if not trial_dir.is_dir():
                continue

            eval_report = _load_json(trial_dir / "eval_report.json")
            judge_report = _load_json(trial_dir / "llm_judge_report.json")
            phase2_report = _load_json(trial_dir / "phase2_eval_report.json")

            if eval_report:
                combined = dict(eval_report)
                if judge_report:
                    combined["llm_judge"] = judge_report
                if phase2_report:
                    combined["phase2"] = phase2_report
                grouped[scenario_name].append(combined)

    return grouped


def aggregate_scenario(reports: List[Dict]) -> Dict[str, Any]:
    """Aggregate metrics across trials for a single scenario."""
    if not reports:
        return {}

    # Collect all metric keys
    metric_paths = [
        ("geometric", "collision_rate"),
        ("geometric", "oob_rate"),
        ("geometric", "mean_nn_distance"),
        ("geometric", "min_pairwise_distance"),
        ("geometric", "floor_coverage"),
        ("geometric", "height_violation_rate"),
        ("geometric", "object_count"),
        ("geometric", "collision_count"),
        ("semantic", "prefab_hit_rate"),
        ("semantic", "prefab_name_validity"),
        ("semantic", "duplicate_rate"),
        ("semantic", "character_count"),
        ("plan", "step_completion_rate"),
        ("plan", "avg_iterations_per_step"),
        ("plan", "total_corrections"),
        ("plan", "total_plan_steps"),
        ("efficiency", "llm_calls"),
        ("efficiency", "total_commands"),
        ("efficiency", "dropped_prefabs"),
        ("efficiency", "fill_commands_total"),
        ("efficiency", "phase1_duration_seconds"),
        # Phase 2 — Coverage
        ("phase2.coverage", "ambient_object_coverage"),
        ("phase2.coverage", "multi_event_pct"),
        ("phase2.coverage", "bubble_density"),
        ("phase2.coverage", "targeted_animation_pct"),
        # Phase 2 — Validity
        ("phase2.validity", "map_controller_validity_rate"),
        ("phase2.validity", "map_state_prefix_validity"),
        ("phase2.validity", "map_target_validity_rate"),
        ("phase2.validity", "map_bubble_on_validity"),
        # Phase 2 — Chain
        ("phase2.chain", "chain_length"),
        ("phase2.chain", "chain_beat_validity_rate"),
        ("phase2.chain", "chain_trigger_validity_rate"),
        ("phase2.chain", "chain_action_richness"),
        ("phase2.chain", "chain_unique_action_types"),
        ("phase2.chain", "chain_char_coverage"),
        ("phase2.chain", "chain_walk_coord_validity"),
        # Phase 2 — Runtime (only populated when INCLUDE_PHASE2 was set)
        ("phase2.runtime", "phase2_ran"),
        ("phase2.runtime", "phase2_duration_seconds"),
        ("phase2.runtime", "chain_beats_fired"),
        ("phase2.runtime", "chain_progression"),
        ("phase2.runtime", "time_to_first_beat_seconds"),
        ("phase2.runtime", "time_to_complete_chain_seconds"),
        ("phase2.runtime", "total_events_handled"),
        ("phase2.runtime", "ambient_animations_fired"),
        ("phase2.runtime", "story_chain_dispatches"),
        ("phase2.runtime", "command_execution_rate"),
        ("phase2.runtime", "dispatch_failure_rate"),
        ("phase2.runtime", "unmapped_event_rate"),
    ]

    agg: Dict[str, Any] = {
        "scene_type": reports[0].get("scene_type", "unknown"),
        "num_trials": len(reports),
    }

    def _walk_section(report: Dict, dotted: str) -> Dict:
        """Resolve nested section path like 'phase2.chain' → report['phase2']['chain']."""
        node: Any = report
        for part in dotted.split("."):
            if not isinstance(node, dict):
                return {}
            node = node.get(part, {})
        return node if isinstance(node, dict) else {}

    for section, key in metric_paths:
        values = []
        for r in reports:
            sec = _walk_section(r, section)
            if key in sec:
                try:
                    values.append(float(sec[key]))
                except (TypeError, ValueError):
                    continue
        if values:
            agg[f"{section}.{key}_mean"] = round(_mean(values), 4)
            agg[f"{section}.{key}_std"] = round(_std(values), 4)

    # Phase 1 composite score
    composites = [r.get("composite_score", 0) for r in reports if "composite_score" in r]
    if composites:
        agg["composite_score_mean"] = round(_mean(composites), 2)
        agg["composite_score_std"] = round(_std(composites), 2)

    # Phase 2 composite score (separate from Phase 1's composite_score above)
    p2_composites = [r.get("phase2", {}).get("composite_score", 0)
                     for r in reports
                     if isinstance(r.get("phase2"), dict) and "composite_score" in r["phase2"]]
    if p2_composites:
        agg["phase2.composite_score_mean"] = round(_mean(p2_composites), 2)
        agg["phase2.composite_score_std"]  = round(_std(p2_composites), 2)

    # LLM Judge — Phase 1 ratings
    coherence = [r.get("llm_judge", {}).get("coherence_score", 0) for r in reports
                 if r.get("llm_judge", {}).get("coherence_score")]
    spatial = [r.get("llm_judge", {}).get("spatial_score", 0) for r in reports
               if r.get("llm_judge", {}).get("spatial_score")]
    if coherence:
        agg["llm_judge.coherence_mean"] = round(_mean(coherence), 2)
        agg["llm_judge.coherence_std"] = round(_std(coherence), 2)
    if spatial:
        agg["llm_judge.spatial_mean"] = round(_mean(spatial), 2)
        agg["llm_judge.spatial_std"] = round(_std(spatial), 2)

    # LLM Judge — Phase 2 ratings (nested under llm_judge.phase2)
    for phase2_key in ("bubble_informativeness", "narrative_coherence",
                       "character_voice_consistency", "bubble_object_fit"):
        vals = [r.get("llm_judge", {}).get("phase2", {}).get(phase2_key, 0)
                for r in reports
                if isinstance(r.get("llm_judge", {}).get("phase2"), dict)
                and r["llm_judge"]["phase2"].get(phase2_key)]
        if vals:
            agg[f"llm_judge.phase2.{phase2_key}_mean"] = round(_mean(vals), 2)
            agg[f"llm_judge.phase2.{phase2_key}_std"]  = round(_std(vals), 2)

    return agg


# ---------------------------------------------------------------------------
# Output: Summary Table
# ---------------------------------------------------------------------------

KEY_METRICS = [
    ("composite_score_mean", "Composite"),
    ("geometric.collision_rate_mean", "Collision%"),
    ("geometric.oob_rate_mean", "OOB%"),
    ("semantic.prefab_hit_rate_mean", "Hit Rate"),
    ("plan.step_completion_rate_mean", "Plan%"),
    ("geometric.floor_coverage_mean", "Coverage"),
    ("geometric.height_violation_rate_mean", "Height Err"),
    ("semantic.duplicate_rate_mean", "Dup%"),
    ("efficiency.llm_calls_mean", "LLM Calls"),
    ("efficiency.phase1_duration_seconds_mean", "Time(s)"),
    ("llm_judge.coherence_mean", "Coherence"),
    ("llm_judge.spatial_mean", "Spatial"),
    ("phase2.composite_score_mean", "P2 Comp"),
    ("phase2.coverage.ambient_object_coverage_mean", "P2 Cov"),
    ("phase2.chain.chain_beat_validity_rate_mean", "P2 BeatV"),
    ("llm_judge.phase2.narrative_coherence_mean", "P2 Narr"),
]


def print_summary_table(all_agg: Dict[str, Dict]) -> str:
    """Print a formatted summary table and return as string."""
    header_labels = ["Scenario"] + [label for _, label in KEY_METRICS]
    col_widths = [max(12, len(h) + 2) for h in header_labels]

    lines = []
    # Header
    header = ""
    for i, label in enumerate(header_labels):
        header += label.ljust(col_widths[i])
    lines.append(header)
    lines.append("-" * len(header))

    # Rows
    for scenario, agg in sorted(all_agg.items()):
        row = scenario.ljust(col_widths[0])
        for i, (key, _) in enumerate(KEY_METRICS):
            val = agg.get(key, None)
            if val is not None:
                if isinstance(val, float):
                    cell = f"{val:.3f}"
                else:
                    cell = str(val)
            else:
                cell = "-"
            row += cell.ljust(col_widths[i + 1])
        lines.append(row)

    # Overall average
    lines.append("-" * len(lines[0]))
    avg_row = "AVERAGE".ljust(col_widths[0])
    for i, (key, _) in enumerate(KEY_METRICS):
        values = [agg.get(key, None) for agg in all_agg.values()]
        values = [v for v in values if v is not None and isinstance(v, (int, float))]
        if values:
            cell = f"{_mean(values):.3f}"
        else:
            cell = "-"
        avg_row += cell.ljust(col_widths[i + 1])
    lines.append(avg_row)

    table = "\n".join(lines)
    print(table)
    return table


# ---------------------------------------------------------------------------
# Output: Charts (matplotlib, optional)
# ---------------------------------------------------------------------------

def generate_charts(all_agg: Dict[str, Dict], output_dir: Path) -> None:
    """Generate bar charts for key metrics. Requires matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  [WARN] matplotlib not installed, skipping chart generation.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    scenarios = sorted(all_agg.keys())

    chart_metrics = [
        ("composite_score_mean", "composite_score_std", "Composite Score (0-100)"),
        ("geometric.collision_rate_mean", "geometric.collision_rate_std", "Collision Rate"),
        ("semantic.prefab_hit_rate_mean", "semantic.prefab_hit_rate_std", "Prefab Hit Rate"),
        ("plan.step_completion_rate_mean", "plan.step_completion_rate_std", "Step Completion Rate"),
    ]

    for mean_key, std_key, title in chart_metrics:
        means = [all_agg[s].get(mean_key, 0) for s in scenarios]
        stds = [all_agg[s].get(std_key, 0) for s in scenarios]

        fig, ax = plt.subplots(figsize=(12, 5))
        x = range(len(scenarios))
        ax.bar(x, means, yerr=stds, capsize=4, alpha=0.8, color="#4a90d9")
        ax.set_xticks(x)
        ax.set_xticklabels(scenarios, rotation=45, ha="right", fontsize=9)
        ax.set_ylabel(title)
        ax.set_title(f"{title} by Scenario")
        ax.grid(axis="y", alpha=0.3)
        plt.tight_layout()

        fname = mean_key.replace(".", "_") + ".png"
        fig.savefig(output_dir / fname, dpi=150)
        plt.close(fig)
        print(f"  Chart saved: {output_dir / fname}")

    # Radar chart for overall averages
    _generate_radar(all_agg, scenarios, output_dir)


def _generate_radar(all_agg: Dict[str, Dict], scenarios: List[str], output_dir: Path) -> None:
    """Generate a radar chart comparing scenarios on key metrics."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return

    radar_metrics = [
        ("composite_score_mean", "Composite", 100),
        ("semantic.prefab_hit_rate_mean", "Hit Rate", 1.0),
        ("plan.step_completion_rate_mean", "Plan%", 1.0),
        ("geometric.floor_coverage_mean", "Coverage", 1.0),
    ]

    labels = [label for _, label, _ in radar_metrics]
    num_vars = len(labels)
    angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

    for scenario in scenarios[:5]:  # limit to 5 for readability
        values = []
        for key, _, max_val in radar_metrics:
            v = all_agg[scenario].get(key, 0)
            values.append(v / max_val if max_val > 0 else 0)
        values += values[:1]
        ax.plot(angles, values, "-o", label=scenario, linewidth=1.5, markersize=4)
        ax.fill(angles, values, alpha=0.1)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylim(0, 1.0)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=8)
    ax.set_title("Scenario Comparison (Radar)", pad=20)
    plt.tight_layout()
    fig.savefig(output_dir / "radar_comparison.png", dpi=150)
    plt.close(fig)
    print(f"  Radar chart saved: {output_dir / 'radar_comparison.png'}")


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

def export_csv(all_agg: Dict[str, Dict], output_path: Path) -> None:
    """Export aggregated results to CSV."""
    if not all_agg:
        return

    # Collect all keys
    all_keys = set()
    for agg in all_agg.values():
        all_keys.update(agg.keys())
    all_keys = sorted(all_keys)

    lines = ["scenario," + ",".join(all_keys)]
    for scenario, agg in sorted(all_agg.items()):
        vals = [str(agg.get(k, "")) for k in all_keys]
        lines.append(scenario + "," + ",".join(vals))

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  CSV exported: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Aggregate experiment results")
    parser.add_argument("--results-root", type=Path, required=True,
                        help="Root directory: results_root/{scenario}/{trial}/")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Directory for reports and charts")
    args = parser.parse_args()

    output_dir = args.output_dir or (args.results_root.parent / "reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nCollecting reports from: {args.results_root}")
    grouped = collect_reports(args.results_root)

    if not grouped:
        print("  No reports found. Run experiments first.")
        sys.exit(1)

    print(f"  Found {sum(len(v) for v in grouped.values())} reports across {len(grouped)} scenarios.\n")

    # Aggregate
    all_agg: Dict[str, Dict] = {}
    for scenario, reports in sorted(grouped.items()):
        all_agg[scenario] = aggregate_scenario(reports)

    # Summary table
    print("\n" + "=" * 80)
    print("  SUMMARY TABLE")
    print("=" * 80 + "\n")
    table_str = print_summary_table(all_agg)

    # Save outputs
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(all_agg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Summary JSON: {summary_path}")

    table_path = output_dir / "summary_table.txt"
    table_path.write_text(table_str, encoding="utf-8")
    print(f"  Summary table: {table_path}")

    export_csv(all_agg, output_dir / "summary.csv")
    generate_charts(all_agg, output_dir / "charts")


if __name__ == "__main__":
    main()
