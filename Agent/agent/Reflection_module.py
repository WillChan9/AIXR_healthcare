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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_complete": self.step_complete,
            "current_action_thoughts": self.current_action_thoughts,
            "next_action_thoughts": self.next_action_thoughts,
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
        plan = self._load_plan()
        recent_actions = self._load_recent_actions(limit=12)
        knowledge = relevant_knowledge or []
        is_loop_step = isinstance(plan_step, dict) and plan_step.get("step") == "loop"

        plan_str = json.dumps(plan, indent=2)
        plan_step_str = json.dumps(plan_step, indent=2)
        game_state_str = json.dumps(game_state, indent=2)
        actions_str = json.dumps(recent_actions, indent=2)
        recent_reflections = self._load_recent_reflections(limit=5)
        reflections_str = json.dumps(recent_reflections, indent=2)
        knowledge_str = "\n".join(
            [f"Knowledge {i+1}: {k.get('text', '')}" for i, k in enumerate(knowledge)]
        )

        system_prompt = """You are a verification module. You do NOT classify, interpret, or infer concepts.
        You ONLY verify whether all constraints implied by the plan step are explicitly satisfied by visual evidence.

        Process you MUST follow internally:
        1. Treat EVERY word in the plan step as potentially introducing a constraint.
        2. Separate constraints into:
        - Observable (can be confirmed directly from the screenshot)
        - Non-observable (would require interpretation or assumption)
        3. A step can ONLY be complete if:
        - All observable constraints are satisfied AND
        - No non-observable constraints are required to judge completion

        Critical rule:
        If a plan step uses descriptive or conceptual language that cannot be reduced to direct visual checks, the step is NOT complete.

        You are NOT allowed to:
        - Decide whether something "counts as" a category
        - Use phrases like "suggests", "appears", "looks like"
        - Infer structure, intent, or design from distribution alone
        - Treat randomness or loose organization as satisfying any constraint

        Count-only satisfaction is valid ONLY when the plan step contains no other descriptive or relational language.

        If the layout could plausibly be the result of random placement, you MUST return "no".

        Evidence priority:
        1) Screenshot visuals ONLY
        2) Game state ONLY if directly visible

        Output ONLY:
        {
        "step_complete": "yes" or "no",
        "current_action_thoughts": "Explicit constraint checks and failures",
        "next_action_thoughts": "Exact missing or violated constraints"
        }

        Default to "no". Use "yes" only if every constraint is directly and mechanically verified."""

        loop_mode_note = ""
        if is_loop_step:
            loop_mode_note = (
                "LOOP MODE: Focus on evaluating loop conditions and monitored values in the current step. "
                "Only mark step_complete as yes if loop conditions are satisfied.\n\n"
            )

        base_content = f"""{loop_mode_note}Generate your JSON reflection based on the information below.

        Plan (full):
        {plan_str}

        Current Step:
        {plan_step_str}

        Recent Actions:
        {actions_str}

        Game State:
        {game_state_str}

        Relevant Knowledge:
        {knowledge_str}

        Recent Reflections:
        {reflections_str}
        """

         # Get screenshots from the game/screenshots folder
        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "game", "screenshots")
        minimap_path = os.path.join(screenshots_dir, "minimap_screenshot.png")
        game_path = os.path.join(screenshots_dir, "game_screenshot.png")
        
        # Read and encode screenshots
        import base64
        game_data = None
        minimap_data = None
        try:
            with open(minimap_path, "rb") as image_file:
                minimap_data = base64.b64encode(image_file.read()).decode('utf-8')
            
            with open(game_path, "rb") as image_file:
                game_data = base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            logger.warning(f"Error reading screenshots: {str(e)}")

        max_retries = 3
        retry_delay = 2.0
         # Prepare messages for Groq API
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": base_content},
        ]
        
        # Add images if available
        if game_data and minimap_data:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "Use these screenshots as the primary evidence:"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{game_data}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{minimap_data}"}},
                ],
            })

        for attempt in range(max_retries):
            try:
                completion = self.groq_client.chat.completions.create(
                    model="meta-llama/llama-4-maverick-17b-128e-instruct",
                    messages=messages,
                    temperature=0,
                    max_completion_tokens=512,
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

                reflection = ReflectionResult(
                    step_complete=step_complete,
                    current_action_thoughts=str(reflection_dict.get("current_action_thoughts", "")).strip(),
                    next_action_thoughts=str(reflection_dict.get("next_action_thoughts", "")).strip(),
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
