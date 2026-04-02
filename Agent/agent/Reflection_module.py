import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple

from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv()

try:
    from .logger import get_logger
except ImportError:
    from agent.logger import get_logger

logger = get_logger("ReflectionModule", module_log_file="api_files/reflection_log.log")

@dataclass
class ReflectionResult:
    step_complete: str
    current_action_thoughts: str
    next_action_thoughts: str
    alignment_suggestions: str = ""
    layout_comments: str = ""
    room_status: str = "good"       # "sparse" | "good" | "cluttered"
    suggest_additions: bool = False  # True only when room_status == "sparse"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_complete": self.step_complete,
            "current_action_thoughts": self.current_action_thoughts,
            "next_action_thoughts": self.next_action_thoughts,
            "alignment_suggestions": self.alignment_suggestions,
            "layout_comments": self.layout_comments,
            "room_status": self.room_status,
            "suggest_additions": self.suggest_additions,
        }

class ReflectionModule:
    # Maximum number of reflection iterations per plan step before forcing step_complete=yes
    MAX_REFLECTIONS_PER_STEP = 3

    def __init__(self):
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")
        self.groq_client = Groq(api_key=groq_api_key)

        self.actions_file = "api_files/actions.json"
        self.plan_file = "api_files/plan.json"
        self.reflection_file = "api_files/reflection.json"

        # Track reflection counts per step to enforce iteration limit
        self._step_reflection_counts: Dict[str, int] = {}

        # Shared state set by reflect_step before threads launch — threads read these directly
        self._view_data: list = []          # [(label, base64_encoded), ...]
        self._exact_scene_names: List[str] = []  # authoritative prefab names from unity_commands.json

        # Derive the 3-view screenshot base path from env var (strip .png if present)
        raw_path = os.getenv("UNITY_SCREENSHOT_PATH", "api_files/scene_screenshot.png")
        base = raw_path[:-4] if raw_path.endswith(".png") else raw_path
        self.screenshot_xz = base + "_xz.png"  # top-down
        self.screenshot_xy = base + "_xy.png"  # front
        self.screenshot_yz = base + "_yz.png"  # side

    def reset_step_count(self, step_key: str = None):
        """Reset reflection iteration count for a step (or all steps if step_key is None).
        Call this when advancing to a new plan step."""
        if step_key is None:
            self._step_reflection_counts.clear()
        else:
            self._step_reflection_counts.pop(str(step_key), None)

    def _project_root(self) -> str:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _load_plan(self) -> Dict:
        plan_path = os.path.join(self._project_root(), self.plan_file)
        if not os.path.exists(plan_path):
            raise FileNotFoundError(f"Plan file not found: {plan_path}")
        with open(plan_path, "r") as f:
            return json.load(f)

    def _load_recent_actions(self, limit: int = 10) -> List[Dict]:
        actions_path = os.path.join(self._project_root(), self.actions_file)
        if not os.path.exists(actions_path):
            return []

        try:
            with open(actions_path, "r") as f:
                content = f.read()
                if not content:
                    return []
                data = json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read actions.json: {str(e)}")
            return []

        flat_actions: List[Dict] = []
        for entry in data:
            timestamp = entry.get("timestamp")
            entry_executed = entry.get("executed", False)
            entry_complete = entry.get("complete", False)

            if "actions" in entry:
                for action in entry["actions"]:
                    flat_actions.append({
                        "timestamp": timestamp,
                        "entry_executed": entry_executed,
                        "entry_complete": entry_complete,
                        "action_type": action.get("action_type"),
                        "object_id": action.get("object_id"),
                        "object_type": action.get("object_type"),
                        "position": action.get("position"),
                        "positions": action.get("positions"),
                        "complete": action.get("complete", False),
                    })
            elif "action" in entry:
                action = entry["action"]
                flat_actions.append({
                    "timestamp": timestamp,
                    "entry_executed": entry_executed,
                    "entry_complete": entry_complete,
                    "action_type": action.get("action_type"),
                    "object_id": action.get("object_id"),
                    "object_type": action.get("object_type"),
                    "position": action.get("position"),
                    "positions": action.get("positions"),
                    "complete": action.get("complete", False),
                })

        if limit and len(flat_actions) > limit:
            return flat_actions[-limit:]
        return flat_actions

    def _extract_json_object(self, content: str) -> str:
        import re

        code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1).strip()

        start_idx = content.find("{")
        if start_idx == -1:
            raise ValueError("No JSON object found in response")

        brace_count = 0
        for i in range(start_idx, len(content)):
            if content[i] == "{":
                brace_count += 1
            elif content[i] == "}":
                brace_count -= 1
                if brace_count == 0:
                    return content[start_idx:i + 1]

        raise ValueError("Incomplete JSON response")

    def _log_reflection(self, reflection: ReflectionResult, plan_step: Optional[Dict] = None):
        entry = {
            "timestamp": time.time(),
            "plan_step": plan_step,
            "reflection": reflection.to_dict(),
        }

        reflection_path = os.path.join(self._project_root(), self.reflection_file)
        existing_data: List[Dict] = []
        if os.path.exists(reflection_path):
            try:
                with open(reflection_path, "r") as f:
                    content = f.read()
                    if content:
                        existing_data = json.loads(content)
            except (json.JSONDecodeError, OSError):
                existing_data = []

        existing_data.append(entry)
        with open(reflection_path, "w") as f:
            json.dump(existing_data, f, indent=2)
        step_label = plan_step.get("action") if isinstance(plan_step, dict) else None
        logger.info(
            "Logged reflection entry%s",
            f" for step '{step_label}'" if step_label else ""
        )

    def _load_placed_objects(self) -> List[Dict]:
        """Load already-placed Unity objects from unity_commands.json for accurate object counting."""
        project_root = self._project_root()
        unity_project = os.path.join(project_root, "firefighter project")
        commands_file = os.path.join(unity_project, "Assets", "Scripts", "unity_commands.json")

        if not os.path.exists(commands_file):
            return []

        try:
            with open(commands_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return []
                commands = json.loads(content)
        except (json.JSONDecodeError, IOError):
            return []

        placed_map: Dict[str, Dict] = {}
        for cmd in commands:
            if not isinstance(cmd, dict):
                continue
            action = cmd.get("action", "")
            name = cmd.get("objectName") or cmd.get("humanName", "")
            if not name:
                continue
            if action in ("PlaceObject", "CreateObjectByName", "PlaceHuman"):
                placed_map[name] = {
                    "action": action,
                    "objectName": name,
                    "x": cmd.get("x", 0),
                    "y": cmd.get("y", 0),
                    "z": cmd.get("z", 0),
                    "rotationY": cmd.get("rotationY", 0),
                }
            elif action == "MoveObject" and name in placed_map:
                # Apply position override — keep the most recent MoveObject for each object
                if cmd.get("x") is not None: placed_map[name]["x"] = cmd["x"]
                if cmd.get("y") is not None: placed_map[name]["y"] = cmd["y"]
                if cmd.get("z") is not None: placed_map[name]["z"] = cmd["z"]
                if cmd.get("rotationY") is not None: placed_map[name]["rotationY"] = cmd["rotationY"]
            elif action == "DeleteObject" and name in placed_map:
                del placed_map[name]

        return list(placed_map.values())

    def _load_recent_reflections(self, limit: int = 5) -> List[Dict]:
        reflection_path = os.path.join(self._project_root(), self.reflection_file)
        if not os.path.exists(reflection_path):
            return []

        try:
            with open(reflection_path, "r") as f:
                content = f.read()
                if not content:
                    return []
                data = json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read reflection.json: {str(e)}")
            return []

        if isinstance(data, list):
            return data[-limit:] if limit and len(data) > limit else data
        if isinstance(data, dict):
            return [data]
        return []

    def _sanitize_correction_lines(self, lines: List[str], valid_names: List[str]) -> List[str]:
        """Repair or drop correction lines whose object name is not a valid scene prefab.

        - Exact match  → keep as-is.
        - Fuzzy match (difflib score ≥ 0.6) → substitute name, log warning.
        - No match     → drop line, log warning.
        """
        import difflib
        if not valid_names:
            return lines
        valid_set = set(valid_names)
        cleaned: List[str] = []
        for line in lines:
            parts = line.split()
            if len(parts) < 2 or parts[0] not in ("MoveObject", "DeleteObject"):
                cleaned.append(line)
                continue
            name = parts[1]
            if name in valid_set:
                cleaned.append(line)
                continue
            matches = difflib.get_close_matches(name, valid_names, n=1, cutoff=0.6)
            if matches:
                fixed_name = matches[0]
                fixed_line = line.replace(name, fixed_name, 1)
                logger.warning(f"[NameFix] '{name}' not in scene → fuzzy-matched to '{fixed_name}'; line corrected.")
                cleaned.append(fixed_line)
            else:
                logger.warning(f"[NameFix] '{name}' has no valid match in scene — line dropped: {line}")
        return cleaned

    def _focused_llm(
        self,
        thread_name: str,
        system_prompt: str,
        object_context: str,
        max_tokens: int = 600,
    ) -> List[str]:
        """Shared helper: focused single-purpose LLM call used by every thread.

        Always uses self._view_data (screenshots) and self._exact_scene_names (prefab list).
        The strict name rule is always injected into the system prompt.
        All returned names are sanitized — invalid names fuzzy-matched or dropped."""
        import re

        # Always inject strict name rule using the authoritative prefab list
        names_block = "\n".join(f"  - {n}" for n in self._exact_scene_names) if self._exact_scene_names else "  (none yet)"
        name_rule = (
            "══ STRICT NAME RULE — NO EXCEPTIONS ══\n"
            "The scene contains EXACTLY these object names. You MUST copy them character-for-character:\n"
            f"{names_block}\n"
            "DO NOT invent, abbreviate, paraphrase, or guess any name.\n"
            "If you are unsure which exact name to use, omit the line entirely.\n"
            "Any name not in this list will be rejected.\n"
            "══════════════════════════════════════\n\n"
        )
        full_system = name_rule + system_prompt

        messages: list = [{"role": "system", "content": full_system}]

        # Always send screenshots — they are the primary source of truth
        if self._view_data:
            img_content: list = [{"type": "text", "text": (
                f"[{thread_name}] Use these screenshots as your PRIMARY source. "
                "Coordinates are supplementary only. XZ=top-down, XY=front, YZ=side.\n"
                "Base every correction on what you can SEE in the images."
            )}]
            for label, encoded in self._view_data:
                img_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
            messages.append({"role": "user", "content": img_content})

        # Object context also repeats the name list so it's immediately visible when the model generates
        name_reminder = (
            f"\nREMINDER — valid names only: {', '.join(self._exact_scene_names)}\n"
            if self._exact_scene_names else ""
        )
        messages.append({"role": "user", "content": object_context + name_reminder})

        try:
            completion = self.groq_client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0,
                max_completion_tokens=max_tokens,
                top_p=1,
                stream=False,
            )
            raw = (completion.choices[0].message.content or "").strip()
            lines = [
                ln.strip() for ln in raw.splitlines()
                if re.match(r"^(MoveObject|DeleteObject)\s+\S+", ln.strip())
            ]
            lines = self._sanitize_correction_lines(lines, self._exact_scene_names)
            logger.info(f"[{thread_name}] focused LLM returned {len(lines)} line(s).")
            return lines
        except Exception as e:
            logger.warning(f"[{thread_name}] focused LLM call failed: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # Threading helpers — run in parallel with the LLM surface thread
    # ──────────────────────────────────────────────────────────────────────────

    def _dispatcher_thread(self, placed_objects: List[Dict]) -> List[str]:
        """Thread 0 (Python + LLM visual): triage — detect floating and randomly placed objects,
        categorise each by issue type, and emit a tagged MoveObject correction line.

        Issue tags embedded in the reason string so the merge step and logs show routing:
          [dispatcher→bounds]   object is also out of bounds (bounds thread will re-clamp)
          [dispatcher→collision] object is overlapping something (collision thread handles nudge)
          [dispatcher→surface]  object needs a logical surface beneath it
          [dispatcher→zone]     supply/decor object randomly placed mid-room
        """
        SURFACE_KEYWORDS  = ["Cart", "Trolley", "Table", "Desk", "Counter", "Shelf", "Bed", "Stand"]
        SUPPLY_KEYWORDS   = ["Cart", "Trolley", "Basin", "Sink", "Dispenser", "Drip", "Stand"]
        DECOR_KEYWORDS    = ["Poster", "Clock", "Sign", "Panel"]
        CHARACTER_PREFIXES = ["Doctor", "Nurse", "Patient", "Surgeon", "EMT"]
        FLOAT_Y_THRESHOLD = 0.6   # objects above this with no surface below are floating
        SURFACE_RADIUS    = 0.5   # x/z radius to search for a supporting surface
        MID_ROOM_X        = 2.1   # supply/decor within |x| < this AND z in [0.7,5.1] = mid-room

        objs = [o for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"]]
        corrections: List[str] = []
        flagged: List[str] = []

        for obj in objs:
            name = obj["objectName"]
            x    = float(obj.get("x",   0))
            y    = float(obj.get("y",   0))
            z    = float(obj.get("z",   0))
            rot  = float(obj.get("rotationY", 0))

            is_character = any(name.startswith(p) for p in CHARACTER_PREFIXES)
            is_decor     = any(t in name for t in DECOR_KEYWORDS)
            is_supply    = any(t in name for t in SUPPLY_KEYWORDS)

            # ── Check 1: Floating ──────────────────────────────────────────────
            # Wall-mounted decor near a wall at height is intentional — skip.
            wall_mounted = is_decor and (abs(x) > 2.0 or z < 0.5 or z > 5.5) and y > 1.0
            if not is_character and not wall_mounted and y > FLOAT_Y_THRESHOLD:
                # Search for a surface object within radius that is below this object
                nearest_surface = None
                min_dist = float("inf")
                for other in objs:
                    oname = other["objectName"]
                    if oname == name:
                        continue
                    if not any(t in oname for t in SURFACE_KEYWORDS):
                        continue
                    ox = float(other.get("x", 0))
                    oz = float(other.get("z", 0))
                    oy = float(other.get("y", 0))
                    if oy >= y:
                        continue  # surface must be below the object
                    dist = ((x - ox) ** 2 + (z - oz) ** 2) ** 0.5
                    if dist < SURFACE_RADIUS and dist < min_dist:
                        min_dist = dist
                        nearest_surface = other

                if nearest_surface is None:
                    # No supporting surface found — floating with nowhere logical to go
                    # Sub-categorise: is it also out of bounds?
                    out_of_bounds = not (-2.8 <= x <= 2.8 and 0.1 <= z <= 5.9)
                    tag = "[dispatcher→bounds]" if out_of_bounds else "[dispatcher→surface]"
                    # Find nearest surface in the whole room to suggest a destination
                    best = None; best_dist = float("inf")
                    for other in objs:
                        if other["objectName"] == name: continue
                        if not any(t in other["objectName"] for t in SURFACE_KEYWORDS): continue
                        d = ((x - float(other.get("x",0)))**2 + (z - float(other.get("z",0)))**2)**0.5
                        if d < best_dist: best_dist = d; best = other
                    if best and best_dist < 4.0:
                        nx = float(best.get("x", x))
                        ny = float(best.get("y", 0)) + 0.1
                        nz = float(best.get("z", z))
                        corrections.append(
                            f"MoveObject {name} x={nx:.2f} y={ny:.2f} z={nz:.2f} rotationY={rot:.0f}"
                            f"  — reason: {tag} floating at y={y:.2f}, no surface below; nearest surface is {best['objectName']}"
                        )
                    else:
                        corrections.append(
                            f"MoveObject {name} x={x:.2f} y=0.00 z={z:.2f} rotationY={rot:.0f}"
                            f"  — reason: {tag} floating at y={y:.2f} with no surface object in scene; dropped to floor"
                        )
                    flagged.append(f"{name} → floating ({tag})")

            # ── Check 2: Supply / decor randomly placed mid-room ──────────────
            if (is_supply or is_decor) and not is_character:
                mid_room = abs(x) <= MID_ROOM_X and 0.7 <= z <= 5.1
                if mid_room:
                    # Route to nearest wall
                    dists = {
                        "left":  (abs(x - (-2.8)), -2.3, z,   90),
                        "right": (abs(x -   2.8),   2.3, z,  270),
                        "front": (abs(z -   0.1),   x,   0.4,  0),
                        "back":  (abs(z -   5.9),   x,   5.5, 180),
                    }
                    wall = min(dists, key=lambda k: dists[k][0])
                    _, nx, nz, nrot = dists[wall]
                    kind = "supply" if is_supply else "decor"
                    corrections.append(
                        f"MoveObject {name} x={nx:.2f} y={y:.2f} z={nz:.2f} rotationY={nrot:.0f}"
                        f"  — reason: [dispatcher→zone] {kind} object mid-room (x={x:.2f}, z={z:.2f}); moved to {wall} wall"
                    )
                    flagged.append(f"{name} → mid-room {kind} [dispatcher→zone]")

        if flagged:
            logger.info(f"[DispatcherThread] {len(flagged)} object(s) flagged: {flagged}")
        else:
            logger.info("[DispatcherThread] no floating or randomly placed objects detected.")

        # Visual LLM check — primary source for catching floating/random placement
        names_corrected = {ln.split()[1] for ln in corrections if ln.startswith("MoveObject") and len(ln.split()) > 1}
        obj_list = "\n".join(f"  {o['objectName']}: x={o.get('x',0):.2f} y={o.get('y',0):.2f} z={o.get('z',0):.2f}" for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"])
        sys_prompt = (
            "You are checking ONLY for floating objects (no surface beneath them) and objects randomly "
            "placed in the middle of the room that should be near a wall. "
            "Room bounds: x in [-2.8,2.8], z in [0.1,5.9]. "
            "Use the screenshots as your primary reference. "
            "Output ONLY MoveObject lines, one per issue, format:\n"
            "MoveObject <exactName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: [dispatcher→<type>] <issue>\n"
            "Do NOT flag anything else. If nothing is wrong, output nothing."
        )
        llm_lines = self._focused_llm("DispatcherThread", sys_prompt, f"Objects:\n{obj_list}")
        new_lines = [ln for ln in llm_lines if not any(n in ln for n in names_corrected)]
        corrections.extend(new_lines)
        if new_lines:
            logger.info(f"[DispatcherThread] LLM visual check added {len(new_lines)} extra correction(s).")

        return corrections

    def _bounds_thread(self, placed_objects: List[Dict]) -> List[str]:
        """Thread 1 (pure Python): clamp every object to room bounds.
        Returns a list of ready-to-use MoveObject correction lines."""
        X_MIN, X_MAX = -2.8, 2.8
        Z_MIN, Z_MAX = 0.1, 5.9
        Y_MIN = 0.0
        corrections: List[str] = []
        for obj in placed_objects:
            name = obj.get("objectName", "")
            if not name or "Cube" in name:
                continue
            x   = float(obj.get("x",   0))
            y   = float(obj.get("y",   0))
            z   = float(obj.get("z",   0))
            rot = float(obj.get("rotationY", 0))
            nx, ny, nz = x, y, z
            issues: List[str] = []
            if x < X_MIN:   nx = X_MIN; issues.append(f"x={x:.2f} < {X_MIN}")
            elif x > X_MAX: nx = X_MAX; issues.append(f"x={x:.2f} > {X_MAX}")
            if z < Z_MIN:   nz = Z_MIN; issues.append(f"z={z:.2f} < {Z_MIN}")
            elif z > Z_MAX: nz = Z_MAX; issues.append(f"z={z:.2f} > {Z_MAX}")
            if y < Y_MIN:   ny = Y_MIN; issues.append(f"y={y:.2f} < {Y_MIN}")
            if issues:
                line = (
                    f"MoveObject {name} x={nx:.2f} y={ny:.2f} z={nz:.2f} rotationY={rot:.0f}"
                    f"  — reason: out of bounds ({', '.join(issues)})"
                )
                corrections.append(line)
                logger.info(f"[BoundsThread] queued: {line}")
        logger.info(f"[BoundsThread] {len(corrections)} bound corrections generated.")

        # Visual LLM check — catches objects clipping through walls that coordinate math missed
        names_corrected = {ln.split()[1] for ln in corrections if ln.startswith("MoveObject") and len(ln.split()) > 1}
        obj_list = "\n".join(f"  {o['objectName']}: x={o.get('x',0):.2f} y={o.get('y',0):.2f} z={o.get('z',0):.2f}" for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"])
        sys_prompt = (
            "You are checking ONLY for objects visually outside or clipping through the room walls. "
            "Room bounds: x in [-2.8,2.8], z in [0.1,5.9], y >= 0. Walls at x=±3, z=0, z=6. "
            "Use the screenshots as your primary reference. "
            "Output ONLY MoveObject lines, one per out-of-bounds object, format:\n"
            "MoveObject <exactName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: visually out of bounds\n"
            "Do NOT flag anything else. If nothing is out of bounds, output nothing."
        )
        llm_lines = self._focused_llm("BoundsThread", sys_prompt, f"Objects:\n{obj_list}")
        new_lines = [ln for ln in llm_lines if not any(n in ln for n in names_corrected)]
        corrections.extend(new_lines)
        if new_lines:
            logger.info(f"[BoundsThread] LLM visual check added {len(new_lines)} extra correction(s).")

        return corrections

    def _collision_thread(self, placed_objects: List[Dict]) -> List[str]:
        """Thread 2 (Python + LLM visual): detect pairs of objects that overlap and nudge
        the less-important one aside. Returns a list of MoveObject correction lines."""
        THRESHOLD = 0.4   # metres — objects closer than this on BOTH x AND z are colliding
        NUDGE     = 0.45  # how far to push the offending object

        # Exclude structural cubes (walls, floor, ceiling) — they have no meaningful collision
        objs = [o for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"]]
        corrections: List[str] = []
        resolved: set = set()  # names already nudged this pass — don't double-move

        for i in range(len(objs)):
            for j in range(i + 1, len(objs)):
                a, b = objs[i], objs[j]
                na, nb = a["objectName"], b["objectName"]
                if nb in resolved:
                    continue
                ax = float(a.get("x", 0)); az = float(a.get("z", 0))
                bx = float(b.get("x", 0)); bz = float(b.get("z", 0)); by = float(b.get("y", 0))
                rot_b = float(b.get("rotationY", 0))
                dx = abs(ax - bx); dz = abs(az - bz)
                if dx < THRESHOLD and dz < THRESHOLD:
                    # Nudge b away from a along the dominant axis
                    if dx >= dz:
                        new_bx = max(-2.8, min(2.8, bx + (NUDGE if bx >= ax else -NUDGE)))
                        new_bz = bz
                    else:
                        new_bx = bx
                        new_bz = max(0.1, min(5.9, bz + (NUDGE if bz >= az else -NUDGE)))
                    line = (
                        f"MoveObject {nb} x={new_bx:.2f} y={by:.2f} z={new_bz:.2f} rotationY={rot_b:.0f}"
                        f"  — reason: overlaps {na} (Δx={dx:.2f}m Δz={dz:.2f}m < {THRESHOLD}m threshold)"
                    )
                    corrections.append(line)
                    resolved.add(nb)
                    logger.info(f"[CollisionThread] queued: {line}")

        logger.info(f"[CollisionThread] {len(corrections)} collision corrections generated.")

        # Visual LLM check — catches visually overlapping large objects whose centers are
        # more than THRESHOLD apart (e.g. CT scanner + table that look merged in the screenshot)
        names_corrected = resolved.copy()
        obj_list = "\n".join(
            f"  {o['objectName']}: x={o.get('x',0):.2f} y={o.get('y',0):.2f} z={o.get('z',0):.2f}"
            for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"]
        )
        sys_prompt = (
            "You are checking ONLY for objects that visually overlap or intersect each other in the scene. "
            "Room bounds: x in [-2.8,2.8], z in [0.1,5.9]. "
            "Use the screenshots as your primary reference. "
            "For each visually overlapping pair, move the less important object away. "
            "Output ONLY MoveObject lines, one per colliding object, format:\n"
            "MoveObject <exactName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: visually overlaps <otherName>\n"
            "Do NOT flag anything else. If no overlaps, output nothing."
        )
        llm_lines = self._focused_llm("CollisionThread", sys_prompt, f"Objects:\n{obj_list}")
        new_lines = [ln for ln in llm_lines if not any(n in ln for n in names_corrected)]
        corrections.extend(new_lines)
        if new_lines:
            logger.info(f"[CollisionThread] LLM visual check added {len(new_lines)} extra correction(s).")

        return corrections

    def reflect_step(self, plan_step: Dict, game_state: Dict, relevant_knowledge: Optional[List[Dict]] = None) -> ReflectionResult:
        """Reflect on the current step completion using plan, actions, and screenshots."""
        knowledge = relevant_knowledge or []
        is_loop_step = isinstance(plan_step, dict) and plan_step.get("step") == "loop"

        # --- Iteration limit: force completion after MAX_REFLECTIONS_PER_STEP ---
        step_key = str(plan_step.get("step", "unknown")) if isinstance(plan_step, dict) else "unknown"
        self._step_reflection_counts[step_key] = self._step_reflection_counts.get(step_key, 0) + 1
        current_count = self._step_reflection_counts[step_key]

        if current_count > self.MAX_REFLECTIONS_PER_STEP and not is_loop_step:
            logger.info(
                f"Step '{step_key}' reached max reflections ({self.MAX_REFLECTIONS_PER_STEP}). "
                "Forcing step_complete=yes to avoid infinite correction loop."
            )
            forced = ReflectionResult(
                step_complete="yes",
                current_action_thoughts="Max reflection iterations reached. Accepting current layout.",
                next_action_thoughts="Proceeding to next step.",
                alignment_suggestions="",
                layout_comments="Layout accepted after maximum correction iterations.",
                room_status="good",
                suggest_additions=False,
            )
            self._log_reflection(forced, plan_step=plan_step)
            return forced

        plan = self._load_plan()
        plan_str = json.dumps(plan, indent=2)
        plan_step_str = json.dumps(plan_step, indent=2)
        game_state_str = json.dumps(game_state, indent=2)
        recent_reflections = self._load_recent_reflections(limit=5)
        reflections_str = json.dumps(recent_reflections, indent=2)

        # Load already-placed Unity objects so the reflection knows the real object count
        placed_objects = self._load_placed_objects()
        placed_objects_str = json.dumps(placed_objects, indent=2)
        placed_count = len(placed_objects)
        knowledge_str = "\n".join(
            [f"Knowledge {i+1}: {k.get('text', '')}" for i, k in enumerate(knowledge)]
        )

        # Build a dynamic object inventory from game_state for context injection
        placed_objects_list = game_state.get("placed_objects", [])
        object_names_in_scene = [o.get("objectName", "") for o in placed_objects_list if o.get("objectName")]
        scene_inventory = ", ".join(object_names_in_scene) if object_names_in_scene else "none"

        # Authoritative prefab names from unity_commands.json — set as instance state for all threads
        exact_scene_names = sorted({o["objectName"] for o in placed_objects if o.get("objectName")})
        self._exact_scene_names = exact_scene_names
        supply_types = ["Cart", "Trolley", "Basin", "Sink", "Dispenser", "Drip", "Stand"]
        supply_objects = [n for n in object_names_in_scene if any(t in n for t in supply_types)]
        character_objects = [n for n in object_names_in_scene if any(n.startswith(p) for p in ["Doctor", "Nurse", "Patient", "Surgeon", "EMT"])]
        small_prop_objects = [n for n in object_names_in_scene if any(t in n for t in ["Folder", "Box", "Syringe", "Thermometer", "Tray"])]
        decor_objects = [n for n in object_names_in_scene if any(t in n for t in ["Poster", "Clock", "Sign", "Panel"])]

        exact_names_str = "\n".join(f"  - {n}" for n in exact_scene_names) if exact_scene_names else "  (none yet)"

        system_prompt = f"""You are a layout quality inspector for a VR medical training room in Unity.
        Room bounds: x in [-2.8, 2.8], z in [0.1, 5.9], y >= 0. Walls at x=±3, z=0, z=6.

        ══════════════════════════════════════════════════
        STRICT NAME RULE — VIOLATION = INVALID RESPONSE
        ══════════════════════════════════════════════════
        The ONLY valid object names you may reference in any field are:
{exact_names_str}
        ANY name you invent, abbreviate, or guess that is NOT in this list is WRONG.
        Do NOT paraphrase (e.g. "CT_Scanner" when scene has "Tomograpf_01" → wrong).
        Do NOT use plural or category words ("chairs", "objects near wall").
        If you cannot identify an object by its exact name above, skip it entirely.
        ══════════════════════════════════════════════════

        ══════════════════════════════════════════════════
        THREAD DIVISION — YOUR SCOPE ONLY
        ══════════════════════════════════════════════════
        Two other threads run in parallel and handle:
          • BOUNDS violations (x/z/y out of range) — already corrected, do NOT re-check.
          • OBJECT OVERLAPS / collisions (pairs within 0.4m) — already corrected, do NOT re-check.
        YOUR JOB: zone correctness, surface logic, rotation, room status. That is all.
        ══════════════════════════════════════════════════

        Objects currently in scene: {scene_inventory}
        Supply objects: {supply_objects}
        Characters: {character_objects}
        Small props: {small_prop_objects}
        Decor: {decor_objects}

        YOUR PRIMARY JOB IS TO FIX LAYOUT ISSUES. Adding objects is secondary (only if sparse).

        PRIORITY 1 — PER-OBJECT ZONE + SURFACE + ROTATION AUDIT:

        CHECK A — WRONG ZONE for object type:
          Supply objects {supply_objects}: must be near a wall (|x| > 2.1 OR z < 0.7 OR z > 5.1).
            If mid-room (|x| <= 2.1 AND z in [0.7,5.1]), flag by exact name and move to nearest valid wall zone.
          Small props {small_prop_objects}: must be on a surface (y >= 0.6). If y < 0.6, flag by exact name.
            LOGICAL SURFACE RULE — think step-by-step for each prop:
              Step 1: Identify the prop type (Syringe/Thermometer/Tray → medical instrument; Folder/Clipboard/Chart → document; Box → supply).
              Step 2: Find the nearest surface object from the strict name list by x/z proximity.
              Step 3: Is that surface type appropriate?
                - Medical instruments: ONLY on Cart, Trolley, or exam Table.
                - Document props: ONLY on desk, counter, or Cart.
                - Supply boxes: ONLY on Cart, Trolley, or shelf — NOT floor mid-room.
              Step 4: If surface is illogical (e.g. Syringe beside a CT bore), find the nearest appropriate surface and move there.
            ALSO verify the prop is within 0.5m x/z of a valid logical surface object from the strict name list.
          Decor objects {decor_objects}: must be near a wall (|x| > 2.0 OR z < 0.5 OR z > 5.5) at y > 1.0.
            If floating mid-room or too low, flag by exact name.
          Characters {character_objects}: must NOT be inside non-human objects.
            If 2+ characters are within 0.5m of each other, name each and recommend moving one.

        CHECK B — WALKWAY BLOCK: central corridor z in [1.5,4.5], |x| < 1.2 must stay clear for primary equipment.
          Any non-primary object in this zone: flag by exact name and move to nearest wall.

        CHECK C — ROTATION:
          Objects near left wall (x < -2.0): rotationY=90 (face right).
          Objects near right wall (x > 2.0): rotationY=270 (face left).
          Objects near back wall (z > 5.0): rotationY=180 (face forward).
          Objects near entry (z < 0.5): rotationY=0 (face inward).
          Characters: face the room's primary equipment (toward room center).

        REQUIRED FORMAT — one line per problem, exact objectName from strict name list only:
          MoveObject <exactObjectName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: <specific issue>
          DeleteObject <exactObjectName>  — reason: <specific issue>
        BANNED: invented names, paraphrased names, category words ("chair", "scanner", "monitor").
        BANNED: "objects look", "characters seem", "scattered", "randomly", "some items", "a few", "various".
        Every objectName MUST appear verbatim in the strict name list. Objects passing all checks: do NOT mention.

        PRIORITY 2 — ROOM FULLNESS:
        Count non-Cube objects in game_state. room_status = "sparse" if < 10 | "good" if 10-25 | "cluttered" if > 25.
        HARD CAP = 25. If count >= 25: room_status = "cluttered", suggest_additions = false — no exceptions.
        suggest_additions = true ONLY if room_status = "sparse".

        PRIORITY 3 — ADDITIONS (only if suggest_additions = true):
        Suggest specific prefab names with exact coordinates. Only suggest objects NOT in: {scene_inventory}.

        Do not reverse corrections already in game_state last_edit_commands.

        Output ONLY valid JSON:
        {{
          "step_complete": "yes" or "no",
          "current_action_thoughts": "zone-by-zone description naming specific objects and coordinates from game_state",
          "next_action_thoughts": "remaining issues listing specific objectNames",
          "alignment_suggestions": "one MoveObject/DeleteObject/PlaceObject line per issue — exact names and coords, no vague phrases",
          "layout_comments": "2-4 sentences naming specific objects by name: what is correct, what is wrong, in the context of this room type",
          "room_status": "sparse" or "good" or "cluttered",
          "suggest_additions": true or false
        }}"""

        loop_mode_note = ""
        if is_loop_step:
            loop_mode_note = (
                "LOOP MODE: Focus on evaluating loop conditions and monitored values in the current step. "
                "Only mark step_complete as yes if loop conditions are satisfied.\n\n"
            )

        base_content = f"""{loop_mode_note}IMPORTANT: Base your visual judgments primarily on the screenshots provided below, not just the object list.
Look at each screenshot carefully before generating edit_commands. The screenshots are ground truth.

        Full Plan:
        {plan_str}

        Current Step:
        {plan_step_str}

        Placed Objects (name, position):
        {game_state_str}

        Already Placed Unity Objects ({placed_count} objects — use this for room_status counting):
        {placed_objects_str}
        NOTE: There are {placed_count} objects in the scene. HARD CAP = 25 meaningful objects (excluding Cube walls/floor/ceiling).
        If count >= 25: room_status MUST be "cluttered", suggest_additions MUST be false. Do not suggest adding anything.
        Do NOT suggest PlaceObject for any objectName already in this list — use MoveObject to reposition instead.

        Relevant Knowledge:
        {knowledge_str}

        Recent Reflections (iteration {current_count} of max {self.MAX_REFLECTIONS_PER_STEP} for this step):
        {reflections_str}
        """

        # Load the 3 orthographic view screenshots (XZ=top-down, XY=front, YZ=side)
        import base64
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        view_labels = [
            ("XZ (top-down)", self.screenshot_xz),
            ("XY (front)", self.screenshot_xy),
            ("YZ (side)", self.screenshot_yz),
        ]
        view_data: list = []
        for label, rel_path in view_labels:
            abs_path = rel_path if os.path.isabs(rel_path) else os.path.join(project_root, rel_path)
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "rb") as f:
                        encoded = base64.b64encode(f.read()).decode("utf-8")
                    view_data.append((label, encoded))
                except Exception as e:
                    logger.warning(f"Error reading {label} screenshot: {e}")

        # Store as instance state — all threads read from self._view_data directly
        self._view_data = view_data

        if not view_data:
            logger.info("No screenshots available — threads will use coordinate-only reasoning.")

        # ── Build LLM messages (Thread 3 input) ───────────────────────────────
        messages = [{"role": "system", "content": system_prompt}]
        if view_data:
            image_content = [{"type": "text", "text": "These are the current orthographic screenshots of the scene. Study them carefully before reading the object list.\nXZ=top-down (shows x/z layout), XY=front, YZ=side."}]
            for label, encoded in view_data:
                image_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
            messages.append({"role": "user", "content": image_content})
        messages.append({"role": "user", "content": base_content})

        def _run_surface_llm() -> dict:
            """Thread 3 (LLM): zone, surface logic, rotation, room status."""
            max_retries, retry_delay = 3, 2.0
            for attempt in range(max_retries):
                try:
                    completion = self.groq_client.chat.completions.create(
                        model="meta-llama/llama-4-scout-17b-16e-instruct",
                        messages=messages,
                        temperature=0,
                        max_completion_tokens=1500,
                        top_p=1,
                        stream=True,
                        stop=None,
                    )
                    raw = ""
                    for chunk in completion:
                        if chunk.choices and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if hasattr(delta, "content") and delta.content:
                                raw += delta.content
                    time.sleep(0.1)
                    raw = raw.strip()
                    if not raw:
                        raise ValueError("Empty response from Groq API")
                    return json.loads(self._extract_json_object(raw))
                except (ValueError, json.JSONDecodeError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"[SurfaceThread] attempt {attempt+1}/{max_retries} failed: {e}")
                        time.sleep(retry_delay)
                        continue
                    logger.error(f"[SurfaceThread] failed after {max_retries} attempts: {e}")
                    raise

        # ── Launch all 4 threads in parallel ──────────────────────────────────
        logger.info("Launching reflection threads: [DispatcherThread] [BoundsThread] [CollisionThread] [SurfaceThread]")
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_dispatch:  Future = executor.submit(self._dispatcher_thread, placed_objects)
            future_bounds:    Future = executor.submit(self._bounds_thread,     placed_objects)
            future_collision: Future = executor.submit(self._collision_thread,  placed_objects)
            future_surface:   Future = executor.submit(_run_surface_llm)

            dispatch_corrections  = future_dispatch.result()
            bounds_corrections    = future_bounds.result()
            collision_corrections = future_collision.result()
            try:
                reflection_dict = future_surface.result()
            except Exception as e:
                logger.error(f"[SurfaceThread] unrecoverable error — falling back to empty result: {e}")
                reflection_dict = {}

        # ── Merge alignment_suggestions ────────────────────────────────────────
        # Priority: bounds > collision > dispatcher > LLM surface.
        # If a higher-priority thread already corrected an object, lower-priority
        # threads skip it to prevent conflicting moves on the same object.
        def _corrected_names(lines: List[str]) -> set:
            return {ln.split()[1] for ln in lines if ln.startswith("MoveObject") and len(ln.split()) > 1}

        bounds_names    = _corrected_names(bounds_corrections)
        collision_names = _corrected_names(collision_corrections)
        already_handled = bounds_names | collision_names

        # Dispatcher: skip objects already corrected by bounds/collision
        filtered_dispatch = [ln for ln in dispatch_corrections if not any(n in ln for n in already_handled)]
        dispatch_names = _corrected_names(filtered_dispatch)
        already_handled |= dispatch_names

        # LLM surface: sanitize names first, then skip anything corrected by Python threads
        llm_suggestions_raw = str(reflection_dict.get("alignment_suggestions", "")).strip()
        llm_lines_raw = [ln.strip() for ln in llm_suggestions_raw.splitlines() if ln.strip()]
        llm_lines = self._sanitize_correction_lines(llm_lines_raw, exact_scene_names)
        filtered_llm = [ln for ln in llm_lines if not any(n in ln for n in already_handled)]

        merged_suggestions = "\n".join(
            bounds_corrections + collision_corrections + filtered_dispatch + filtered_llm
        )

        def _log_corrections(tag: str, lines: List[str], skipped: int = 0) -> None:
            skip_note = f" (skipped {skipped} already handled)" if skipped else ""
            if lines:
                logger.info(f"[{tag}] {len(lines)} correction(s) applied{skip_note}:")
                for ln in lines:
                    logger.info(f"  [{tag}] {ln}")
            else:
                logger.info(f"[{tag}] 0 corrections applied{skip_note}.")

        _log_corrections("BoundsThread",     bounds_corrections)
        _log_corrections("CollisionThread",  collision_corrections)
        _log_corrections("DispatcherThread", filtered_dispatch, skipped=len(dispatch_corrections) - len(filtered_dispatch))
        _log_corrections("SurfaceThread",    filtered_llm,      skipped=len(llm_lines) - len(filtered_llm))

        # ── Assemble final ReflectionResult ───────────────────────────────────
        step_complete = str(reflection_dict.get("step_complete", "no")).strip().lower()
        if step_complete not in {"yes", "no"}:
            step_complete = "no"

        raw_status = str(reflection_dict.get("room_status", "good")).strip().lower()
        room_status = raw_status if raw_status in {"sparse", "good", "cluttered"} else "good"
        suggest_additions = bool(reflection_dict.get("suggest_additions", False)) and room_status == "sparse"

        if not view_data:
            if room_status == "sparse":
                room_status = "good"
            suggest_additions = False

        reflection = ReflectionResult(
            step_complete=step_complete,
            current_action_thoughts=str(reflection_dict.get("current_action_thoughts", "")).strip(),
            next_action_thoughts=str(reflection_dict.get("next_action_thoughts", "")).strip(),
            alignment_suggestions=merged_suggestions,
            layout_comments=str(reflection_dict.get("layout_comments", "")).strip(),
            room_status=room_status,
            suggest_additions=suggest_additions,
        )
        self._log_reflection(reflection, plan_step=plan_step)
        return reflection
