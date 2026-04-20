"""
Experiment 1 Runner: 10 Scenarios x 3 Trials each.

Orchestrates the full evaluation pipeline:
  1. Copy scenario experience file to api_files/experience.txt
  2. Run groq_static_test.py --phase1-only
  3. Collect output artifacts to results directory
  4. Run evaluate_scene.py on collected artifacts
  5. Run llm_judge.py on collected artifacts
  6. After all runs, aggregate results

Usage:
    python run_experiment.py [--scenarios S01,S03,S07] [--trials 3] [--skip-judge]

Requires Unity running with ScreenshotLoopSender active for full evaluation.
For offline (no Unity) runs, use --offline to skip screenshot waiting.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).parent.resolve()
AGENT_DIR = SCRIPT_DIR.parent

# Resolve Python interpreter: prefer the one running this script
PYTHON = sys.executable
PROJECT_ROOT = AGENT_DIR.parent
SCENARIOS_DIR = SCRIPT_DIR / "scenarios"
RESULTS_DIR = SCRIPT_DIR / "results"

API_FILES_DIR = AGENT_DIR / "api_files"
EXPERIENCE_PATH = API_FILES_DIR / "experience.txt"
SCENE_STATE_PATH = API_FILES_DIR / "scene_state.json"
REFLECTION_PATH = API_FILES_DIR / "reflection.json"
AGENT_LOG_PATH = API_FILES_DIR / "agent.log"
PLAN_PATH = API_FILES_DIR / "plan.json"
COMMANDS_PATH = PROJECT_ROOT / "firefighter project" / "Assets" / "Scripts" / "unity_commands.json"

ALL_SCENARIOS = [f"S{i:02d}" for i in range(1, 11)]


def _find_scenario_file(scenario_id: str) -> Optional[Path]:
    """Find the experience file for a scenario."""
    path = SCENARIOS_DIR / f"experience_{scenario_id}.txt"
    if path.exists():
        return path
    return None


def _clear_unity_scene(wait: float = 5.0) -> None:
    """Send ClearScene command to Unity via unity_commands.json, then poll until confirmed."""
    clear_cmd = [
        {
            "action": "ClearScene",
            "id": f"cmd_clear_{int(time.time() * 1000)}",
            "timestamp": time.time(),
            "executed": False,
        }
    ]
    COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMANDS_PATH.write_text(json.dumps(clear_cmd, indent=2), encoding="utf-8")
    print(f"    Sent ClearScene, polling for confirmation (max {wait}s)...")

    # Poll: Unity sets executed=true and rewrites the file after processing
    deadline = time.time() + wait
    confirmed = False
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            data = json.loads(COMMANDS_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, list) and data and data[0].get("executed"):
                confirmed = True
                break
        except Exception:
            pass
        # Also check if scene_state.json is empty/has no non-Cube objects
        try:
            state = json.loads(SCENE_STATE_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(state, list) and len(state) == 0:
                confirmed = True
                break
        except Exception:
            pass

    if confirmed:
        print(f"    ClearScene confirmed.")
    else:
        print(f"    ClearScene not confirmed within {wait}s — proceeding anyway.")


def _clear_api_files() -> None:
    """Reset all api_files to clean state between trials."""
    for jf in [REFLECTION_PATH, API_FILES_DIR / "actions.json"]:
        try:
            jf.write_text("[]", encoding="utf-8")
        except Exception:
            pass
    for lf in [AGENT_LOG_PATH, API_FILES_DIR / "reflection_log.log"]:
        try:
            lf.write_text("", encoding="utf-8")
        except Exception:
            pass
    # Clear scene_state (Unity will rewrite after ClearScene)
    try:
        SCENE_STATE_PATH.write_text("[]", encoding="utf-8")
    except Exception:
        pass


def _collect_artifacts(trial_dir: Path) -> None:
    """Copy output artifacts from api_files to the trial results directory."""
    trial_dir.mkdir(parents=True, exist_ok=True)

    artifacts = [
        (SCENE_STATE_PATH, "scene_state.json"),
        (REFLECTION_PATH, "reflection.json"),
        (AGENT_LOG_PATH, "agent.log"),
        (COMMANDS_PATH, "unity_commands.json"),
        (PLAN_PATH, "plan.json"),
    ]

    for src, dst_name in artifacts:
        dst = trial_dir / dst_name
        if src.exists():
            shutil.copy2(src, dst)
        else:
            print(f"    [WARN] Artifact not found: {src}")

    # Copy screenshots if available
    for suffix in ("_xz.png", "_xy.png", "_yz.png"):
        ss_path = API_FILES_DIR / f"scene_screenshot{suffix}"
        if ss_path.exists():
            shutil.copy2(ss_path, trial_dir / f"scene_screenshot{suffix}")


def _run_phase1(scenario_file: Path, timeout: int = 300) -> bool:
    """Run groq_static_test.py --phase1-only with the given scenario."""
    # Copy scenario to experience.txt
    shutil.copy2(scenario_file, EXPERIENCE_PATH)

    cmd = [
        PYTHON,
        str(AGENT_DIR / "groq_static_test.py"),
        "--phase1-only",
        "--path", str(EXPERIENCE_PATH),
        "--interval", "3.0",
    ]

    print(f"    Running Phase 1: {' '.join(cmd[-4:])}")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(AGENT_DIR),
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"    [ERROR] Phase 1 failed (exit code {result.returncode})")
            if result.stderr:
                # Print last 10 lines of stderr
                stderr_lines = result.stderr.strip().splitlines()
                for line in stderr_lines[-10:]:
                    print(f"      {line}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print(f"    [ERROR] Phase 1 timed out after {timeout}s")
        return False
    except Exception as e:
        print(f"    [ERROR] Phase 1 exception: {e}")
        return False


def _run_evaluation(trial_dir: Path, scenario_file: Path) -> Optional[dict]:
    """Run evaluate_scene.py on collected artifacts."""
    # Import evaluate locally
    sys.path.insert(0, str(SCRIPT_DIR))
    from evaluate_scene import evaluate

    report = evaluate(trial_dir, scenario_file)

    report_path = trial_dir / "eval_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _run_llm_judge(trial_dir: Path, scenario_file: Path, provider: str = "groq") -> Optional[dict]:
    """Run llm_judge.py on collected artifacts."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from llm_judge import run_judge

    try:
        ratings = run_judge(trial_dir, scenario_file, provider=provider)
        judge_path = trial_dir / "llm_judge_report.json"
        judge_path.write_text(json.dumps(ratings, indent=2, ensure_ascii=False), encoding="utf-8")
        return ratings
    except Exception as e:
        print(f"    [WARN] LLM Judge failed: {e}")
        return None


def _run_aggregation() -> None:
    """Run aggregate_results.py on all collected results."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from aggregate_results import collect_reports, aggregate_scenario, print_summary_table, generate_charts, export_csv

    grouped = collect_reports(RESULTS_DIR)
    if not grouped:
        print("\n  No reports found for aggregation.")
        return

    all_agg = {}
    for scenario, reports in sorted(grouped.items()):
        all_agg[scenario] = aggregate_scenario(reports)

    reports_dir = SCRIPT_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 80)
    print("  EXPERIMENT 1 RESULTS SUMMARY")
    print("=" * 80 + "\n")
    table_str = print_summary_table(all_agg)

    # Save outputs
    summary_path = reports_dir / "summary.json"
    summary_path.write_text(json.dumps(all_agg, indent=2, ensure_ascii=False), encoding="utf-8")
    (reports_dir / "summary_table.txt").write_text(table_str, encoding="utf-8")
    export_csv(all_agg, reports_dir / "summary.csv")
    generate_charts(all_agg, reports_dir / "charts")

    print(f"\n  Reports saved to: {reports_dir}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run Experiment 1: 10 scenarios x 3 trials")
    parser.add_argument("--scenarios", type=str, default=None,
                        help="Comma-separated scenario IDs (e.g. S01,S03,S07). Default: all 10")
    parser.add_argument("--trials", type=int, default=3,
                        help="Number of trials per scenario (default: 3)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Timeout per trial in seconds (default: 300)")
    parser.add_argument("--clear-wait", type=float, default=5.0,
                        help="Seconds to wait after ClearScene for Unity to process (default: 5)")
    parser.add_argument("--skip-judge", action="store_true",
                        help="Skip LLM judge evaluation")
    parser.add_argument("--judge-provider", choices=["groq", "openai"], default="groq",
                        help="LLM provider for judge (default: groq)")
    parser.add_argument("--eval-only", action="store_true",
                        help="Only run evaluation on existing results (no Phase 1 generation)")
    parser.add_argument("--aggregate-only", action="store_true",
                        help="Only run aggregation on existing evaluation reports")
    args = parser.parse_args()

    # Determine which scenarios to run
    if args.scenarios:
        scenarios = [s.strip().upper() for s in args.scenarios.split(",")]
    else:
        scenarios = ALL_SCENARIOS

    # Aggregate-only mode
    if args.aggregate_only:
        _run_aggregation()
        return

    print(f"\n{'=' * 60}")
    print(f"  Experiment 1: Scene Generation Quality Evaluation")
    print(f"  Scenarios: {', '.join(scenarios)}")
    print(f"  Trials per scenario: {args.trials}")
    print(f"  Total runs: {len(scenarios) * args.trials}")
    print(f"{'=' * 60}\n")

    # Track overall results
    run_results = []

    for scenario_id in scenarios:
        scenario_file = _find_scenario_file(scenario_id)
        if not scenario_file:
            print(f"[SKIP] Scenario {scenario_id}: experience file not found")
            continue

        print(f"\n--- Scenario {scenario_id} ---")

        for trial in range(1, args.trials + 1):
            trial_dir = RESULTS_DIR / scenario_id / f"trial_{trial}"
            print(f"\n  Trial {trial}/{args.trials}  ->  {trial_dir}")

            # Phase 1 generation (unless eval-only)
            if not args.eval_only:
                # Clear previous scene before each trial
                _clear_unity_scene(wait=args.clear_wait)
                _clear_api_files()

                success = _run_phase1(scenario_file, timeout=args.timeout)
                if not success:
                    print(f"    [FAIL] Skipping evaluation for this trial")
                    run_results.append({"scenario": scenario_id, "trial": trial, "status": "FAILED"})
                    continue

                # Collect artifacts
                _collect_artifacts(trial_dir)
                print(f"    Artifacts collected to {trial_dir}")

            # Evaluation
            if (trial_dir / "scene_state.json").exists():
                report = _run_evaluation(trial_dir, scenario_file)
                if report:
                    score = report.get("composite_score", 0)
                    collision = report.get("geometric", {}).get("collision_rate", 0)
                    hit_rate = report.get("semantic", {}).get("prefab_hit_rate", 0)
                    print(f"    Composite: {score:.1f}  Collision: {collision:.3f}  Hit Rate: {hit_rate:.3f}")
                    run_results.append({
                        "scenario": scenario_id, "trial": trial,
                        "status": "OK", "composite": score,
                    })
                else:
                    run_results.append({"scenario": scenario_id, "trial": trial, "status": "EVAL_FAILED"})

                # LLM Judge
                if not args.skip_judge and (trial_dir / "scene_state.json").exists():
                    ratings = _run_llm_judge(trial_dir, scenario_file, provider=args.judge_provider)
                    if ratings:
                        c = ratings.get("coherence_score", "?")
                        s = ratings.get("spatial_score", "?")
                        print(f"    LLM Judge: coherence={c}/5  spatial={s}/5")
            else:
                print(f"    [SKIP] No scene_state.json found in {trial_dir}")
                run_results.append({"scenario": scenario_id, "trial": trial, "status": "NO_DATA"})

            # Brief pause between trials to avoid API rate limits
            if trial < args.trials:
                time.sleep(2)

    # Aggregation
    print("\n\n" + "=" * 60)
    print("  Running aggregation...")
    print("=" * 60)
    _run_aggregation()

    # Final summary
    print(f"\n\n{'=' * 60}")
    print(f"  EXPERIMENT COMPLETE")
    print(f"{'=' * 60}")
    total = len(run_results)
    ok = sum(1 for r in run_results if r["status"] == "OK")
    failed = sum(1 for r in run_results if r["status"] == "FAILED")
    print(f"  Total runs: {total}")
    print(f"  Successful: {ok}")
    print(f"  Failed: {failed}")
    if ok > 0:
        avg_composite = sum(r.get("composite", 0) for r in run_results if r["status"] == "OK") / ok
        print(f"  Average composite score: {avg_composite:.1f}")
    print(f"\n  Results: {RESULTS_DIR}")
    print(f"  Reports: {SCRIPT_DIR / 'reports'}")
    print()


if __name__ == "__main__":
    main()
