"""
Evaluate Phase 1 scene generation quality from output artifacts.

Computes geometric, semantic (automated), plan-completion, and efficiency metrics
from scene_state.json, reflection.json, agent.log, and unity_commands.json.

Usage:
    python evaluate_scene.py --results-dir path/to/run_output [--experience path/to/experience.txt]
"""

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return None
    return json.loads(text)


def _parse_experience_config(txt: str) -> dict:
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


def _clean_description(txt: str) -> str:
    idx = txt.find("[room_config]")
    return txt[:idx].strip() if idx != -1 else txt.strip()


def _extract_required_objects(description: str) -> List[str]:
    """Extract object names referenced after 'REQUIRED OBJECTS' in the description."""
    required: List[str] = []
    # Match patterns like "Treadmill_01", "Bed_01", etc.
    # Look for lines with numbered items containing prefab-like names
    pattern = re.compile(r'\b([A-Z][a-zA-Z]*(?:_[A-Za-z0-9]+)+)\b')
    in_required = False
    for line in description.splitlines():
        if "REQUIRED" in line.upper() and "OBJECT" in line.upper():
            in_required = True
            continue
        if in_required:
            if line.strip() == "" or line.strip().startswith("Staff") or line.strip().startswith("Room exterior"):
                in_required = False
                continue
            matches = pattern.findall(line)
            for m in matches:
                if m not in required:
                    required.append(m)
    return required


# ---------------------------------------------------------------------------
# Geometric Metrics
# ---------------------------------------------------------------------------

def _aabb_overlap(a: Dict, b: Dict) -> bool:
    """Check if two objects' AABB bounding boxes overlap."""
    return (
        a["boundsMinX"] < b["boundsMaxX"] and a["boundsMaxX"] > b["boundsMinX"] and
        a["boundsMinY"] < b["boundsMaxY"] and a["boundsMaxY"] > b["boundsMinY"] and
        a["boundsMinZ"] < b["boundsMaxZ"] and a["boundsMaxZ"] > b["boundsMinZ"]
    )


def _center_dist(a: Dict, b: Dict) -> float:
    return math.sqrt(
        (a["x"] - b["x"]) ** 2 +
        (a["y"] - b["y"]) ** 2 +
        (a["z"] - b["z"]) ** 2
    )


def _xz_dist(a: Dict, b: Dict) -> float:
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["z"] - b["z"]) ** 2)


def compute_geometric_metrics(
    scene_state: List[Dict],
    cfg: dict,
) -> Dict[str, float]:
    """Compute all geometric quality metrics from scene_state.json objects."""

    x_min = float(cfg.get("x_min", -2.8))
    x_max = float(cfg.get("x_max", 2.8))
    z_min = float(cfg.get("z_min", 0.1))
    z_max = float(cfg.get("z_max", 5.9))
    room_w = float(cfg.get("room_width", 6))
    room_d = float(cfg.get("room_depth", 6))

    # Filter out structural Cubes (walls/floor/ceiling)
    objects = [
        obj for obj in scene_state
        if obj.get("objectName", "") != "Cube"
    ]

    n = len(objects)
    metrics: Dict[str, float] = {"object_count": n}

    if n == 0:
        metrics.update({
            "collision_rate": 0.0,
            "oob_rate": 0.0,
            "mean_nn_distance": 0.0,
            "min_pairwise_distance": 0.0,
            "floor_coverage": 0.0,
            "height_violation_rate": 0.0,
        })
        return metrics

    # --- Collision Rate ---
    collision_pairs = 0
    total_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total_pairs += 1
            if _aabb_overlap(objects[i], objects[j]):
                collision_pairs += 1
    metrics["collision_count"] = collision_pairs
    metrics["collision_rate"] = collision_pairs / total_pairs if total_pairs > 0 else 0.0

    # --- Out-of-Bounds Rate ---
    oob_count = 0
    for obj in objects:
        cx, cz = obj["x"], obj["z"]
        if cx < x_min or cx > x_max or cz < z_min or cz > z_max:
            oob_count += 1
    metrics["oob_count"] = oob_count
    metrics["oob_rate"] = oob_count / n

    # --- Nearest Neighbor Distance (XZ plane) ---
    nn_distances = []
    for i in range(n):
        min_d = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = _xz_dist(objects[i], objects[j])
            if d < min_d:
                min_d = d
        nn_distances.append(min_d if min_d < float("inf") else 0.0)
    metrics["mean_nn_distance"] = sum(nn_distances) / len(nn_distances) if nn_distances else 0.0
    metrics["std_nn_distance"] = (
        math.sqrt(sum((d - metrics["mean_nn_distance"]) ** 2 for d in nn_distances) / len(nn_distances))
        if nn_distances else 0.0
    )

    # --- Min Pairwise Distance ---
    min_pw = float("inf")
    for i in range(n):
        for j in range(i + 1, n):
            d = _xz_dist(objects[i], objects[j])
            if d < min_pw:
                min_pw = d
    metrics["min_pairwise_distance"] = min_pw if min_pw < float("inf") else 0.0

    # --- Floor Coverage (union of XZ footprints / room area) ---
    # Approximate: sum of individual footprint areas (overestimates if overlapping)
    room_area = room_w * room_d
    total_footprint = 0.0
    for obj in objects:
        sx = obj.get("boundsSizeX", 0.5)
        sz = obj.get("boundsSizeZ", 0.5)
        total_footprint += sx * sz
    metrics["floor_coverage"] = min(total_footprint / room_area, 1.0) if room_area > 0 else 0.0

    # --- Height Violation Rate ---
    # Small props (defined by keywords) should be on surfaces (y > 0.3), not on floor
    small_keywords = {"folder", "syringe", "scalpel", "forceps", "bottle", "tray",
                      "stethoscope", "thermometer", "otoscope", "keyboard", "mouse",
                      "copybook", "glasses", "bandage", "bowl", "mask_box", "aid_kit",
                      "scissors", "neuro_hammer", "dental"}
    small_props = [
        obj for obj in objects
        if any(kw in obj.get("objectName", "").lower() for kw in small_keywords)
    ]
    height_violations = sum(1 for obj in small_props if obj.get("y", 0) < 0.3)
    metrics["small_prop_count"] = len(small_props)
    metrics["height_violation_count"] = height_violations
    metrics["height_violation_rate"] = (
        height_violations / len(small_props) if small_props else 0.0
    )

    return metrics


# ---------------------------------------------------------------------------
# Semantic Metrics (automated, no LLM)
# ---------------------------------------------------------------------------

def compute_semantic_metrics(
    scene_state: List[Dict],
    commands: List[Dict],
    description: str,
    prefab_list: Optional[List[str]] = None,
) -> Dict[str, float]:
    """Compute automated semantic metrics."""

    objects = [
        obj for obj in scene_state
        if obj.get("objectName", "") != "Cube"
    ]
    object_names = [obj.get("objectName", "") for obj in objects]

    metrics: Dict[str, float] = {}

    # --- Prefab Hit Rate ---
    required = _extract_required_objects(description)
    if required:
        hits = sum(1 for r in required if any(r in name for name in object_names))
        metrics["required_object_count"] = len(required)
        metrics["required_objects_placed"] = hits
        metrics["prefab_hit_rate"] = hits / len(required)
    else:
        metrics["required_object_count"] = 0
        metrics["required_objects_placed"] = 0
        metrics["prefab_hit_rate"] = 1.0  # no requirements = vacuously true

    # --- Prefab Name Validity ---
    if prefab_list:
        prefab_set = set(prefab_list)
        place_cmds = [
            c for c in commands
            if c.get("action") in ("PlaceObject", "PlaceHuman")
        ]
        valid_names = 0
        total_placements = len(place_cmds)
        for cmd in place_cmds:
            name = cmd.get("objectName") or cmd.get("humanName", "")
            if name in prefab_set:
                valid_names += 1
        metrics["total_placements"] = total_placements
        metrics["valid_name_count"] = valid_names
        metrics["prefab_name_validity"] = valid_names / total_placements if total_placements > 0 else 1.0

    # --- Duplicate Object Rate ---
    name_counts = Counter(object_names)
    duplicates = sum(count - 1 for count in name_counts.values() if count > 1)
    metrics["duplicate_count"] = duplicates
    metrics["duplicate_rate"] = duplicates / len(object_names) if object_names else 0.0

    # --- Character Count ---
    char_prefixes = ("doctor", "nurse", "patient", "emt", "surgeon", "infectiologist")
    characters = [
        name for name in object_names
        if any(name.lower().startswith(p) for p in char_prefixes)
    ]
    metrics["character_count"] = len(characters)

    return metrics


# ---------------------------------------------------------------------------
# Plan Completion Metrics
# ---------------------------------------------------------------------------

def compute_plan_metrics(reflection_data: List[Dict]) -> Dict[str, float]:
    """Compute plan completion metrics from reflection.json."""
    metrics: Dict[str, float] = {}

    if not reflection_data:
        metrics.update({
            "total_plan_steps": 0,
            "steps_completed": 0,
            "step_completion_rate": 0.0,
            "total_reflection_iterations": 0,
            "avg_iterations_per_step": 0.0,
            "total_corrections": 0,
        })
        return metrics

    # Group reflections by step
    step_results: Dict[int, List[Dict]] = {}
    for entry in reflection_data:
        step_num = entry.get("plan_step", {}).get("step", 0)
        step_results.setdefault(step_num, []).append(entry)

    total_steps = len(step_results)
    completed_steps = 0
    total_iterations = len(reflection_data)
    total_corrections = 0

    for step_num, entries in step_results.items():
        # Check if any entry for this step has step_complete="yes"
        if any(e.get("reflection", {}).get("step_complete") == "yes" for e in entries):
            completed_steps += 1
        # Count corrections
        for entry in entries:
            suggestions = entry.get("reflection", {}).get("alignment_suggestions", "")
            total_corrections += suggestions.count("MoveObject")
            total_corrections += suggestions.count("DeleteObject")

    metrics["total_plan_steps"] = total_steps
    metrics["steps_completed"] = completed_steps
    metrics["step_completion_rate"] = completed_steps / total_steps if total_steps > 0 else 0.0
    metrics["total_reflection_iterations"] = total_iterations
    metrics["avg_iterations_per_step"] = total_iterations / total_steps if total_steps > 0 else 0.0
    metrics["total_corrections"] = total_corrections

    # Room status distribution
    statuses = [e.get("reflection", {}).get("room_status", "unknown") for e in reflection_data]
    for status in ("sparse", "good", "cluttered"):
        metrics[f"room_status_{status}_count"] = statuses.count(status)

    return metrics


# ---------------------------------------------------------------------------
# Efficiency Metrics
# ---------------------------------------------------------------------------

_LOG_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


def _extract_phase1_duration(log_text: str) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """Find Phase 1 start/end timestamps in agent.log and return (duration_seconds, start, end).

    Start marker: line containing "PHASE 1: Static Environment Generation".
    End markers (whichever appears last): "Step complete — all", "Total objects placed:",
    "--phase1-only flag set", or the start of "PHASE 2".
    Falls back to the last timestamped line if no end marker is found.
    """
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    last_dt: Optional[datetime] = None

    end_markers = (
        "Step complete — all",
        "Step complete - all",
        "Total objects placed:",
        "--phase1-only flag set",
        "PHASE 2:",
    )

    for line in log_text.splitlines():
        m = _LOG_TS_RE.match(line)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        last_dt = dt
        if start_dt is None and "PHASE 1: Static Environment Generation" in line:
            start_dt = dt
            continue
        if start_dt is not None and any(mk in line for mk in end_markers):
            end_dt = dt  # keep updating to capture the last matching marker

    if start_dt is None:
        return None, None, None
    final_end = end_dt or last_dt
    if final_end is None or final_end < start_dt:
        return None, start_dt.isoformat(sep=" "), None
    duration = (final_end - start_dt).total_seconds()
    return duration, start_dt.isoformat(sep=" "), final_end.isoformat(sep=" ")


def compute_efficiency_metrics(
    log_path: Path,
    commands: List[Dict],
) -> Dict[str, float]:
    """Compute efficiency metrics from agent.log and unity_commands.json."""
    metrics: Dict[str, float] = {}

    # --- Count LLM calls and timing from log ---
    llm_calls = 0
    screenshot_wait_total = 0.0

    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        # Count Groq API calls
        llm_calls = log_text.count("GROQ RAW RESPONSE")

        # Count screenshot waits
        wait_matches = re.findall(r'Waiting up to (\d+)s for Unity', log_text)
        screenshot_wait_total = sum(int(w) for w in wait_matches)

        # Count fill commands
        fill_matches = re.findall(r'Appended (\d+) fill commands', log_text)
        metrics["fill_commands_total"] = sum(int(f) for f in fill_matches)

        # Count dropped prefabs
        metrics["dropped_prefabs"] = log_text.count("Dropping PlaceObject") + log_text.count("Dropping PlaceHuman")

        # --- Phase 1 wall-clock duration (from log timestamps) ---
        duration, start_ts, end_ts = _extract_phase1_duration(log_text)
        if duration is not None:
            metrics["phase1_duration_seconds"] = round(duration, 2)
            metrics["phase1_start_time"] = start_ts
            metrics["phase1_end_time"] = end_ts
        else:
            metrics["phase1_duration_seconds"] = 0.0

    metrics["llm_calls"] = llm_calls
    metrics["screenshot_wait_seconds"] = screenshot_wait_total

    # --- Command counts ---
    if commands:
        action_counts = Counter(c.get("action", "") for c in commands)
        metrics["total_commands"] = len(commands)
        metrics["place_object_count"] = action_counts.get("PlaceObject", 0)
        metrics["place_human_count"] = action_counts.get("PlaceHuman", 0)
        metrics["move_object_count"] = action_counts.get("MoveObject", 0)
        metrics["delete_object_count"] = action_counts.get("DeleteObject", 0)
        metrics["create_object_count"] = action_counts.get("CreateObjectByName", 0)

    return metrics


# ---------------------------------------------------------------------------
# Main Evaluation
# ---------------------------------------------------------------------------

def evaluate(results_dir: Path, experience_path: Optional[Path] = None) -> Dict[str, Any]:
    """Run full evaluation on a single experiment run."""

    # Load artifacts
    scene_state_path = results_dir / "scene_state.json"
    reflection_path = results_dir / "reflection.json"
    commands_path = results_dir / "unity_commands.json"
    log_path = results_dir / "agent.log"

    scene_state = _load_json(scene_state_path) or []
    reflection_data = _load_json(reflection_path) or []
    commands = _load_json(commands_path) or []

    # Load experience description + config
    cfg = {}
    description = ""
    if experience_path and experience_path.exists():
        exp_text = experience_path.read_text(encoding="utf-8")
        cfg = _parse_experience_config(exp_text)
        description = _clean_description(exp_text)

    # Compute all metric groups
    report: Dict[str, Any] = {
        "results_dir": str(results_dir),
        "experience_file": str(experience_path) if experience_path else None,
        "scene_type": cfg.get("scene_type", "unknown"),
    }

    report["geometric"] = compute_geometric_metrics(scene_state, cfg)
    report["semantic"] = compute_semantic_metrics(scene_state, commands, description)
    report["plan"] = compute_plan_metrics(reflection_data)
    report["efficiency"] = compute_efficiency_metrics(log_path, commands)

    # Compute composite score (0-100)
    g = report["geometric"]
    s = report["semantic"]
    p = report["plan"]

    collision_score = max(0, 1.0 - g.get("collision_rate", 0) * 10)  # penalize heavily
    oob_score = max(0, 1.0 - g.get("oob_rate", 0) * 5)
    hit_score = s.get("prefab_hit_rate", 0)
    plan_score = p.get("step_completion_rate", 0)
    height_score = max(0, 1.0 - g.get("height_violation_rate", 0))

    composite = (
        collision_score * 0.25 +
        oob_score * 0.15 +
        hit_score * 0.30 +
        plan_score * 0.20 +
        height_score * 0.10
    ) * 100

    report["composite_score"] = round(composite, 2)

    return report


def print_report(report: Dict[str, Any]) -> None:
    """Pretty-print an evaluation report."""
    print(f"\n{'=' * 60}")
    print(f"  Scene Evaluation: {report.get('scene_type', 'unknown')}")
    print(f"  Results dir: {report.get('results_dir', 'N/A')}")
    print(f"{'=' * 60}")

    print(f"\n  COMPOSITE SCORE: {report.get('composite_score', 0):.1f} / 100\n")

    for section_name in ("geometric", "semantic", "plan", "efficiency"):
        section = report.get(section_name, {})
        print(f"  --- {section_name.upper()} ---")
        for key, val in section.items():
            if isinstance(val, float):
                print(f"    {key:35s} {val:.4f}")
            else:
                print(f"    {key:35s} {val}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Evaluate Phase 1 scene generation quality")
    parser.add_argument("--results-dir", type=Path, required=True,
                        help="Directory containing scene_state.json, reflection.json, agent.log, unity_commands.json")
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
