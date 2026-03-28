import os
import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

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

        placed = []
        for cmd in commands:
            if not isinstance(cmd, dict):
                continue
            action = cmd.get("action", "")
            if action in ("PlaceObject", "CreateObjectByName", "PlaceHuman"):
                placed.append({
                    "action": action,
                    "objectName": cmd.get("objectName") or cmd.get("humanName", ""),
                    "x": cmd.get("x", 0),
                    "y": cmd.get("y", 0),
                    "z": cmd.get("z", 0),
                    "rotationY": cmd.get("rotationY", 0),
                })
        return placed

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

        system_prompt = """You are a layout quality inspector for a Unity VR CT examination room.
        Your PRIMARY job is to fix layout problems. Adding new objects is secondary and only happens when the room is sparse.

        PRIORITY ORDER — work through these in order every call:

        PRIORITY 1 — IDENTIFY LAYOUT ISSUES (always do this first):
        Look at the screenshots and game_state. Identify every object that is out of place OR wrongly rotated.
        Out of place / wrong rotation means ANY of:
        - Object is isolated in the middle of the floor with no wall or furniture nearby.
        - Object is inside the walkway (z=1.5-4.5, |x|<1.2) and is NOT the CT scanner, exam table, or IV stand.
        - Two or more objects are visually overlapping or stacked.
        - A character is standing inside equipment or bunched with 2+ other characters within 0.5m.
        - A supply item (cart, tray, sanitizer) is not against a wall.
        - An object is facing the wrong direction: wall-mounted items should face inward, characters should face
          the scanner/table or room center, carts against the left wall should have rotationY=90,
          carts against the right wall rotationY=270, carts against the back wall rotationY=180.
        List ALL of these in alignment_suggestions with specific objectNames, current positions, and where they should go.

        PRIORITY 2 — ASSESS ROOM FULLNESS:
        After identifying layout fixes, assess whether the room needs more objects.
        Count meaningful objects (exclude walls/floor/ceiling cubes) in game_state.
        - room_status = "sparse"    if fewer than 10 meaningful objects placed.
        - room_status = "good"      if 10-25 meaningful objects and layout is organized.
        - room_status = "cluttered" if more than 25 meaningful objects OR room looks overcrowded in screenshots.
        Set suggest_additions = true ONLY when room_status is "sparse".
        If room_status is "good" or "cluttered", set suggest_additions = false — do NOT add more objects.

        PRIORITY 3 — ADVISE ADDITIONS (only if suggest_additions = true):
        In alignment_suggestions, include specific zones and object types that are empty and need filling.
        Be specific: "back wall x=1.5,z=5.5 needs a supply cart" not "add more equipment".

        alignment_suggestions is read by the agent that executes commands — be precise and use these exact formats:
        - To move/rotate: "MoveObject <objectName> x=<x> y=<y> z=<z> rotationY=<deg>"
        - For additions (sparse only): "PlaceObject <objectName> x=<x> y=<y> z=<z> rotationY=<deg>"
        - For removals: "DeleteObject <objectName>"
        CRITICAL: NEVER suggest placing an object that is already in the scene. To reposition, always use MoveObject.

        game_state also contains last_edit_commands — commands the agent applied last iteration. Do not advise reversing these.
        Do not keep oscillating: if a previous reflection already addressed an issue and the agent acted on it, accept the result unless it is clearly wrong.

        Output ONLY valid JSON:
        {
          "step_complete": "yes" or "no",
          "current_action_thoughts": "zone-by-zone description of what you see in the screenshots",
          "next_action_thoughts": "remaining layout issues to address next",
          "alignment_suggestions": "precise advice for the agent: moves, rotations, deletions first; additions only if sparse",
          "layout_comments": "2-4 sentence plain-English verdict — does it look like a real CT room, what's working, what's wrong",
          "room_status": "sparse" or "good" or "cluttered",
          "suggest_additions": true or false
        }"""

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
        NOTE: There are {placed_count} objects already in the Unity scene. Count these (excluding Cube walls/floor/ceiling) for room_status.
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

        max_retries = 3
        retry_delay = 2.0

        # Guard: if no screenshots are available, the LLM cannot visually evaluate the room.
        # Override room_status to "good" and suggest_additions to False so we don't blindly
        # add objects based on coordinate-only guesswork.
        if not view_data:
            logger.info("No screenshots available — skipping visual evaluation, using coordinate-only reflection.")

        # Prepare messages — screenshots come first so the model sees images before text context
        messages = [{"role": "system", "content": system_prompt}]

        if view_data:
            image_content = [{"type": "text", "text": "These are the current orthographic screenshots of the scene. Study them carefully before reading the object list.\nXZ=top-down (shows x/z layout), XY=front, YZ=side."}]
            for label, encoded in view_data:
                image_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
            messages.append({"role": "user", "content": image_content})

        messages.append({"role": "user", "content": base_content})

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

                content = ""
                for chunk in completion:
                    if chunk.choices and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, "content") and delta.content:
                            content += delta.content

                time.sleep(0.1)
                content = content.strip()
                if not content:
                    raise ValueError("Empty response from Groq API")

                json_str = self._extract_json_object(content)
                reflection_dict = json.loads(json_str)

                step_complete = reflection_dict.get("step_complete", "no").strip().lower()
                if step_complete not in {"yes", "no"}:
                    step_complete = "no"

                raw_status = str(reflection_dict.get("room_status", "good")).strip().lower()
                room_status = raw_status if raw_status in {"sparse", "good", "cluttered"} else "good"
                suggest_additions = bool(reflection_dict.get("suggest_additions", False)) and room_status == "sparse"

                # Override: without screenshots the LLM is guessing — never suggest additions blindly
                if not view_data:
                    if room_status == "sparse":
                        room_status = "good"  # don't trust coordinate-only "sparse" judgment
                    suggest_additions = False

                reflection = ReflectionResult(
                    step_complete=step_complete,
                    current_action_thoughts=str(reflection_dict.get("current_action_thoughts", "")).strip(),
                    next_action_thoughts=str(reflection_dict.get("next_action_thoughts", "")).strip(),
                    alignment_suggestions=str(reflection_dict.get("alignment_suggestions", "")).strip(),
                    layout_comments=str(reflection_dict.get("layout_comments", "")).strip(),
                    room_status=room_status,
                    suggest_additions=suggest_additions,
                )
                self._log_reflection(reflection, plan_step=plan_step)
                return reflection

            except (ValueError, json.JSONDecodeError) as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Invalid reflection response (attempt {attempt + 1}/{max_retries}). Retrying: {str(e)}"
                    )
                    time.sleep(retry_delay)
                    continue
                logger.error(f"Failed to parse reflection after {max_retries} attempts: {str(e)}")
                raise
