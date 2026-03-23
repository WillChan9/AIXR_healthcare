import os
import json
import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv()

def setup_logger(name: str, log_file: str, level=logging.DEBUG) -> logging.Logger:
    """Set up a logger with file and console handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        logger.handlers.clear()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_path = os.path.join(project_root, log_file)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

logger = setup_logger("ReflectionModule", "api_files/reflection_log.log")

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
    def __init__(self):
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")
        self.groq_client = Groq(api_key=groq_api_key)

        self.actions_file = "api_files/actions.json"
        self.plan_file = "api_files/plan.json"
        self.reflection_file = "api_files/reflection.json"

        # Derive the 3-view screenshot base path from env var (strip .png if present)
        raw_path = os.getenv("UNITY_SCREENSHOT_PATH", "api_files/scene_screenshot.png")
        base = raw_path[:-4] if raw_path.endswith(".png") else raw_path
        self.screenshot_xz = base + "_xz.png"  # top-down
        self.screenshot_xy = base + "_xy.png"  # front
        self.screenshot_yz = base + "_yz.png"  # side

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

        plan = self._load_plan()
        plan_str = json.dumps(plan, indent=2)
        plan_step_str = json.dumps(plan_step, indent=2)
        game_state_str = json.dumps(game_state, indent=2)
        recent_reflections = self._load_recent_reflections(limit=5)
        reflections_str = json.dumps(recent_reflections, indent=2)
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

        alignment_suggestions is read by the agent that executes commands — be precise:
        - For wrong rotation: "Rotate <objectName> to rotationY=90 (faces inward from left wall)"
        - For additions (sparse only): "Add a supply cart to back wall at x=1.5,z=5.5"
        - For removals: "Delete <objectName> — does not belong in a CT room"

        game_state also contains last_edit_commands — commands the agent applied last iteration. Do not advise reversing these.

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

        Relevant Knowledge:
        {knowledge_str}

        Recent Reflections:
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
