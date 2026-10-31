"""
Experiment runner: generate, evaluate, and aggregate scenes for every scenario/trial.

For each scenario (``scenarios/experience_SXX.txt``) and each trial, this script:

  1. Sends ``ClearScene`` to Unity and resets the files in ``Agent/api_files/``.
  2. Copies the scenario file to ``api_files/experience.txt``.
  3. Runs ``groq_static_test.py`` (Phase 1 only by default; ``--include-phase2``
     also runs the interactive Phase 2 event loop).
  4. Copies the output artifacts into ``<results-dir>/<scenario>/trial_<N>/``.
  5. Runs ``evaluate_scene.py`` -> ``eval_report.json`` and
     ``evaluate_phase2.py`` -> ``phase2_eval_report.json``.
  6. Runs ``llm_judge.py`` -> ``llm_judge_report.json`` (unless ``--skip-judge``).

After all trials, results are aggregated into ``evaluation/reports/``
(``summary.json``, ``summary.csv``, ``summary_table.txt``, ``charts/``).

Requirements:
    Unity Editor in Play mode with ``AgentNetworkBridge`` and
    ``ScreenshotLoopSender`` active (not needed for ``--eval-only`` /
    ``--aggregate-only``). API keys (``GROQ_API_KEY``, and ``OPENAI_API_KEY``
    for frontier/OpenAI-judge runs) in the environment or ``Agent/.env``.

Usage (from ``Agent/``):
    python evaluation/run_experiment.py                         # all scenarios, 3 trials
    python evaluation/run_experiment.py --scenarios S01,S03 --trials 2
    python evaluation/run_experiment.py --skip-judge --frontier-model gpt-5.4-mini
    python evaluation/run_experiment.py --judge-provider openai --judge-model gpt-5.5
    python evaluation/run_experiment.py --eval-only             # re-score existing results
    python evaluation/run_experiment.py --aggregate-only        # rebuild reports only

Run ``python evaluation/run_experiment.py --help`` for the full list of flags.
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


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
AGENT_DIR = SCRIPT_DIR.parent

# Use the same interpreter that is running this script for the pipeline subprocess.
PYTHON = sys.executable
PROJECT_ROOT = AGENT_DIR.parent
SCENARIOS_DIR = SCRIPT_DIR / "scenarios"
RESULTS_DIR = SCRIPT_DIR / "results"  # overridden by --results-dir

API_FILES_DIR = AGENT_DIR / "api_files"
EXPERIENCE_PATH = API_FILES_DIR / "experience.txt"
SCENE_STATE_PATH = API_FILES_DIR / "scene_state.json"
REFLECTION_PATH = API_FILES_DIR / "reflection.json"
AGENT_LOG_PATH = API_FILES_DIR / "agent.log"
PLAN_PATH = API_FILES_DIR / "plan.json"
INTERACTION_MAP_PATH = API_FILES_DIR / "interaction_map.json"
STORY_CHAIN_PATH = API_FILES_DIR / "story_chain.json"
COMMANDS_PATH = (
    PROJECT_ROOT / "firefighter project" / "Assets" / "Scripts" / "unity_commands.json"
)

ALL_SCENARIOS = [f"S{i:02d}" for i in range(1, 16)]


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _find_scenario_file(scenario_id: str) -> Optional[Path]:
    """Return ``scenarios/experience_<id>.txt`` if it exists, else None."""
    path = SCENARIOS_DIR / f"experience_{scenario_id}.txt"
    if path.exists():
        return path
    return None


def _write_file_atomic(path: Path, data: str, retries: int = 5, delay: float = 0.3) -> None:
    """Write ``data`` via temp file + atomic replace, to avoid lock conflicts with Unity.

    Retries on PermissionError; falls back to a direct write on the last attempt.
    """
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
                path.write_text(data, encoding="utf-8")


def _clear_api_files() -> None:
    """Reset reflection/action/log/scene-state files in api_files between trials."""
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
    # Unity rewrites scene_state.json after ClearScene.
    try:
        SCENE_STATE_PATH.write_text("[]", encoding="utf-8")
    except Exception:
        pass


def _collect_artifacts(trial_dir: Path) -> None:
    """Copy output artifacts (JSON, log, screenshots) from api_files into ``trial_dir``."""
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

    for suffix in ("_xz.png", "_xy.png", "_yz.png"):
        ss_path = API_FILES_DIR / f"scene_screenshot{suffix}"
        if ss_path.exists():
            shutil.copy2(ss_path, trial_dir / f"scene_screenshot{suffix}")


# ---------------------------------------------------------------------------
# Unity communication
# ---------------------------------------------------------------------------

def _clear_unity_scene(wait: float = 5.0, retries: int = 3, stabilize: float = 3.0) -> bool:
    """Send ClearScene to Unity and wait for confirmation.

    The command is written ONCE and polled for up to ``wait * retries`` seconds.
    It is deliberately not re-written on retry: that would overwrite Unity's
    write-back of the ``executed`` flag and cause a confirmation race.

    Confirmation is either the command's ``executed`` flag or a freshly
    written, empty ``scene_state.json``. Returns True if confirmed.
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
                print(f"    ClearScene confirmed in {elapsed:.1f}s. "
                      f"Waiting {stabilize}s for Unity to stabilize...")
                time.sleep(stabilize)
                return True
        except Exception:
            pass
        try:
            if SCENE_STATE_PATH.exists() and SCENE_STATE_PATH.stat().st_mtime > send_time:
                state = json.loads(SCENE_STATE_PATH.read_text(encoding="utf-8-sig"))
                if isinstance(state, list) and len(state) == 0:
                    elapsed = time.time() - send_time
                    print(f"    ClearScene confirmed (via scene_state) in {elapsed:.1f}s. "
                          f"Waiting {stabilize}s...")
                    time.sleep(stabilize)
                    return True
        except Exception:
            pass

    print(f"    ClearScene not confirmed after {total_wait:.0f}s — proceeding anyway.")
    return False


def _preflight_unity_check() -> bool:
    """Send a ``DebugLog`` probe command; return True if Unity marks it executed within ~11 s."""
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
    """Wait for Unity to write screenshots newer than ``after_time``.

    Returns True once at least 2 of the 3 orthographic views (xz/xy/yz) are fresh.
    """
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


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def _run_phase1(scenario_file: Path, timeout: int = 300,
                frontier_model: Optional[str] = None,
                include_phase2: bool = False,
                phase2_max_iterations: int = 0) -> bool:
    """Run ``groq_static_test.py`` on ``scenario_file``; return True on exit code 0.

    By default passes ``--phase1-only``. With ``include_phase2=True`` that flag
    is dropped so the pipeline continues into the interactive Phase 2 loop,
    which runs until ``phase2_max_iterations`` (if > 0) or the trial timeout.
    ``frontier_model``, if not None, is forwarded as ``--frontier-model``.
    """
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
    """Run ``evaluate_scene.evaluate`` and write ``eval_report.json``."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from evaluate_scene import evaluate

    report = evaluate(trial_dir, scenario_file)

    report_path = trial_dir / "eval_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _run_phase2_evaluation(trial_dir: Path, scenario_file: Path) -> Optional[dict]:
    """Run ``evaluate_phase2.evaluate`` and write ``phase2_eval_report.json``.

    Returns None if neither Phase 2 artifact exists or evaluation fails.
    """
    sys.path.insert(0, str(SCRIPT_DIR))
    from evaluate_phase2 import evaluate as evaluate_p2

    if (not (trial_dir / "interaction_map.json").exists()
            and not (trial_dir / "story_chain.json").exists()):
        return None

    try:
        report = evaluate_p2(trial_dir, scenario_file)
        report_path = trial_dir / "phase2_eval_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        return report
    except Exception as e:
        print(f"    [WARN] Phase 2 eval failed: {e}")
        return None


def _run_llm_judge(trial_dir: Path, scenario_file: Path, provider: str = "groq",
                   model: Optional[str] = None) -> Optional[dict]:
    """Run ``llm_judge.run_judge`` and write ``llm_judge_report.json``; None on failure."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from llm_judge import run_judge

    try:
        ratings = run_judge(trial_dir, scenario_file, provider=provider, model=model)
        judge_path = trial_dir / "llm_judge_report.json"
        judge_path.write_text(json.dumps(ratings, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        return ratings
    except Exception as e:
        print(f"    [WARN] LLM Judge failed: {e}")
        return None


def _run_aggregation() -> None:
    """Aggregate all reports under RESULTS_DIR into ``evaluation/reports/``."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from aggregate_results import (
        aggregate_scenario,
        collect_reports,
        export_csv,
        generate_charts,
        print_summary_table,
    )

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
    parser = argparse.ArgumentParser(description="Run Experiment 1: scenarios x trials")
    parser.add_argument("--scenarios", type=str, default=None,
                        help="Comma-separated scenario IDs (e.g. S01,S03,S07). "
                             "Default: all (S01-S15)")
    parser.add_argument("--trials", type=int, default=3,
                        help="Number of trials per scenario (default: 3)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Timeout per trial in seconds (default: 300)")
    parser.add_argument("--clear-wait", type=float, default=8.0,
                        help="Seconds to wait per ClearScene attempt (default: 8)")
    parser.add_argument("--clear-retries", type=int, default=3,
                        help="Number of ClearScene retry attempts if Unity doesn't respond "
                             "(default: 3)")
    parser.add_argument("--stabilize", type=float, default=3.0,
                        help="Seconds to wait after ClearScene confirms, for Unity GC to settle "
                             "(default: 3)")
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
                        help="Abort the entire experiment if Unity stops responding. "
                             "Note: currently has no effect; the run always aborts after 3 "
                             "consecutive ClearScene failures.")
    parser.add_argument("--frontier-model", type=str, default=None,
                        help="Forwarded to groq_static_test.py as --frontier-model. "
                             "If omitted, groq_static_test.py uses its own default "
                             "(currently gpt-5.5). Pass '' (empty string) to disable frontier "
                             "and use Llama-4-Scout for everything.")
    parser.add_argument("--include-phase2", action="store_true",
                        help="Run Phase 2 (interactive event loop) after Phase 1. "
                             "Without this flag, only Phase 1 + static Phase 2 evaluation runs.")
    parser.add_argument("--phase2-max-iterations", type=int, default=0,
                        help="Cap Phase 2 event-loop iterations (0 = no cap, only --timeout "
                             "stops it). Only applies when --include-phase2 is set.")
    parser.add_argument("--judge-model", type=str, default=None,
                        help="Override the LLM-judge model (e.g. 'gpt-5.5', 'gpt-5.2'). "
                             "Defaults to the provider's standard. Only applies when "
                             "--judge-provider=openai.")
    args = parser.parse_args()

    global RESULTS_DIR
    if args.results_dir:
        RESULTS_DIR = Path(args.results_dir).resolve()
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  Results directory: {RESULTS_DIR}")

    if args.scenarios:
        scenarios = [s.strip().upper() for s in args.scenarios.split(",")]
    else:
        scenarios = ALL_SCENARIOS

    if args.aggregate_only:
        _run_aggregation()
        return

    print(f"\n{'=' * 60}")
    print("  Experiment 1: Scene Generation Quality Evaluation")
    print(f"  Scenarios: {', '.join(scenarios)}")
    print(f"  Trials per scenario: {args.trials}")
    print(f"  Total runs: {len(scenarios) * args.trials}")
    print(f"{'=' * 60}\n")

    if not args.eval_only:
        print("  Pre-flight: checking Unity responsiveness...")
        if _preflight_unity_check():
            print("  Unity is alive. Starting experiment.\n")
        else:
            print("  [ERROR] Unity is NOT responding to commands.")
            print("    Ensure: Unity Editor open, scene in Play mode, AgentNetworkBridge active.")
            print("  Aborting. Fix Unity and re-run.")
            return

    run_results = []
    consecutive_no_unity = 0  # auto-abort after repeated ClearScene failures
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

            # --- Generation (skipped with --eval-only) ---
            if not args.eval_only:
                # ClearScene confirmation doubles as the per-trial Unity liveness check.
                clear_ok = _clear_unity_scene(
                    wait=args.clear_wait,
                    retries=args.clear_retries,
                    stabilize=args.stabilize,
                )
                _clear_api_files()

                if not clear_ok:
                    consecutive_no_unity += 1
                    if consecutive_no_unity >= MAX_CONSECUTIVE_NO_UNITY:
                        print(f"    [ABORT] ClearScene failed {consecutive_no_unity} "
                              f"consecutive times. Unity unresponsive.")
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
                    print("    [FAIL] Skipping evaluation for this trial")
                    run_results.append(
                        {"scenario": scenario_id, "trial": trial, "status": "FAILED"}
                    )
                    continue

                # Let Unity finish the last commands and write back executed flags
                # (avoids a file race with Phase 1's final write).
                time.sleep(3.0)

                # Wait for a screenshot of the final scene so we don't save stale
                # mid-trial images.
                print("    Waiting for fresh screenshots of final scene state...")
                got_fresh = _wait_for_fresh_screenshots(after_time=phase1_end, timeout=20.0)
                if not got_fresh:
                    print("    [WARN] Fresh screenshots not ready within 20s — collecting anyway")

                _collect_artifacts(trial_dir)
                print(f"    Artifacts collected to {trial_dir}")

            # --- Evaluation ---
            if (trial_dir / "scene_state.json").exists():
                report = _run_evaluation(trial_dir, scenario_file)
                if report:
                    score = report.get("composite_score", 0)
                    collision = report.get("geometric", {}).get("collision_rate", 0)
                    hit_rate = report.get("semantic", {}).get("prefab_hit_rate", 0)
                    print(f"    Composite: {score:.1f}  Collision: {collision:.3f}  "
                          f"Hit Rate: {hit_rate:.3f}")
                    run_results.append({
                        "scenario": scenario_id, "trial": trial,
                        "status": "OK", "composite": score,
                    })
                else:
                    run_results.append(
                        {"scenario": scenario_id, "trial": trial, "status": "EVAL_FAILED"}
                    )

                # Static Phase 2 metrics (interaction_map.json + story_chain.json).
                p2_report = _run_phase2_evaluation(trial_dir, scenario_file)
                if p2_report:
                    p2_score = p2_report.get("composite_score", 0)
                    cov = p2_report.get("coverage", {}).get("ambient_object_coverage", 0)
                    chain_len = p2_report.get("chain", {}).get("chain_length", 0)
                    beat_valid = p2_report.get("chain", {}).get("chain_beat_validity_rate", 0)
                    print(f"    Phase 2: composite={p2_score:.1f} coverage={cov:.2f} "
                          f"chain={chain_len}beats valid={beat_valid:.2f}")

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

            # Brief pause between trials to avoid API rate limits.
            if trial < args.trials:
                time.sleep(2)

    print("\n\n" + "=" * 60)
    print("  Running aggregation...")
    print("=" * 60)
    _run_aggregation()

    print(f"\n\n{'=' * 60}")
    print("  EXPERIMENT COMPLETE")
    print(f"{'=' * 60}")
    total = len(run_results)
    ok = sum(1 for r in run_results if r["status"] == "OK")
    failed = sum(1 for r in run_results if r["status"] == "FAILED")
    print(f"  Total runs: {total}")
    print(f"  Successful: {ok}")
    print(f"  Failed: {failed}")
    if ok > 0:
        avg_composite = sum(
            r.get("composite", 0) for r in run_results if r["status"] == "OK"
        ) / ok
        print(f"  Average composite score: {avg_composite:.1f}")
    print(f"\n  Results: {RESULTS_DIR}")
    print(f"  Reports: {SCRIPT_DIR / 'reports'}")
    print()


if __name__ == "__main__":
    main()
