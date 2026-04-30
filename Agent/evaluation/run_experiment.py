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
import os
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
INTERACTION_MAP_PATH = API_FILES_DIR / "interaction_map.json"
STORY_CHAIN_PATH = API_FILES_DIR / "story_chain.json"
COMMANDS_PATH = PROJECT_ROOT / "firefighter project" / "Assets" / "Scripts" / "unity_commands.json"

ALL_SCENARIOS = [f"S{i:02d}" for i in range(1, 16)]


def _find_scenario_file(scenario_id: str) -> Optional[Path]:
    """Find the experience file for a scenario."""
    path = SCENARIOS_DIR / f"experience_{scenario_id}.txt"
    if path.exists():
        return path
    return None


def _write_file_atomic(path: Path, data: str, retries: int = 5, delay: float = 0.3) -> None:
    """Write data to file using atomic replace to avoid lock conflicts with Unity."""
    for attempt in range(retries):
        try:
            tmp = path.parent / (path.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                # Fallback to direct write
                path.write_text(data, encoding="utf-8")


def _clear_unity_scene(wait: float = 5.0, retries: int = 3, stabilize: float = 3.0) -> bool:
    """Send ClearScene to Unity and wait for confirmation.

    Sends the command ONCE and polls for up to `wait * retries` seconds total.
    Does NOT re-write the command on retry — that would overwrite Unity's
    write-back of the executed flag, causing a confirmation race condition.

    Returns True if confirmed, False if timeout exhausted.
    """
    COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)

    total_wait = wait * retries
    clear_cmd = [
        {
            "action": "ClearScene",
            "id": f"cmd_clear_{int(time.time() * 1000)}",
            "timestamp": time.time(),
            "executed": False,
        }
    ]
    _write_file_atomic(COMMANDS_PATH, json.dumps(clear_cmd, indent=2))
    send_time = time.time()
    print(f"    Sent ClearScene, polling up to {total_wait:.0f}s...")

    deadline = time.time() + total_wait
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            data = json.loads(COMMANDS_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, list) and data and data[0].get("executed"):
                elapsed = time.time() - send_time
                print(f"    ClearScene confirmed in {elapsed:.1f}s. Waiting {stabilize}s for Unity to stabilize...")
                time.sleep(stabilize)
                return True
        except Exception:
            pass
        try:
            if SCENE_STATE_PATH.exists() and SCENE_STATE_PATH.stat().st_mtime > send_time:
                state = json.loads(SCENE_STATE_PATH.read_text(encoding="utf-8-sig"))
                if isinstance(state, list) and len(state) == 0:
                    elapsed = time.time() - send_time
                    print(f"    ClearScene confirmed (via scene_state) in {elapsed:.1f}s. Waiting {stabilize}s...")
                    time.sleep(stabilize)
                    return True
        except Exception:
            pass

    print(f"    ClearScene not confirmed after {total_wait:.0f}s — proceeding anyway.")
    return False


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


def _check_unity_alive(timeout: float = 10.0) -> bool:
    """Return True if Unity is processing commands (at least 1 non-structural command
    in unity_commands.json has executed=true after waiting up to `timeout` seconds).
    Structural CreateObjectByName commands (walls) are excluded since they execute first
    and may have been from a previous run."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cmds = json.loads(COMMANDS_PATH.read_text(encoding="utf-8-sig"))
            executed = [
                c for c in cmds
                if c.get("executed")
                and not (c.get("action") == "CreateObjectByName" and c.get("objectName") == "Cube")
            ]
            if executed:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    # Fall back: check if ANY command executed (even structural)
    try:
        cmds = json.loads(COMMANDS_PATH.read_text(encoding="utf-8-sig"))
        if any(c.get("executed") for c in cmds):
            return True
    except Exception:
        pass
    return False


def _preflight_unity_check() -> bool:
    """Quick pre-flight check: send a DebugLog command and see if Unity picks it up.
    Returns True if Unity is responsive, False otherwise."""
    COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    probe_cmd = [
        {
            "action": "DebugLog",
            "message": "preflight_probe",
            "id": f"cmd_preflight_{int(time.time() * 1000)}",
            "executed": False,
        }
    ]
    _write_file_atomic(COMMANDS_PATH, json.dumps(probe_cmd, indent=2))
    time.sleep(1.0)  # give Unity a full poll cycle to pick up the file
    deadline = time.time() + 10.0
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            data = json.loads(COMMANDS_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, list) and data and data[0].get("executed"):
                return True
        except Exception:
            pass
    return False


def _wait_for_fresh_screenshots(after_time: float, timeout: float = 20.0) -> bool:
    """Wait for Unity to write fresh screenshots captured after `after_time`.
    Returns True if at least 2 of 3 views updated within the timeout."""
    suffixes = ("_xz.png", "_xy.png", "_yz.png")
    paths = [API_FILES_DIR / f"scene_screenshot{s}" for s in suffixes]
    deadline = time.time() + timeout
    while time.time() < deadline:
        fresh = [
            p for p in paths
            if p.exists() and p.stat().st_size > 0 and p.stat().st_mtime > after_time
        ]
        if len(fresh) >= 2:
            return True
        time.sleep(1.0)
    return False


def _collect_artifacts(trial_dir: Path) -> None:
    """Copy output artifacts from api_files to the trial results directory."""
    trial_dir.mkdir(parents=True, exist_ok=True)

    artifacts = [
        (SCENE_STATE_PATH, "scene_state.json"),
        (REFLECTION_PATH, "reflection.json"),
        (AGENT_LOG_PATH, "agent.log"),
        (COMMANDS_PATH, "unity_commands.json"),
        (PLAN_PATH, "plan.json"),
        (INTERACTION_MAP_PATH, "interaction_map.json"),
        (STORY_CHAIN_PATH, "story_chain.json"),
        (EXPERIENCE_PATH, "experience.txt"),
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


def _run_phase1(scenario_file: Path, timeout: int = 300,
                frontier_model: Optional[str] = None,
                include_phase2: bool = False,
                phase2_max_iterations: int = 0) -> bool:
    """Run groq_static_test.py with the given scenario.

    By default runs Phase 1 only (--phase1-only). When include_phase2=True the
    flag is dropped so Phase 2 enters interactive mode after Phase 1; the
    subprocess will then poll for player events until phase2_max_iterations
    is reached (or until the trial timeout fires).

    frontier_model: forwards `--frontier-model X` to groq_static_test.py if set.
    """
    # Copy scenario to experience.txt
    shutil.copy2(scenario_file, EXPERIENCE_PATH)

    cmd = [
        PYTHON,
        str(AGENT_DIR / "groq_static_test.py"),
        "--path", str(EXPERIENCE_PATH),
        "--interval", "3.0",
    ]
    if not include_phase2:
        cmd.append("--phase1-only")
    elif phase2_max_iterations > 0:
        cmd += ["--max-iterations", str(phase2_max_iterations)]
    if frontier_model is not None:
        cmd += ["--frontier-model", frontier_model]

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


def _run_phase2_evaluation(trial_dir: Path, scenario_file: Path) -> Optional[dict]:
    """Run evaluate_phase2.py on collected interaction_map.json + story_chain.json."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from evaluate_phase2 import evaluate as evaluate_p2

    if not (trial_dir / "interaction_map.json").exists() and not (trial_dir / "story_chain.json").exists():
        return None

    try:
        report = evaluate_p2(trial_dir, scenario_file)
        report_path = trial_dir / "phase2_eval_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report
    except Exception as e:
        print(f"    [WARN] Phase 2 eval failed: {e}")
        return None


def _run_llm_judge(trial_dir: Path, scenario_file: Path, provider: str = "groq",
                    model: Optional[str] = None) -> Optional[dict]:
    """Run llm_judge.py on collected artifacts."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from llm_judge import run_judge

    try:
        ratings = run_judge(trial_dir, scenario_file, provider=provider, model=model)
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
    parser.add_argument("--clear-wait", type=float, default=8.0,
                        help="Seconds to wait per ClearScene attempt (default: 8)")
    parser.add_argument("--clear-retries", type=int, default=3,
                        help="Number of ClearScene retry attempts if Unity doesn't respond (default: 3)")
    parser.add_argument("--stabilize", type=float, default=3.0,
                        help="Seconds to wait after ClearScene confirms, for Unity GC to settle (default: 3)")
    parser.add_argument("--skip-judge", action="store_true",
                        help="Skip LLM judge evaluation")
    parser.add_argument("--judge-provider", choices=["groq", "openai"], default="groq",
                        help="LLM provider for judge (default: groq)")
    parser.add_argument("--eval-only", action="store_true",
                        help="Only run evaluation on existing results (no Phase 1 generation)")
    parser.add_argument("--aggregate-only", action="store_true",
                        help="Only run aggregation on existing evaluation reports")
    parser.add_argument("--results-dir", type=str, default=None,
                        help="Custom results directory (default: evaluation/results)")
    parser.add_argument("--abort-no-unity", action="store_true",
                        help="Abort the entire experiment if Unity stops responding")
    parser.add_argument("--frontier-model", type=str, default=None,
                        help="Forwarded to groq_static_test.py as --frontier-model. "
                             "If omitted, groq_static_test.py uses its own default (currently gpt-5.5). "
                             "Pass '' (empty string) to disable frontier and use Llama-4-Scout for everything.")
    parser.add_argument("--include-phase2", action="store_true",
                        help="Run Phase 2 (interactive event loop) after Phase 1. "
                             "Without this flag, only Phase 1 + static Phase 2 evaluation runs.")
    parser.add_argument("--phase2-max-iterations", type=int, default=0,
                        help="Cap Phase 2 event-loop iterations (0 = no cap, only --timeout stops it). "
                             "Only applies when --include-phase2 is set.")
    parser.add_argument("--judge-model", type=str, default=None,
                        help="Override the LLM-judge model (e.g. 'gpt-5.5', 'gpt-5.2'). "
                             "Defaults to the provider's standard. Only applies when --judge-provider=openai.")
    args = parser.parse_args()

    # Override results directory if specified
    global RESULTS_DIR
    if args.results_dir:
        RESULTS_DIR = Path(args.results_dir).resolve()
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  Results directory: {RESULTS_DIR}")

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

    # Pre-flight: verify Unity is alive before starting
    if not args.eval_only:
        print("  Pre-flight: checking Unity responsiveness...")
        if _preflight_unity_check():
            print("  Unity is alive. Starting experiment.\n")
        else:
            print("  [ERROR] Unity is NOT responding to commands.")
            print("    Ensure: Unity Editor open, scene in Play mode, AgentNetworkBridge active.")
            print("  Aborting. Fix Unity and re-run.")
            return

    # Track overall results
    run_results = []
    consecutive_no_unity = 0  # auto-abort after repeated failures
    MAX_CONSECUTIVE_NO_UNITY = 3

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
                # Clear previous scene before each trial.
                # ClearScene confirmation also serves as our Unity liveness check.
                clear_ok = _clear_unity_scene(
                    wait=args.clear_wait,
                    retries=args.clear_retries,
                    stabilize=args.stabilize,
                )
                _clear_api_files()

                if not clear_ok:
                    consecutive_no_unity += 1
                    if consecutive_no_unity >= MAX_CONSECUTIVE_NO_UNITY:
                        print(f"    [ABORT] ClearScene failed {consecutive_no_unity} consecutive times. Unity unresponsive.")
                        _run_aggregation()
                        return
                else:
                    consecutive_no_unity = 0

                success = _run_phase1(scenario_file, timeout=args.timeout,
                                      frontier_model=args.frontier_model,
                                      include_phase2=args.include_phase2,
                                      phase2_max_iterations=args.phase2_max_iterations)
                phase1_end = time.time()
                if not success:
                    print(f"    [FAIL] Skipping evaluation for this trial")
                    run_results.append({"scenario": scenario_id, "trial": trial, "status": "FAILED"})
                    continue

                # Brief wait for Unity to finish processing last commands and
                # write-back executed flags (avoids file race with Phase 1's final write).
                time.sleep(3.0)

                # Wait for Unity to capture a fresh screenshot of the final scene
                # before collecting artifacts — avoids saving stale mid-trial images.
                print(f"    Waiting for fresh screenshots of final scene state...")
                got_fresh = _wait_for_fresh_screenshots(after_time=phase1_end, timeout=20.0)
                if not got_fresh:
                    print(f"    [WARN] Fresh screenshots not ready within 20s — collecting anyway")

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

                # Phase 2 evaluation (interaction_map.json + story_chain.json static metrics)
                p2_report = _run_phase2_evaluation(trial_dir, scenario_file)
                if p2_report:
                    p2_score = p2_report.get("composite_score", 0)
                    cov = p2_report.get("coverage", {}).get("ambient_object_coverage", 0)
                    chain_len = p2_report.get("chain", {}).get("chain_length", 0)
                    beat_valid = p2_report.get("chain", {}).get("chain_beat_validity_rate", 0)
                    print(f"    Phase 2: composite={p2_score:.1f} coverage={cov:.2f} "
                          f"chain={chain_len}beats valid={beat_valid:.2f}")

                # LLM Judge
                if not args.skip_judge and (trial_dir / "scene_state.json").exists():
                    ratings = _run_llm_judge(trial_dir, scenario_file,
                                             provider=args.judge_provider,
                                             model=args.judge_model)
                    if ratings:
                        c = ratings.get("coherence_score", "?")
                        s = ratings.get("spatial_score", "?")
                        print(f"    LLM Judge: coherence={c}/5  spatial={s}/5")
                        p2 = ratings.get("phase2", {})
                        if isinstance(p2, dict) and "error" not in p2:
                            bi = p2.get("bubble_informativeness", "?")
                            nc = p2.get("narrative_coherence", "?")
                            print(f"    LLM Judge (Phase 2): bubble={bi}/5  narrative={nc}/5")
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
