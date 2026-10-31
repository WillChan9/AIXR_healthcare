"""
Evaluate Phase 2 (interaction layer) quality for a single trial.

Inputs (read from ``--results-dir``):
    interaction_map.json  ambient layer: per-object event -> animation / speech-bubble config
    story_chain.json      scripted layer: ordered beats (trigger + actions)
    scene_state.json      objects actually placed in Unity
    agent.log             used for runtime metrics, if Phase 2 actually ran
    unity_commands.json   used for command execution rate
plus the scenario's ``experience.txt`` (``--experience``) for room bounds,
character prefixes and the required character roster. Animator controller
names are checked against
``firefighter project/Assets/Resources/AnimatorControllers/*.controller``.

Output:
    A JSON report with ``coverage``, ``validity``, ``chain`` and ``runtime``
    sections and a 0-100 ``composite_score`` (static metrics only; runtime
    metrics are not part of the composite). Printed to stdout and optionally
    written to ``--output``. ``run_experiment.py`` saves it as
    ``phase2_eval_report.json``.

Usage:
    python evaluate_phase2.py --results-dir results/S03/trial_1 \
        --experience scenarios/experience_S03.txt \
        --output results/S03/trial_1/phase2_eval_report.json
"""

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Any:
    """Load JSON from ``path``; return None if the file is missing or empty."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return None
    return json.loads(text)


def _parse_experience_config(txt: str) -> dict:
    """Parse ``key = value`` lines from the ``[room_config]`` section of an experience file."""
    cfg: dict = {}
    in_section = False
    for line in txt.splitlines():
        line = line.strip()
        if line == "[room_config]":
            in_section = True
            continue
        if in_section:
            if line.startswith("["):
                break
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                cfg[key.strip()] = val.strip()
    return cfg


def _list_animator_controllers(repo_root: Path) -> Set[str]:
    """Return controller names (without ``.controller``) in Resources/AnimatorControllers."""
    ctrl_dir = repo_root / "firefighter project" / "Assets" / "Resources" / "AnimatorControllers"
    if not ctrl_dir.exists():
        return set()
    return {p.stem for p in ctrl_dir.glob("*.controller")}


# ---------------------------------------------------------------------------
# Scene + roster derivation
# ---------------------------------------------------------------------------

def _placed_object_names(scene_state: List[Dict]) -> Set[str]:
    """Names of every non-structural object actually placed in the scene."""
    names: Set[str] = set()
    for o in scene_state or []:
        n = (o.get("objectName") or "").strip()
        if not n or n == "Cube":
            continue
        names.add(n)
    return names


def _placed_character_names(scene_state: List[Dict], char_prefixes: List[str]) -> Set[str]:
    """Names of placed objects that start with one of the character prefixes."""
    chars: Set[str] = set()
    for o in scene_state or []:
        n = (o.get("objectName") or "").strip()
        if any(n.startswith(p) for p in char_prefixes):
            chars.add(n)
    return chars


def _required_characters(experience_txt: str, char_prefixes: List[str]) -> Set[str]:
    """Character names the scene should contain, per experience.txt.

    Collects ``<Prefix>_<digits>`` tokens (e.g. ``Doctor_01``) that appear before
    the ``[interactions]`` section.
    """
    if not char_prefixes:
        return set()
    name_re = re.compile(r'\b((?:' + '|'.join(re.escape(p) for p in char_prefixes) + r')_\d+)\b')
    found: Set[str] = set()
    for line in experience_txt.splitlines():
        if line.strip().startswith("[interactions]"):
            break
        for m in name_re.finditer(line):
            found.add(m.group(1))
    return found


# ---------------------------------------------------------------------------
# Section A: Coverage metrics (interaction_map.json)
# ---------------------------------------------------------------------------

def compute_coverage_metrics(
    interaction_map: Dict[str, Any],
    placed_names: Set[str],
    placed_chars: Set[str],
) -> Dict[str, float]:
    """How much of the placed scene the interaction map covers, and how richly.

    - ambient_object_coverage: placed objects with a map entry / placed objects
    - multi_event_pct: map entries with >= 2 event types
    - bubble_density: event configs with a ``bubble_message`` / all event configs
    - targeted_animation_pct: character event configs with ``target_character``
    """
    metrics: Dict[str, float] = {}

    # Strip Unity (Clone) suffix from map keys to match scene state.
    map_objects = {k.replace("(Clone)", "").strip(): v for k, v in (interaction_map or {}).items()}

    # ambient_object_coverage = entries / placed non-structural objects
    if placed_names:
        covered = sum(1 for n in placed_names if n in map_objects)
        metrics["ambient_object_coverage"] = covered / len(placed_names)
    else:
        metrics["ambient_object_coverage"] = 0.0
    metrics["map_entry_count"] = len(map_objects)
    metrics["placed_object_count"] = len(placed_names)

    # multi_event_pct = entries with >= 2 distinct event types
    if map_objects:
        multi = sum(1 for ev in map_objects.values() if isinstance(ev, dict) and len(ev) >= 2)
        metrics["multi_event_pct"] = multi / len(map_objects)
    else:
        metrics["multi_event_pct"] = 0.0

    # bubble_density = config entries with bubble_message / total entries
    total_entries = 0
    bubble_entries = 0
    for ev_map in map_objects.values():
        if not isinstance(ev_map, dict):
            continue
        for cfg in ev_map.values():
            if not isinstance(cfg, dict):
                continue
            total_entries += 1
            if cfg.get("bubble_message"):
                bubble_entries += 1
    metrics["total_event_entries"] = total_entries
    metrics["entries_with_bubble"] = bubble_entries
    metrics["bubble_density"] = bubble_entries / total_entries if total_entries else 0.0

    # targeted_animation_pct = character entries with target_character set / character entries
    char_entries = 0
    targeted = 0
    for name, ev_map in map_objects.items():
        if name not in placed_chars:
            continue
        if not isinstance(ev_map, dict):
            continue
        for cfg in ev_map.values():
            if not isinstance(cfg, dict):
                continue
            char_entries += 1
            if cfg.get("target_character"):
                targeted += 1
    metrics["character_entry_count"] = char_entries
    metrics["targeted_animation_pct"] = targeted / char_entries if char_entries else 0.0

    return metrics


# ---------------------------------------------------------------------------
# Section B: Validity metrics (interaction_map.json)
# ---------------------------------------------------------------------------

def compute_map_validity_metrics(
    interaction_map: Dict[str, Any],
    placed_names: Set[str],
    placed_chars: Set[str],
    available_controllers: Set[str],
    cfg: dict,
) -> Dict[str, float]:
    """Check that interaction-map references resolve to real assets/objects.

    Each rate is valid / present for configs that set the field (1.0 if none do):
    controller exists, state name equals the controller stem, target character
    is placed, ``bubble_on`` refers to a placed object. ``cfg`` is accepted for
    signature symmetry with ``compute_chain_metrics`` but unused (the map has
    no coordinates).
    """
    metrics: Dict[str, float] = {}

    total = 0
    valid_controller = 0
    valid_state = 0
    valid_target = 0
    valid_bubble_on = 0
    has_controller = 0
    has_target = 0
    has_bubble_on = 0

    map_objects = {k.replace("(Clone)", "").strip(): v for k, v in (interaction_map or {}).items()}

    for ev_map in map_objects.values():
        if not isinstance(ev_map, dict):
            continue
        for cfg_dict in ev_map.values():
            if not isinstance(cfg_dict, dict):
                continue
            total += 1

            ctrl = cfg_dict.get("controller") or cfg_dict.get("controllerPath")
            if ctrl:
                has_controller += 1
                # Accept both "AnimatorControllers/HumanM@Talking" and "HumanM@Talking";
                # validity is checked against the available controller stems.
                stem = ctrl.split("/")[-1] if "/" in ctrl else ctrl
                if stem in available_controllers:
                    valid_controller += 1
                    state = cfg_dict.get("state") or cfg_dict.get("stateName") or ""
                    if state == stem:
                        valid_state += 1

            tgt = cfg_dict.get("target_character") or cfg_dict.get("target")
            if tgt:
                has_target += 1
                if tgt in placed_chars:
                    valid_target += 1

            bub_on = cfg_dict.get("bubble_on")
            if bub_on:
                has_bubble_on += 1
                if bub_on in placed_names or bub_on in placed_chars:
                    valid_bubble_on += 1

    metrics["map_total_entries"] = total
    metrics["map_controller_validity_rate"] = (
        valid_controller / has_controller if has_controller else 1.0
    )
    metrics["map_state_prefix_validity"] = valid_state / has_controller if has_controller else 1.0
    metrics["map_target_validity_rate"] = valid_target / has_target if has_target else 1.0
    metrics["map_bubble_on_validity"] = (
        valid_bubble_on / has_bubble_on if has_bubble_on else 1.0
    )

    return metrics


# ---------------------------------------------------------------------------
# Section C: Story chain quality (story_chain.json)
# ---------------------------------------------------------------------------

def compute_chain_metrics(
    chain: Dict[str, Any],
    placed_names: Set[str],
    placed_chars: Set[str],
    required_chars: Set[str],
    available_controllers: Set[str],
    cfg: dict,
) -> Dict[str, float]:
    """Score the story chain's structure and the validity of its references.

    A beat is valid when its trigger object is placed and every action target
    resolves (characters must be placed; WalkCharacterTo controllers must exist).
    Also reports action richness, trigger validity, WalkCharacterTo coordinates
    within room bounds, state-name == controller-stem consistency, and coverage
    of the required character roster.
    """
    metrics: Dict[str, float] = {}

    beats = (chain or {}).get("beats", []) or []
    metrics["chain_length"] = len(beats)
    if not beats:
        metrics.update({
            "chain_beat_validity_rate": 0.0,
            "chain_action_richness": 0.0,
            "chain_unique_action_types": 0,
            "chain_char_coverage": 0.0,
            "chain_trigger_validity_rate": 0.0,
            "chain_walk_coord_validity": 1.0,
            "chain_state_prefix_validity": 1.0,
        })
        return metrics

    x_min = float(cfg.get("x_min", -3.8))
    x_max = float(cfg.get("x_max", 3.8))
    z_min = float(cfg.get("z_min", 0.1))
    z_max = float(cfg.get("z_max", 7.9))

    valid_beats = 0
    valid_triggers = 0
    total_actions = 0
    action_kinds: Counter = Counter()
    referenced_chars: Set[str] = set()

    walk_total = 0
    walk_valid_coords = 0
    state_total = 0
    state_valid_prefix = 0

    for beat in beats:
        trig = beat.get("trigger") or {}
        trig_obj = trig.get("object")
        beat_valid = True

        if trig_obj and (trig_obj in placed_names or trig_obj in placed_chars):
            valid_triggers += 1
        else:
            beat_valid = False

        for act in beat.get("actions") or []:
            kind = act.get("action")
            if not kind:
                continue
            total_actions += 1
            action_kinds[kind] += 1

            if kind == "PlayHumanAnimationFromResources":
                target = act.get("targetModel")
                if target:
                    if target in placed_chars:
                        referenced_chars.add(target)
                    else:
                        beat_valid = False
                ctrl = act.get("controllerPathInResources") or act.get("controllerPath") or ""
                state = act.get("stateName") or ""
                stem = ctrl.split("/")[-1] if "/" in ctrl else ctrl
                if stem and state:
                    state_total += 1
                    if state == stem:
                        state_valid_prefix += 1

            elif kind == "WalkCharacterTo":
                target = act.get("objectName") or act.get("humanName")
                if target:
                    if target in placed_chars:
                        referenced_chars.add(target)
                    else:
                        beat_valid = False
                walk_total += 1
                x = float(act.get("x", 0))
                z = float(act.get("z", 0))
                if x_min <= x <= x_max and z_min <= z <= z_max:
                    walk_valid_coords += 1
                ctrl = act.get("controllerPathInResources") or act.get("controllerPath") or ""
                stem = ctrl.split("/")[-1] if "/" in ctrl else ctrl
                if stem and stem not in available_controllers:
                    beat_valid = False

            elif kind in ("ShowSpeechBubble", "HighlightObject", "ClearHighlight", "MoveObject"):
                target = act.get("objectName")
                if target and not (target in placed_names or target in placed_chars):
                    beat_valid = False

        if beat_valid:
            valid_beats += 1

    metrics["chain_beat_validity_rate"] = valid_beats / len(beats)
    metrics["chain_trigger_validity_rate"] = valid_triggers / len(beats)
    metrics["chain_action_richness"] = total_actions / len(beats)
    metrics["chain_unique_action_types"] = len(action_kinds)
    metrics["chain_total_actions"] = total_actions
    metrics["chain_walk_coord_validity"] = walk_valid_coords / walk_total if walk_total else 1.0
    metrics["chain_state_prefix_validity"] = (
        state_valid_prefix / state_total if state_total else 1.0
    )

    if required_chars:
        metrics["chain_char_coverage"] = (
            len(referenced_chars & required_chars) / len(required_chars)
        )
    else:
        metrics["chain_char_coverage"] = 1.0

    return metrics


# ---------------------------------------------------------------------------
# Section D: Runtime metrics (only meaningful when Phase 2 actually ran)
# ---------------------------------------------------------------------------

_LOG_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


def _parse_log_timestamp(line: str) -> Optional[datetime]:
    """Parse the leading ``YYYY-MM-DD HH:MM:SS`` timestamp of a log line, if any."""
    m = _LOG_TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def compute_runtime_metrics(log_path: Path, commands_path: Path,
                            chain_total_beats: int) -> Dict[str, float]:
    """Compute Phase 2 runtime metrics from agent.log and unity_commands.json.

    Only meaningful when Phase 2 actually ran (the log contains a "PHASE 2:"
    marker). Otherwise returns all-zero metrics with ``phase2_ran = 0``.
    """
    metrics: Dict[str, float] = {
        "phase2_ran": 0,
        "phase2_duration_seconds": 0.0,
        "chain_beats_fired": 0,
        "chain_progression": 0.0,
        "time_to_first_beat_seconds": 0.0,
        "time_to_complete_chain_seconds": 0.0,
        "total_events_handled": 0,
        "ambient_animations_fired": 0,
        "story_chain_dispatches": 0,
        "command_execution_rate": 0.0,
        "dispatch_failure_rate": 0.0,
        "unmapped_event_rate": 0.0,
    }

    # --- agent.log ---
    if not log_path.exists():
        return metrics
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return metrics
    lines = log_text.splitlines()

    # Find Phase 2 start marker
    p2_start_idx = next((i for i, l in enumerate(lines) if "PHASE 2:" in l), -1)
    if p2_start_idx < 0:
        return metrics  # Phase 2 never entered
    metrics["phase2_ran"] = 1

    p2_lines = lines[p2_start_idx:]
    p2_start_ts = next((_parse_log_timestamp(l) for l in p2_lines if _parse_log_timestamp(l)), None)
    p2_end_ts = None
    for l in reversed(p2_lines):
        ts = _parse_log_timestamp(l)
        if ts is not None:
            p2_end_ts = ts
            break
    if p2_start_ts and p2_end_ts:
        metrics["phase2_duration_seconds"] = round((p2_end_ts - p2_start_ts).total_seconds(), 2)

    # Chain progression: count "Firing beat" lines + first / last beat timestamps
    fire_re = re.compile(r"\[StoryChain\] Firing beat (\S+)")
    fire_times: List[datetime] = []
    fired_beat_ids: List[str] = []
    for l in p2_lines:
        m = fire_re.search(l)
        if m:
            fired_beat_ids.append(m.group(1))
            ts = _parse_log_timestamp(l)
            if ts:
                fire_times.append(ts)

    metrics["chain_beats_fired"] = len(fired_beat_ids)
    if chain_total_beats > 0:
        metrics["chain_progression"] = min(len(fired_beat_ids) / chain_total_beats, 1.0)
    elif fired_beat_ids:
        metrics["chain_progression"] = 1.0

    if fire_times and p2_start_ts:
        metrics["time_to_first_beat_seconds"] = round(
            (fire_times[0] - p2_start_ts).total_seconds(), 2
        )
        if len(fire_times) >= 2:
            metrics["time_to_complete_chain_seconds"] = round(
                (fire_times[-1] - fire_times[0]).total_seconds(), 2
            )

    # Ambient event lines handled successfully (excluding unmapped/silent/repeat events).
    ambient_re = re.compile(
        r"\[(touch|hover_enter|grab|release|activate)\]\s+\S+\s+\(\w+\)\s*[—\-→]"
    )
    ambient_lines = [l for l in p2_lines if ambient_re.search(l)
                     and "no animation mapped" not in l
                     and "silent event type" not in l
                     and "repeat #" not in l]
    metrics["total_events_handled"] = len(ambient_lines)

    fired_anim = sum(1 for l in p2_lines
                     if "InteractionAnimations" in l and "anim=" in l and "anim=-" not in l)
    metrics["ambient_animations_fired"] = fired_anim

    sc_dispatch = sum(1 for l in p2_lines if "[StoryChain] dispatched" in l)
    metrics["story_chain_dispatches"] = sc_dispatch

    # Unmapped event rate: "no animation mapped" lines / all ambient event lines.
    unmapped = sum(1 for l in p2_lines if "no animation mapped" in l and "(touch)" not in l)
    raw_event_lines = [l for l in p2_lines if ambient_re.search(l)]
    metrics["unmapped_event_rate"] = (
        unmapped / len(raw_event_lines) if raw_event_lines else 0.0
    )

    # --- unity_commands.json: execution rate ---
    if commands_path.exists():
        try:
            cmds = json.loads(commands_path.read_text(encoding="utf-8-sig"))
            if isinstance(cmds, list) and cmds:
                executed = sum(1 for c in cmds if c.get("executed"))
                metrics["command_execution_rate"] = round(executed / len(cmds), 4)
                metrics["dispatch_failure_rate"] = round(1.0 - executed / len(cmds), 4)
        except Exception:
            pass

    return metrics


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

def compute_composite(coverage: Dict, validity: Dict, chain: Dict) -> float:
    """Return the 0-100 Phase 2 composite score.

    composite = 30% coverage + 30% map validity + 40% chain quality, where each
    component is itself a weighted mean of its metrics (see below). The chain
    component is 0 when the story chain is empty.
    """
    cov = (
        coverage.get("ambient_object_coverage", 0) * 0.4
        + coverage.get("bubble_density", 0)        * 0.3
        + coverage.get("multi_event_pct", 0)       * 0.15
        + coverage.get("targeted_animation_pct", 0) * 0.15
    )
    val = (
        validity.get("map_controller_validity_rate", 0) * 0.3
        + validity.get("map_state_prefix_validity", 0)  * 0.2
        + validity.get("map_target_validity_rate", 0)   * 0.3
        + validity.get("map_bubble_on_validity", 0)     * 0.2
    )
    chain_len = chain.get("chain_length", 0)
    chain_score = 0.0
    if chain_len > 0:
        chain_score = (
            chain.get("chain_beat_validity_rate", 0)     * 0.3
            + chain.get("chain_trigger_validity_rate", 0) * 0.2
            + chain.get("chain_walk_coord_validity", 0)   * 0.15
            + chain.get("chain_state_prefix_validity", 0) * 0.15
            + chain.get("chain_char_coverage", 0)         * 0.2
        )

    composite = (cov * 0.30 + val * 0.30 + chain_score * 0.40) * 100
    return round(composite, 2)


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate(results_dir: Path, experience_path: Optional[Path] = None) -> Dict[str, Any]:
    """Evaluate one trial directory and return the full Phase 2 report dict."""
    repo_root = Path(__file__).resolve().parents[2]

    map_path = results_dir / "interaction_map.json"
    chain_path = results_dir / "story_chain.json"
    scene_path = results_dir / "scene_state.json"

    interaction_map = _load_json(map_path) or {}
    chain = _load_json(chain_path) or {}
    scene_state = _load_json(scene_path) or []

    cfg: dict = {}
    char_prefixes: List[str] = ["Doctor", "Nurse", "Patient", "Surgeon", "EMT"]
    required_chars: Set[str] = set()
    if experience_path and experience_path.exists():
        exp_text = experience_path.read_text(encoding="utf-8")
        cfg = _parse_experience_config(exp_text)
        if cfg.get("character_prefixes"):
            char_prefixes = [p.strip() for p in cfg["character_prefixes"].split(",") if p.strip()]
        required_chars = _required_characters(exp_text, char_prefixes)

    placed_names = _placed_object_names(scene_state)
    placed_chars = _placed_character_names(scene_state, char_prefixes)
    available_controllers = _list_animator_controllers(repo_root)

    coverage = compute_coverage_metrics(interaction_map, placed_names, placed_chars)
    validity = compute_map_validity_metrics(interaction_map, placed_names, placed_chars,
                                            available_controllers, cfg)
    chain_m = compute_chain_metrics(chain, placed_names, placed_chars, required_chars,
                                    available_controllers, cfg)
    runtime = compute_runtime_metrics(
        log_path=results_dir / "agent.log",
        commands_path=results_dir / "unity_commands.json",
        chain_total_beats=int(chain_m.get("chain_length", 0)),
    )

    report: Dict[str, Any] = {
        "results_dir": str(results_dir),
        "experience_file": str(experience_path) if experience_path else None,
        "scene_type": cfg.get("scene_type", "unknown"),
        "required_characters_count": len(required_chars),
        "placed_characters_count": len(placed_chars),
    }
    report["coverage"] = coverage
    report["validity"] = validity
    report["chain"] = chain_m
    report["runtime"] = runtime
    report["composite_score"] = compute_composite(coverage, validity, chain_m)
    return report


def print_report(report: Dict[str, Any]) -> None:
    """Pretty-print a Phase 2 evaluation report."""
    print(f"\n{'=' * 60}")
    print(f"  Phase 2 Evaluation: {report.get('scene_type', 'unknown')}")
    print(f"  Results dir: {report.get('results_dir', 'N/A')}")
    print(f"{'=' * 60}")
    print(f"\n  COMPOSITE SCORE: {report.get('composite_score', 0):.1f} / 100\n")
    for section in ("coverage", "validity", "chain", "runtime"):
        sec = report.get(section, {})
        print(f"  --- {section.upper()} ---")
        for k, v in sec.items():
            if isinstance(v, float):
                print(f"    {k:35s} {v:.4f}")
            else:
                print(f"    {k:35s} {v}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Evaluate Phase 2 interaction quality")
    parser.add_argument("--results-dir", type=Path, required=True,
                        help="Directory containing interaction_map.json + story_chain.json "
                             "+ scene_state.json")
    parser.add_argument("--experience", type=Path, default=None,
                        help="Path to the experience.txt used for this run")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write JSON report to this file")
    args = parser.parse_args()

    report = evaluate(args.results_dir, args.experience)
    print_report(report)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Report saved to: {args.output}")


if __name__ == "__main__":
    main()
