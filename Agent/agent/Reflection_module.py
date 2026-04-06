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

def _parse_experience_config(txt: str) -> dict:
    """Parse key=value pairs from the [room_config] section of experience.txt."""
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
        self._view_data: list = []              # [(label, base64_encoded), ...]
        self._scene_state: List[Dict] = []      # live scene objects from scene_state.json (or fallback)
        self._exact_scene_names: List[str] = [] # derived from _scene_state; used by _sanitize_correction_lines
        self._denied_corrections: List[str] = [] # names dropped this round due to invalid names

        # Derive the 3-view screenshot base path from env var (strip .png if present)
        raw_path = os.getenv("UNITY_SCREENSHOT_PATH", "api_files/scene_screenshot.png")
        base = raw_path[:-4] if raw_path.endswith(".png") else raw_path
        self.screenshot_xz = base + "_xz.png"  # top-down
        self.screenshot_xy = base + "_xy.png"  # front
        self.screenshot_yz = base + "_yz.png"  # side

        # Load scene config from experience.txt
        self._cfg = self._load_experience_config()

    def _load_experience_config(self) -> dict:
        """Load [room_config] from experience.txt. Returns empty dict if not found."""
        experience_path = os.path.join(self._project_root(), "api_files", "experience.txt")
        if not os.path.exists(experience_path):
            return {}
        try:
            with open(experience_path, "r", encoding="utf-8") as f:
                return _parse_experience_config(f.read())
        except Exception as e:
            logger.warning(f"Could not load experience.txt config: {e}")
            return {}

    def reset_step_count(self, step_key: str = None):
        """Reset reflection iteration count for a step (or all steps if step_key is None).
        Call this when advancing to a new plan step."""
        if step_key is None:
            self._step_reflection_counts.clear()
        else:
            self._step_reflection_counts.pop(str(step_key), None)

    def _project_root(self) -> str:
        """Returns the Agent/ directory (where api_files/ lives)."""
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _repo_root(self) -> str:
        """Returns the repo root (one level above Agent/, where 'firefighter project/' lives)."""
        return os.path.dirname(self._project_root())

    def _enqueue_debug_log(self, message: str) -> None:
        """Append a DebugLog command to unity_commands.json so Unity prints it in the Console."""
        commands_file = os.path.join(
            self._repo_root(), "firefighter project", "Assets", "Scripts", "unity_commands.json"
        )
        import uuid
        new_cmd = {"id": str(uuid.uuid4()), "executed": False, "done": False, "action": "DebugLog", "message": message}
        try:
            existing: list = []
            if os.path.exists(commands_file):
                with open(commands_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        existing = json.loads(content)
            existing.append(new_cmd)
            with open(commands_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.warning(f"[CollisionThread] _enqueue_debug_log failed: {e}")

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

    def _log_reflection(self, reflection: ReflectionResult, plan_step: Optional[Dict] = None, denied_corrections: Optional[List[str]] = None):
        entry = {
            "timestamp": time.time(),
            "plan_step": plan_step,
            "reflection": reflection.to_dict(),
            "denied_corrections": denied_corrections or [],
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

    def _load_scene_state(self) -> List[Dict]:
        """Load live scene state from scene_state.json written by EnvironmentAgent.WriteSceneState().

        Each record has: objectName, x, y, z, rotationY, and real renderer bounds
        (boundsMinX/Y/Z, boundsMaxX/Y/Z, boundsSizeX/Y/Z).

        Falls back to _load_placed_objects() if scene_state.json doesn't exist yet
        (i.e. Unity hasn't run or the file hasn't been written on this session).
        """
        project_root = self._project_root()
        state_file = os.path.join(project_root, "api_files", "scene_state.json")

        if not os.path.exists(state_file):
            logger.debug("[SceneState] scene_state.json not found — falling back to command-log reconstruction.")
            return self._load_placed_objects()

        try:
            with open(state_file, 'r', encoding='utf-8-sig') as f:
                content = f.read().strip()
                if not content:
                    return self._load_placed_objects()
                data = json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"[SceneState] Failed to read scene_state.json: {e} — falling back.")
            return self._load_placed_objects()

        if not isinstance(data, list):
            logger.warning("[SceneState] scene_state.json is not a list — falling back.")
            return self._load_placed_objects()

        logger.info(f"[SceneState] Loaded {len(data)} live objects from scene_state.json.")
        return data

    def _load_placed_objects(self) -> List[Dict]:
        """Reconstruct placed objects from unity_commands.json command log.
        Used as fallback when scene_state.json is not available."""
        commands_file = os.path.join(
            self._repo_root(), "firefighter project", "Assets", "Scripts", "unity_commands.json"
        )

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
        """Drop correction lines whose object name is not an exact match in the scene.

        - Exact match  → keep as-is.
        - No match     → drop line, log warning.
        Fuzzy matching is intentionally omitted: substituting a visually-similar but wrong
        name would move the wrong object. If the LLM cannot produce the exact name, the
        correction is unsafe and must be discarded.
        """
        if not valid_names:
            logger.warning("[NameFix] valid_names is empty — all LLM correction lines dropped to prevent hallucinated moves.")
            for line in lines:
                parts = line.split()
                if len(parts) >= 2 and parts[0] in ("MoveObject", "DeleteObject"):
                    self._denied_corrections.append(f"{parts[1]} (no valid names loaded)")
            return []
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
            else:
                logger.warning(f"[NameFix] '{name}' not in scene — line dropped (hallucinated name): {line}")
                self._denied_corrections.append(name)
        return cleaned

    def _focused_llm(
        self,
        thread_name: str,
        system_prompt: str,
        object_context: str,
        max_tokens: int = 600,
    ) -> List[str]:
        """Shared helper: focused single-purpose LLM call used by every thread.

        Uses self._scene_state (full scene JSON with positions + bounds) and self._view_data
        (screenshots). The strict name rule and scene JSON are always injected into the prompt.
        All returned names are sanitized — unrecognised names are dropped."""
        import re

        # Build name rule from live scene state if available
        if self._scene_state:
            names_block = "\n".join(f"  - {o['objectName']}" for o in self._scene_state if o.get("objectName"))
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
        else:
            full_system = system_prompt

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

        # Pass scene state JSON directly if available; otherwise fall back to screenshots only
        if self._scene_state:
            scene_json = json.dumps(
                [{"objectName": o["objectName"], "x": o.get("x", 0), "y": o.get("y", 0), "z": o.get("z", 0),
                  "rotationY": o.get("rotationY", 0),
                  "boundsSize": {"x": o.get("boundsSizeX", 0), "y": o.get("boundsSizeY", 0), "z": o.get("boundsSizeZ", 0)}}
                 for o in self._scene_state if o.get("objectName") and "Cube" not in o["objectName"]],
                indent=2
            )
            user_text = f"SCENE STATE (from scene_state.json — use objectName values verbatim):\n{scene_json}\n\n{object_context}"
        else:
            logger.info(f"[{thread_name}] scene state empty — using screenshots only.")
            user_text = object_context
        messages.append({"role": "user", "content": user_text})

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
        """Thread 0 (Python + LLM visual): detect floating objects and fix them.

        Only intervenes when an object is physically unsupported (floating in mid-air with
        no surface beneath it). Does NOT push mid-room objects to walls — room layout
        decisions belong to the LLM planner, not the reflection pass.

        Issue tags embedded in the reason string:
          [dispatcher→bounds]   object is also out of bounds (bounds thread will re-clamp)
          [dispatcher→surface]  object needs a logical surface beneath it
        """
        cfg = self._cfg
        def _kw(key, default): return [s.strip() for s in cfg.get(key, default).split(",") if s.strip()]
        SURFACE_KEYWORDS   = _kw("surface_keywords", "Cart,Trolley,Table,Desk,Counter,Shelf,Bed,Stand")
        DECOR_KEYWORDS     = _kw("decor_keywords",   "Poster,Clock,Sign,Panel")
        CHARACTER_PREFIXES = _kw("character_prefixes", "Doctor,Nurse,Patient,Surgeon,EMT")
        FLOAT_Y_THRESHOLD = 0.6   # objects above this with no surface below are floating
        SURFACE_RADIUS    = 0.5   # x/z radius to search for a supporting surface
        x_min = float(cfg.get("x_min", -2.8)); x_max = float(cfg.get("x_max", 2.8))
        z_min = float(cfg.get("z_min",  0.1)); z_max = float(cfg.get("z_max", 5.9))

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

            # ── Floating check ────────────────────────────────────────────────
            # Wall-mounted decor near a wall at height is intentional — skip.
            wall_mounted = is_decor and (abs(x) > 2.0 or z < 0.5 or z > 5.5) and y > 1.0
            if is_character or wall_mounted or y <= FLOAT_Y_THRESHOLD:
                continue

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
                out_of_bounds = not (x_min <= x <= x_max and z_min <= z <= z_max)
                tag = "[dispatcher→bounds]" if out_of_bounds else "[dispatcher→surface]"
                # Find nearest surface anywhere in the room to suggest a destination
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

        if flagged:
            logger.info(f"[DispatcherThread] {len(flagged)} object(s) flagged: {flagged}")
        else:
            logger.info("[DispatcherThread] no floating objects detected.")

        # Visual LLM check — catches floating cases the coordinate check missed
        _d_cfg = self._cfg
        _dx_min = float(_d_cfg.get("x_min", -2.8)); _dx_max = float(_d_cfg.get("x_max", 2.8))
        _dz_min = float(_d_cfg.get("z_min",  0.1)); _dz_max = float(_d_cfg.get("z_max", 5.9))
        names_corrected = {ln.split()[1] for ln in corrections if ln.startswith("MoveObject") and len(ln.split()) > 1}
        obj_list = "\n".join(f"  {o['objectName']}: x={o.get('x',0):.2f} y={o.get('y',0):.2f} z={o.get('z',0):.2f}" for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"])
        sys_prompt = (
            "You are checking ONLY for objects that are floating in mid-air with no surface beneath them. "
            "Do NOT flag objects based on their position in the room — mid-room placement is intentional. "
            "Wall-mounted decor (posters, clocks, signs) elevated near a wall are intentional, do not flag them. "
            f"Room bounds: x in [{_dx_min},{_dx_max}], z in [{_dz_min},{_dz_max}]. "
            "Use the screenshots as your primary reference. "
            "Output ONLY MoveObject lines, one per floating object, format:\n"
            "MoveObject <exactName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: [dispatcher→surface] <issue>\n"
            "Do NOT flag anything else. If nothing is wrong, output nothing."
        )
        llm_lines = self._focused_llm("DispatcherThread", sys_prompt, f"Objects:\n{obj_list}")
        new_lines = [ln for ln in llm_lines if not any(n in ln for n in names_corrected)]
        corrections.extend(new_lines)
        if new_lines:
            logger.info(f"[DispatcherThread] LLM visual check added {len(new_lines)} extra correction(s).")

        return corrections

    def _bounds_thread(self, placed_objects: List[Dict]) -> List[str]:
        """Thread 1 (pure Python + LLM visual): enforce room bounds and doorway clearance.

        Catches three categories:
          1. Center position outside hard limits (x/z/y).
          2. Bounding box edges partially clipping through a wall (partial out-of-bounds).
          3. Objects blocking or partially covering the doorway at the front wall.
        Returns a list of ready-to-use MoveObject correction lines.
        """
        cfg = self._cfg
        X_MIN = float(cfg.get("x_min", -2.8)); X_MAX = float(cfg.get("x_max", 2.8))
        Z_MIN = float(cfg.get("z_min",  0.1)); Z_MAX = float(cfg.get("z_max", 5.9))
        Y_MIN = 0.0

        # Doorway geometry — objects within this z-depth of the front wall that overlap
        # the doorway x-range are considered blocking the entrance.
        DOOR_WIDTH   = float(cfg.get("doorway_width", 1.4))
        DOOR_CX      = float(cfg.get("doorway_x",    0.0))
        DOOR_X_MIN   = DOOR_CX - DOOR_WIDTH / 2
        DOOR_X_MAX   = DOOR_CX + DOOR_WIDTH / 2
        DOOR_Z_DEPTH = 1.2   # metres inward from z=0; anything within this zone + overlapping x blocks the door

        corrections: List[str] = []
        handled: set = set()

        for obj in placed_objects:
            name = obj.get("objectName", "")
            if not name or "Cube" in name:
                continue

            x   = float(obj.get("x",   0))
            y   = float(obj.get("y",   0))
            z   = float(obj.get("z",   0))
            rot = float(obj.get("rotationY", 0))

            # Bounding box edges (fall back to center if not present)
            bx_min = float(obj.get("boundsMinX", x))
            bx_max = float(obj.get("boundsMaxX", x))
            bz_min = float(obj.get("boundsMinZ", z))
            bz_max = float(obj.get("boundsMaxZ", z))

            nx, ny, nz = x, y, z
            issues: List[str] = []

            # Half-extents from the real renderer bounds (fall back to 0 if missing)
            half_x = (bx_max - bx_min) / 2
            half_z = (bz_max - bz_min) / 2

            # Valid center range for this object: wall + half_size + margin.
            # This means the object's *edge* always clears the wall, regardless of size.
            MARGIN = 0.05
            cx_min = X_MIN + half_x + MARGIN
            cx_max = X_MAX - half_x - MARGIN
            cz_min = Z_MIN + half_z + MARGIN
            cz_max = Z_MAX - half_z - MARGIN

            if x < cx_min: nx = cx_min; issues.append(f"left edge clips wall (x={x:.2f}, half={half_x:.2f})")
            elif x > cx_max: nx = cx_max; issues.append(f"right edge clips wall (x={x:.2f}, half={half_x:.2f})")
            if z < cz_min: nz = cz_min; issues.append(f"front edge clips wall (z={z:.2f}, half={half_z:.2f})")
            elif z > cz_max: nz = cz_max; issues.append(f"back edge clips wall (z={z:.2f}, half={half_z:.2f})")
            if y < Y_MIN:   ny = Y_MIN; issues.append(f"y={y:.2f} < {Y_MIN}")

            if issues:
                line = (
                    f"MoveObject {name} x={nx:.2f} y={ny:.2f} z={nz:.2f} rotationY={rot:.0f}"
                    f"  — reason: [bounds] {'; '.join(issues)}"
                )
                corrections.append(line)
                handled.add(name)
                logger.info(f"[BoundsThread] queued: {line}")

            # ── 3. Doorway blockage ──────────────────────────────────────────
            if name not in handled:
                near_front = bz_min < DOOR_Z_DEPTH          # object is close to front wall
                x_overlaps = bx_min < DOOR_X_MAX and bx_max > DOOR_X_MIN  # overlaps doorway x-range
                if near_front and x_overlaps:
                    # Move object sideways to clear the doorway — push to whichever side is closer
                    dist_left  = abs(x - DOOR_X_MIN)
                    dist_right = abs(x - DOOR_X_MAX)
                    if dist_left <= dist_right:
                        nx = max(X_MIN, DOOR_X_MIN - (bx_max - bx_min) / 2 - 0.15)
                    else:
                        nx = min(X_MAX, DOOR_X_MAX + (bx_max - bx_min) / 2 + 0.15)
                    line = (
                        f"MoveObject {name} x={nx:.2f} y={y:.2f} z={z:.2f} rotationY={rot:.0f}"
                        f"  — reason: [bounds] blocking/partially covering doorway "
                        f"(door x=[{DOOR_X_MIN:.2f},{DOOR_X_MAX:.2f}], object bx=[{bx_min:.2f},{bx_max:.2f}], bz_min={bz_min:.2f})"
                    )
                    corrections.append(line)
                    handled.add(name)
                    logger.info(f"[BoundsThread] queued: {line}")

        logger.info(f"[BoundsThread] {len(corrections)} bound/doorway corrections generated.")

        # Visual LLM check — catches partial clips and doorway blockage that coordinate math missed
        names_corrected = {ln.split()[1] for ln in corrections if ln.startswith("MoveObject") and len(ln.split()) > 1}
        obj_list = "\n".join(
            f"  {o['objectName']}: x={o.get('x',0):.2f} y={o.get('y',0):.2f} z={o.get('z',0):.2f} "
            f"bx=[{o.get('boundsMinX',o.get('x',0)):.2f},{o.get('boundsMaxX',o.get('x',0)):.2f}] "
            f"bz=[{o.get('boundsMinZ',o.get('z',0)):.2f},{o.get('boundsMaxZ',o.get('z',0)):.2f}]"
            for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"]
        )
        _b_cfg = self._cfg
        _bx_min = float(_b_cfg.get("x_min", -2.8)); _bx_max = float(_b_cfg.get("x_max", 2.8))
        _bz_min = float(_b_cfg.get("z_min",  0.1)); _bz_max = float(_b_cfg.get("z_max", 5.9))
        _bw = float(_b_cfg.get("room_width", 6)); _bd = float(_b_cfg.get("room_depth", 6))
        sys_prompt = (
            "You are checking ONLY for two issues: (1) objects visually outside OR partially clipping "
            "through the room walls — even if just one edge is through; "
            "(2) objects blocking or partially covering the doorway at the front wall. "
            f"Room bounds: x in [{_bx_min},{_bx_max}], z in [{_bz_min},{_bz_max}], y >= 0. "
            f"Walls at x=±{_bw/2}, z=0, z={_bd}. "
            f"Doorway: centered at x={DOOR_CX}, width={DOOR_WIDTH}m (x=[{DOOR_X_MIN:.2f},{DOOR_X_MAX:.2f}]), at the front wall (z≈0). "
            f"Any object within {DOOR_Z_DEPTH}m of z=0 whose x-extent overlaps the doorway range is blocking it and must be moved sideways. "
            "Use the screenshots as your primary reference. "
            "Output ONLY MoveObject lines, one per issue, format:\n"
            "MoveObject <exactName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: <visually clipping wall / blocking doorway>\n"
            "Do NOT flag anything else. If nothing is wrong, output nothing."
        )
        llm_lines = self._focused_llm("BoundsThread", sys_prompt, f"Objects (with bounds):\n{obj_list}")
        new_lines = [ln for ln in llm_lines if not any(n in ln for n in names_corrected)]
        corrections.extend(new_lines)
        if new_lines:
            logger.info(f"[BoundsThread] LLM visual check added {len(new_lines)} extra correction(s).")

        return corrections

    def _collision_thread(self, placed_objects: List[Dict]) -> List[str]:
        """Thread 2 (math + LLM visual): detect pairs of objects that genuinely overlap
        and nudge the less-important one aside.

        Two independent detection paths — a collision is acted on if EITHER fires:
          1. Math:  real bounding-box overlap (pen_x > 0 AND pen_z > 0) using Unity bounds.
                    Falls back to a default assumed size only when bounds data is absent.
          2. Image: LLM vision check on the three screenshots for pairs that look merged.
        """
        CLEARANCE = 0.25          # gap to leave between edges after separating
        DEFAULT_HALF_SIZE = 0.35  # assumed half-size only when bounds fields are missing

        cfg = self._cfg
        X_MIN = float(cfg.get("x_min", -2.8)); X_MAX = float(cfg.get("x_max", 2.8))
        Z_MIN = float(cfg.get("z_min",  0.1)); Z_MAX = float(cfg.get("z_max", 5.9))

        # Exclude structural cubes (walls, floor, ceiling)
        objs = [o for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"]]
        corrections: List[str] = []
        resolved: set = set()  # names already nudged this pass — don't double-move

        # ── PATH 1: Math — bounding-box overlap ────────────────────────────────
        for i in range(len(objs)):
            for j in range(i + 1, len(objs)):
                a, b = objs[i], objs[j]
                na, nb = a["objectName"], b["objectName"]
                if nb in resolved:
                    continue

                ax = float(a.get("x", 0)); az = float(a.get("z", 0))
                bx = float(b.get("x", 0)); bz = float(b.get("z", 0)); by = float(b.get("y", 0))
                rot_b = float(b.get("rotationY", 0))

                has_bounds_a = "boundsMinX" in a and "boundsMaxX" in a
                has_bounds_b = "boundsMinX" in b and "boundsMaxX" in b

                a_xmin = float(a["boundsMinX"]) if has_bounds_a else ax - DEFAULT_HALF_SIZE
                a_xmax = float(a["boundsMaxX"]) if has_bounds_a else ax + DEFAULT_HALF_SIZE
                a_zmin = float(a["boundsMinZ"]) if has_bounds_a else az - DEFAULT_HALF_SIZE
                a_zmax = float(a["boundsMaxZ"]) if has_bounds_a else az + DEFAULT_HALF_SIZE

                b_xmin = float(b["boundsMinX"]) if has_bounds_b else bx - DEFAULT_HALF_SIZE
                b_xmax = float(b["boundsMaxX"]) if has_bounds_b else bx + DEFAULT_HALF_SIZE
                b_zmin = float(b["boundsMinZ"]) if has_bounds_b else bz - DEFAULT_HALF_SIZE
                b_zmax = float(b["boundsMaxZ"]) if has_bounds_b else bz + DEFAULT_HALF_SIZE

                # If bounds collapsed to a degenerate point, expand to assumed size
                if a_xmax - a_xmin < 0.01: a_xmin = ax - DEFAULT_HALF_SIZE; a_xmax = ax + DEFAULT_HALF_SIZE
                if a_zmax - a_zmin < 0.01: a_zmin = az - DEFAULT_HALF_SIZE; a_zmax = az + DEFAULT_HALF_SIZE
                if b_xmax - b_xmin < 0.01: b_xmin = bx - DEFAULT_HALF_SIZE; b_xmax = bx + DEFAULT_HALF_SIZE
                if b_zmax - b_zmin < 0.01: b_zmin = bz - DEFAULT_HALF_SIZE; b_zmax = bz + DEFAULT_HALF_SIZE

                # Actual penetration — must be positive on BOTH axes to be a real overlap
                pen_x = min(a_xmax, b_xmax) - max(a_xmin, b_xmin)
                pen_z = min(a_zmax, b_zmax) - max(a_zmin, b_zmin)
                if pen_x <= 0 or pen_z <= 0:
                    continue  # boxes are separated — no collision

                # Resolve: push b out along the axis of least penetration
                half_b_x = (b_xmax - b_xmin) / 2
                half_b_z = (b_zmax - b_zmin) / 2
                if pen_x <= pen_z:
                    new_bx = (a_xmax + half_b_x + CLEARANCE) if bx >= ax else (a_xmin - half_b_x - CLEARANCE)
                    new_bx = max(X_MIN + half_b_x, min(X_MAX - half_b_x, new_bx))
                    new_bz = bz
                else:
                    new_bz = (a_zmax + half_b_z + CLEARANCE) if bz >= az else (a_zmin - half_b_z - CLEARANCE)
                    new_bz = max(Z_MIN + half_b_z, min(Z_MAX - half_b_z, new_bz))
                    new_bx = bx

                bounds_tag = "exact bounds" if (has_bounds_a and has_bounds_b) else "estimated bounds"
                line = (
                    f"MoveObject {nb} x={new_bx:.2f} y={by:.2f} z={new_bz:.2f} rotationY={rot_b:.0f}"
                    f"  — reason: overlaps {na} (pen_x={pen_x:.2f}m pen_z={pen_z:.2f}m; {bounds_tag})"
                )
                corrections.append(line)
                resolved.add(nb)
                logger.info(f"[CollisionThread][math] {line}")
                self._enqueue_debug_log(
                    f"COLLISION (math) {na} <-> {nb}  pen_x={pen_x:.2f}m pen_z={pen_z:.2f}m  moving {nb} to ({new_bx:.2f}, {by:.2f}, {new_bz:.2f})"
                )

        logger.info(f"[CollisionThread] math path: {len(corrections)} collision(s) found.")

        # ── PATH 2: Image — LLM vision check ──────────────────────────────────
        # Runs independently; only adds objects not already corrected by math.
        _cx_min = float(cfg.get("x_min", -2.8)); _cx_max = float(cfg.get("x_max", 2.8))
        _cz_min = float(cfg.get("z_min",  0.1)); _cz_max = float(cfg.get("z_max", 5.9))
        def _fmt_bounds(o: Dict) -> str:
            bx0 = f"{o['boundsMinX']:.2f}" if "boundsMinX" in o else "?"
            bx1 = f"{o['boundsMaxX']:.2f}" if "boundsMaxX" in o else "?"
            bz0 = f"{o['boundsMinZ']:.2f}" if "boundsMinZ" in o else "?"
            bz1 = f"{o['boundsMaxZ']:.2f}" if "boundsMaxZ" in o else "?"
            return f"bounds_x=[{bx0},{bx1}] bounds_z=[{bz0},{bz1}]"

        obj_list = "\n".join(
            f"  {o['objectName']}: x={o.get('x',0):.2f} y={o.get('y',0):.2f} z={o.get('z',0):.2f}  {_fmt_bounds(o)}"
            for o in placed_objects if o.get("objectName") and "Cube" not in o["objectName"]
        )
        sys_prompt = (
            "You are checking ONLY for objects whose 3D meshes visually intersect or merge in the screenshots. "
            f"Room bounds: x in [{_cx_min},{_cx_max}], z in [{_cz_min},{_cz_max}]. "
            "The scene state already shows each object's bounding box — use it alongside the screenshots. "
            "Flag a pair ONLY if the meshes clearly overlap (not just nearby). "
            "For each overlapping object, move the less important one clear of the other. "
            "Output ONLY MoveObject lines, one per colliding object:\n"
            "MoveObject <exactName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: visually overlaps <otherName>\n"
            "If no meshes overlap, output nothing."
        )
        llm_lines = self._focused_llm("CollisionThread", sys_prompt, f"Objects (with bounds):\n{obj_list}")
        new_lines = [ln for ln in llm_lines if not any(n in ln for n in resolved)]
        for ln in new_lines:
            logger.info(f"[CollisionThread][image] {ln}")
            # extract object name from "MoveObject <name> ..."
            parts = ln.split()
            if len(parts) >= 2:
                self._enqueue_debug_log(f"COLLISION (image) {parts[1]}: {ln}")
        corrections.extend(new_lines)
        if new_lines:
            logger.info(f"[CollisionThread] image path added {len(new_lines)} extra correction(s).")

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
            self._log_reflection(forced, plan_step=plan_step, denied_corrections=[])
            return forced

        # Reset denied corrections accumulator for this round
        self._denied_corrections = []

        plan = self._load_plan()
        plan_str = json.dumps(plan, indent=2)
        plan_step_str = json.dumps(plan_step, indent=2)
        game_state_str = json.dumps(game_state, indent=2)
        recent_reflections = self._load_recent_reflections(limit=5)
        reflections_str = json.dumps(recent_reflections, indent=2)

        # Collect denied corrections from previous rounds to warn the LLM
        prev_denied: List[str] = []
        for entry in recent_reflections:
            prev_denied.extend(entry.get("denied_corrections", []))
        prev_denied = list(dict.fromkeys(prev_denied))  # deduplicate, preserve order

        # Load live scene state (real positions + bounds from Unity) so reflection uses ground-truth data
        placed_objects = self._load_scene_state()
        placed_objects_str = json.dumps(placed_objects, indent=2)
        placed_count = len(placed_objects)
        knowledge_str = "\n".join(
            [f"Knowledge {i+1}: {k.get('text', '')}" for i, k in enumerate(knowledge)]
        )

        # Build a dynamic object inventory from game_state for context injection
        placed_objects_list = game_state.get("placed_objects", [])
        object_names_in_scene = [o.get("objectName", "") for o in placed_objects_list if o.get("objectName")]
        scene_inventory = ", ".join(object_names_in_scene) if object_names_in_scene else "none"

        # Store live scene state and derive name list — both are set before threads launch
        self._scene_state = placed_objects
        exact_scene_names = sorted({o["objectName"] for o in placed_objects if o.get("objectName")})
        self._exact_scene_names = exact_scene_names

        # Load scene params from config (always re-read so any change to experience.txt is picked up)
        self._cfg = self._load_experience_config()
        cfg = self._cfg
        def _kw(key, default): return [s.strip() for s in cfg.get(key, default).split(",") if s.strip()]
        supply_types    = _kw("supply_keywords",    "Cart,Trolley,Basin,Sink,Dispenser,Drip,Stand")
        char_prefixes   = _kw("character_prefixes", "Doctor,Nurse,Patient,Surgeon,EMT")
        small_prop_kws  = _kw("small_prop_keywords","Folder,Box,Syringe,Thermometer,Tray")
        decor_kws       = _kw("decor_keywords",     "Poster,Clock,Sign,Panel")
        surface_kws     = _kw("surface_keywords",   "Cart,Trolley,Table,Desk,Counter,Shelf,Bed,Stand")
        scene_type      = cfg.get("scene_type", "room")
        x_min = float(cfg.get("x_min", -2.8)); x_max = float(cfg.get("x_max", 2.8))
        z_min = float(cfg.get("z_min",  0.1)); z_max = float(cfg.get("z_max", 5.9))
        max_objects = int(cfg.get("max_objects", 25))
        min_objects = int(cfg.get("min_objects", 10))
        room_w = float(cfg.get("room_width",  6))
        room_d = float(cfg.get("room_depth",  6))

        supply_objects    = [n for n in object_names_in_scene if any(t in n for t in supply_types)]
        character_objects = [n for n in object_names_in_scene if any(n.startswith(p) for p in char_prefixes)]
        small_prop_objects= [n for n in object_names_in_scene if any(t in n for t in small_prop_kws)]
        decor_objects     = [n for n in object_names_in_scene if any(t in n for t in decor_kws)]

        exact_names_str = "\n".join(f"  - {n}" for n in exact_scene_names) if exact_scene_names else "  (none yet)"

        wall_x = room_w / 2; wall_z_far = room_d
        wall_near_x = x_max - 0.8; wall_near_z_back = z_max - 0.9; wall_near_z_front = z_min + 0.4
        mid_x = x_max - 0.7

        system_prompt = f"""You are a layout quality inspector for a VR {scene_type} in Unity.
        Room bounds: x in [{x_min}, {x_max}], z in [{z_min}, {z_max}], y >= 0. Walls at x=±{wall_x}, z=0, z={wall_z_far}.

        ══════════════════════════════════════════════════
        STRICT NAME RULE — VIOLATION = INVALID RESPONSE
        ══════════════════════════════════════════════════
        The ONLY valid object names you may reference in any field are:
{exact_names_str}
        ANY name you invent, abbreviate, or guess that is NOT in this list is WRONG.
        Do NOT paraphrase or abbreviate any name (e.g. write the name exactly as it appears in the list above).
        Do NOT use plural or category words ("chairs", "objects near wall").
        If you cannot identify an object by its exact name above, skip it entirely.
        ══════════════════════════════════════════════════

        ══════════════════════════════════════════════════
        THREAD DIVISION — YOUR SCOPE ONLY
        ══════════════════════════════════════════════════
        Two other threads run in parallel and handle:
          • BOUNDS violations (x/z/y out of range), partial wall clips, and doorway blockage — already corrected, do NOT re-check.
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
          Supply objects {supply_objects}: must be near a wall (|x| > {mid_x:.1f} OR z < {wall_near_z_front:.1f} OR z > {wall_near_z_back:.1f}).
            If mid-room, flag by exact name and move to nearest valid wall zone.
          Small props {small_prop_objects}: must be on a surface (y >= 0.6). If y < 0.6, flag by exact name.
            LOGICAL SURFACE RULE — think step-by-step for each prop:
              Step 1: Identify the prop type and its logical surface category.
              Step 2: Find the nearest surface object ({', '.join(surface_kws)}) from the strict name list.
              Step 3: Is that surface type appropriate for this prop?
              Step 4: If the surface is illogical, find the nearest appropriate surface and move there.
            ALSO verify the prop is within 0.5m x/z of a valid logical surface object from the strict name list.
          Decor objects {decor_objects}: must be near a wall (|x| > {wall_near_x:.1f} OR z < {wall_near_z_front:.1f} OR z > {wall_near_z_back:.1f}) at y > 1.0.
            If floating mid-room or too low, flag by exact name.
          Characters {character_objects}: must NOT be inside non-human objects.
            If 2+ characters are within 0.5m of each other, name each and recommend moving one.

        CHECK B — WALKWAY BLOCK: central corridor, |x| < {x_max * 0.43:.1f} must stay clear for primary equipment.
          Any non-primary object in this zone: flag by exact name and move to nearest wall.

        CHECK C — ROTATION:
          Objects near left wall (x < {-wall_near_x:.1f}): rotationY=90 (face right).
          Objects near right wall (x > {wall_near_x:.1f}): rotationY=270 (face left).
          Objects near back wall (z > {wall_near_z_back:.1f}): rotationY=180 (face forward).
          Objects near entry (z < {wall_near_z_front:.1f}): rotationY=0 (face inward).
          Characters: face the room's primary equipment (toward room center).

        REQUIRED FORMAT — one line per problem, exact objectName from strict name list only:
          MoveObject <exactObjectName> x=<X> y=<Y> z=<Z> rotationY=<R>  — reason: <specific issue>
          DeleteObject <exactObjectName>  — reason: <specific issue>
        BANNED: invented names, paraphrased names, category words ("chair", "scanner", "monitor").
        BANNED: "objects look", "characters seem", "scattered", "randomly", "some items", "a few", "various".
        Every objectName MUST appear verbatim in the strict name list. Objects passing all checks: do NOT mention.

        PRIORITY 2 — ROOM FULLNESS:
        Count non-Cube objects in game_state. room_status = "sparse" if < {min_objects} | "good" if {min_objects}-{max_objects} | "cluttered" if > {max_objects}.
        HARD CAP = {max_objects}. If count >= {max_objects}: room_status = "cluttered", suggest_additions = false — no exceptions.
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

        denied_notice = ""
        if prev_denied:
            denied_list = ", ".join(prev_denied)
            denied_notice = (
                f"⚠ PREVIOUS ROUND DENIALS: The following object names you used were NOT found in the scene "
                f"and were rejected — do NOT use them again: {denied_list}. "
                f"You MUST use only the exact names from the STRICT NAME RULE list above.\n\n"
            )

        base_content = f"""{loop_mode_note}{denied_notice}IMPORTANT: Base your visual judgments primarily on the screenshots provided below, not just the object list.
Look at each screenshot carefully before generating edit_commands. The screenshots are ground truth.

        Full Plan:
        {plan_str}

        Current Step:
        {plan_step_str}

        Placed Objects (name, position):
        {game_state_str}

        Already Placed Unity Objects ({placed_count} objects — use this for room_status counting):
        {placed_objects_str}
        NOTE: There are {placed_count} objects in the scene. HARD CAP = {max_objects} meaningful objects (excluding Cube walls/floor/ceiling).
        If count >= {max_objects}: room_status MUST be "cluttered", suggest_additions MUST be false. Do not suggest adding anything.
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
        self._log_reflection(reflection, plan_step=plan_step, denied_corrections=list(self._denied_corrections))
        return reflection
