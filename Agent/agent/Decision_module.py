import os
import json
import logging
from typing import List, Dict, Tuple, Optional, Any, Union
from dataclasses import dataclass
from enum import Enum
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
from groq import Groq
import time

# Handle both package import and direct execution
try:
    from .Parser_module import Parser
    from .Sensing_module import GameSensor, Position, ObjectType, GameObject
    from .Context_module import VectorStorage
    from .logger import get_logger
except ImportError:
    # Handle direct execution
    import sys
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    from agent.Parser_module import Parser
    from agent.Sensing_module import GameSensor, Position, ObjectType, GameObject
    from agent.Context_module import VectorStorage
    from agent.logger import get_logger

# Load environment variables
load_dotenv()

# Initialize logger — writes to combined agent.log + per-module decision_log.log + console
logger = get_logger('DecisionMaker', module_log_file='api_files/decision_log.log')

class ActionType(Enum):
    MOVE = "move"
    DELETE = "delete"
    GENERATE = "generate"
    UNITY = "unity"  # Unity environment actions (PlaceObject, PlaceHuman, etc.)

@dataclass
class GameDecision:
    action_type: ActionType
    # MOVE action parameters
    object_id: Optional[str] = None  # For MOVE and DELETE actions (e.g., "bomb_0", "heart_1", "wall_2")
    position: Optional[Position] = None     # For MOVE action
    
    # GENERATE action parameters
    object_type: Optional[str] = None       # For GENERATE action ("bomb", "heart", "wall", "fire", "watergun", "treasure")
    positions: Optional[List[Position]] = None  # For GENERATE action (multiple positions)
    # Additional parameters for wall generation
    width: Optional[int] = None
    height: Optional[int] = None
    angle: Optional[int] = None
    
    # UNITY action parameters (for Unity environment control)
    unity_action: Optional[str] = None      # Unity action name (e.g., "PlaceObject", "PlaceHuman", "SetTimeOfDay")
    object_name: Optional[str] = None       # For PlaceObject: prefab name in Resources/Prefabs/
    human_name: Optional[str] = None       # For PlaceHuman: prefab name in Resources/Prefabs/
    x: Optional[float] = None              # Position X (Unity world space)
    y: Optional[float] = None              # Position Y (Unity world space)
    z: Optional[float] = None              # Position Z (Unity world space)
    rotation_y: Optional[float] = None     # Rotation Y (degrees)
    scale_x: Optional[float] = None         # Scale X (for CreateObjectByName)
    scale_y: Optional[float] = None         # Scale Y (for CreateObjectByName)
    scale_z: Optional[float] = None         # Scale Z (for CreateObjectByName)
    hour: Optional[float] = None           # For SetTimeOfDay
    enabled: Optional[bool] = None         # For SetFogEnabled, etc.
    density: Optional[float] = None        # For SetFogDensity
    r: Optional[float] = None              # Color R component (0-1)
    g: Optional[float] = None              # Color G component (0-1)
    b: Optional[float] = None              # Color B component (0-1)
    gravity_y: Optional[float] = None     # For SetGravity
    scale: Optional[float] = None          # For SetTimeScale, SetLightIntensity
    controller_path: Optional[str] = None  # For animation: controller path in Resources/
    state_name: Optional[str] = None       # For animation: state name
    cross_fade_time: Optional[float] = None # For animation: cross fade duration
    
    def __post_init__(self):
        # Validate action parameters
        if self.action_type == ActionType.MOVE:
            if not self.object_id or not self.position:
                raise ValueError("MOVE action requires both object_id and position")
            if not self.object_id.startswith(("bomb_", "heart_", "wall_", "treasure", "player", "watergun_")):
                raise ValueError("Invalid object_id for MOVE action")
                
        elif self.action_type == ActionType.DELETE:
            if not self.object_id:
                raise ValueError("DELETE action requires object_id")
            if not self.object_id.startswith(("bomb_", "heart_", "wall_", "fire_", "treasure", "watergun_")):
                raise ValueError("Invalid object_id for DELETE action")
                
        elif self.action_type == ActionType.GENERATE:
            if not self.object_type or not self.positions:
                raise ValueError("GENERATE action requires object_type and positions")
            if self.object_type not in ["bomb", "heart", "wall", "fire", "treasure", "watergun"]:
                raise ValueError("Invalid object_type for GENERATE action")
            # Additional validation for wall generation
            if self.object_type == "wall":
                if not all(v is not None for v in [self.width, self.height, self.angle]):
                    raise ValueError("Wall generation requires width, height, and angle")
            # object_id is optional for GENERATE (will be auto-generated if not provided)
        
        elif self.action_type == ActionType.UNITY:
            if not self.unity_action:
                raise ValueError("UNITY action requires unity_action field")
            # Validate Unity action specific parameters
            if self.unity_action == "PlaceObject":
                if not self.object_name:
                    raise ValueError("PlaceObject requires object_name")
                if self.x is None or self.y is None or self.z is None:
                    raise ValueError("PlaceObject requires x, y, z coordinates")
            elif self.unity_action == "PlaceHuman":
                if not self.human_name:
                    raise ValueError("PlaceHuman requires human_name")
                if self.x is None or self.y is None or self.z is None:
                    raise ValueError("PlaceHuman requires x, y, z coordinates")
            elif self.unity_action == "SetTimeOfDay":
                if self.hour is None:
                    raise ValueError("SetTimeOfDay requires hour")
    
    def to_dict(self) -> Dict:
        """Convert the decision to a dictionary for JSON serialization."""
        result = {
            "action_type": self.action_type.value,
            "object_id": self.object_id,
            "position": self.position,
            "object_type": self.object_type,
            "positions": self.positions,
            "width": self.width,
            "height": self.height,
            "angle": self.angle
        }
        
        # Add Unity action fields if this is a Unity action
        if self.action_type == ActionType.UNITY:
            result["unity_action"] = self.unity_action
            result["action"] = self.unity_action  # Also add as "action" for Operation_module compatibility
            if self.object_name is not None:
                result["objectName"] = self.object_name
            if self.human_name is not None:
                result["humanName"] = self.human_name
            if self.x is not None:
                result["x"] = self.x
            if self.y is not None:
                result["y"] = self.y
            if self.z is not None:
                result["z"] = self.z
            if self.rotation_y is not None:
                result["rotationY"] = self.rotation_y
            if self.scale_x is not None:
                result["scaleX"] = self.scale_x
            if self.scale_y is not None:
                result["scaleY"] = self.scale_y
            if self.scale_z is not None:
                result["scaleZ"] = self.scale_z
            if self.hour is not None:
                result["hour"] = self.hour
            if self.enabled is not None:
                result["enabled"] = self.enabled
            if self.density is not None:
                result["density"] = self.density
            if self.r is not None:
                result["r"] = self.r
            if self.g is not None:
                result["g"] = self.g
            if self.b is not None:
                result["b"] = self.b
            if self.gravity_y is not None:
                result["gravityY"] = self.gravity_y
            if self.scale is not None:
                result["scale"] = self.scale
            if self.controller_path is not None:
                result["controllerPathInResources"] = self.controller_path
            if self.state_name is not None:
                result["stateName"] = self.state_name
            if self.cross_fade_time is not None:
                result["crossFadeTime"] = self.cross_fade_time
        
        return result
    

class DecisionMaker:
    def __init__(self):
        # Initialize OpenAI
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
        self.client = OpenAI()
        
        # Initialize Groq
        self.groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        
        # Initialize modules
        self.parser = Parser()
        self.sensor = GameSensor()
        self.vector_storage = VectorStorage()
        
        # Decision history for context
        self.decision_history: List[GameDecision] = []
        self.max_history = 10

        self.actions_file = "api_files/actions.json"  # For Operation module
        self.plan_file = "api_files/plan.json"
        self.reflection_file = "api_files/reflection.json"
        
        # Track object ID counters for generating new IDs
        self.object_id_counters = {
            "wall": 0,
            "bomb": 0,
            "heart": 0,
            "fire": 0,
            "treasure": 0,
            "watergun": 0
        }
        
    def _load_plan(self) -> Dict:
        """Load the plan from plan.json file."""
        # Get project root (one level up from agent/)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        plan_path = os.path.join(project_root, self.plan_file)
        if not os.path.exists(plan_path):
            raise FileNotFoundError(f"Plan file not found: {plan_path}")
        
        with open(plan_path, 'r') as f:
            return json.load(f)

    def _load_latest_reflection(self) -> Dict:
        """Load the most recent reflection entry from reflection.json."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        reflection_path = os.path.join(project_root, self.reflection_file)
        if not os.path.exists(reflection_path):
            return {}

        try:
            with open(reflection_path, "r") as f:
                content = f.read()
                if not content:
                    return {}
                data = json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read reflection.json: {str(e)}")
            return {}

        if isinstance(data, list) and data:
            return data[-1]
        if isinstance(data, dict):
            return data
        return {}

    def _load_placed_objects(self) -> List[Dict]:
        """Load the list of already-placed Unity objects from unity_commands.json.
        Returns a list of dicts with objectName, position, rotationY for each placed object.
        This prevents the LLM from re-placing objects that already exist in the scene."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
                    "executed": cmd.get("executed", False),
                })
        return placed
    
    def _repair_incomplete_json(self, json_str: str) -> str:
        """Try to repair incomplete JSON by adding missing closing brackets and braces."""
        if not json_str:
            return json_str
        
        result = json_str.strip()
        
        # Remove trailing commas before closing brackets/braces
        import re
        result = re.sub(r',\s*([}\]])', r'\1', result)
        
        # Count brackets and braces properly (accounting for strings)
        open_brackets = 0
        close_brackets = 0
        open_braces = 0
        close_braces = 0
        in_string = False
        escape_next = False
        
        for char in result:
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            
            if char == '[':
                open_brackets += 1
            elif char == ']':
                close_brackets += 1
            elif char == '{':
                open_braces += 1
            elif char == '}':
                close_braces += 1
        
        # Add missing closing brackets and braces in the correct order
        # Close braces first (inner objects), then brackets (arrays)
        missing_braces = open_braces - close_braces
        missing_brackets = open_brackets - close_brackets
        
        if missing_braces > 0:
            result += '}' * missing_braces
        if missing_brackets > 0:
            result += ']' * missing_brackets
        
        return result
    
    def _get_next_object_id(self, object_type: str, game_state: Dict) -> str:
        """Generate the next available object ID for a given object type."""
        # First, check existing objects in game state to find the highest ID
        objects = game_state.get("objects", {})
        max_id = -1
        
        if object_type == "wall":
            for wall in objects.get("walls", []):
                if "id" in wall:
                    try:
                        id_num = int(wall["id"].split("_")[1])
                        max_id = max(max_id, id_num)
                    except (ValueError, IndexError):
                        pass
        elif object_type == "bomb":
            for bomb in objects.get("bombs", []):
                if "id" in bomb:
                    try:
                        id_num = int(bomb["id"].split("_")[1])
                        max_id = max(max_id, id_num)
                    except (ValueError, IndexError):
                        pass
        elif object_type == "heart":
            for heart in objects.get("hearts", []):
                if "id" in heart:
                    try:
                        id_num = int(heart["id"].split("_")[1])
                        max_id = max(max_id, id_num)
                    except (ValueError, IndexError):
                        pass
        elif object_type == "fire":
            # Fires don't have explicit IDs in game state, use counter
            max_id = self.object_id_counters.get("fire", -1)
        elif object_type == "watergun":
            for watergun in objects.get("waterguns", []):
                if "id" in watergun:
                    try:
                        id_num = int(watergun["id"].split("_")[1])
                        max_id = max(max_id, id_num)
                    except (ValueError, IndexError):
                        pass
        elif object_type == "treasure":
            # Treasure is usually singular, but check if it exists
            if "treasure" in objects:
                return "treasure"
            return "treasure"
        
        # Use the maximum found ID + 1, or use counter if no objects found
        next_id = max(max_id + 1, self.object_id_counters.get(object_type, 0))
        self.object_id_counters[object_type] = next_id + 1
        
        if object_type == "treasure":
            return "treasure"
        else:
            return f"{object_type}_{next_id}"
    
    def _log_decision(self, decision_data: Union[Dict, str, List], executed: bool, complete: bool = False):
        """Save decision to actions.json (for Operation module) and log to decision_log.log.
        
        Args:
            decision_data: Single decision dict or list of decision dicts
            executed: Whether the decision was executed
            complete: Whether the action/step is complete
        """
        # Log to logger
        if isinstance(decision_data, list):
            logger.info(f"Generated {len(decision_data)} actions - Executed: {executed}, Complete: {complete}")
            for i, action in enumerate(decision_data):
                if isinstance(action, dict):
                    action_type = action.get("action_type", "unknown")
                    obj_id = action.get("object_id") or action.get("object_type", "N/A")
                    logger.info(f"  Action {i+1}: {action_type} - {obj_id}")
        elif isinstance(decision_data, dict):
            action_type = decision_data.get("action_type", "unknown")
            obj_id = decision_data.get("object_id") or decision_data.get("object_type", "N/A")
            logger.info(f"Action: {action_type} - {obj_id} - Executed: {executed}, Complete: {complete}")
        else:
            logger.info(f"Status: {decision_data} - Executed: {executed}, Complete: True")
        
        # Save to actions.json for Operation module
        # If decision_data is a list, add complete flag to each action
        if isinstance(decision_data, list):
            for action in decision_data:
                if isinstance(action, dict):
                    action["complete"] = complete
            entry = {
                "timestamp": time.time(),
                "actions": decision_data,
                "executed": executed,
                "complete": complete
            }
        elif isinstance(decision_data, dict):
            # Single action
            decision_data["complete"] = complete
            entry = {
                "timestamp": time.time(),
                "action": decision_data,
                "executed": executed,
                "complete": complete
            }
        else:
            # Other string (shouldn't happen in normal flow)
            entry = {
                "timestamp": time.time(),
                "status": decision_data,
                "executed": executed,
                "complete": True
            }
        
        existing_data = []
        # Read existing file if it exists
        actions_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), self.actions_file)
        if os.path.exists(actions_path):
            try:
                with open(actions_path, 'r') as f:
                    content = f.read()
                    if content:
                        existing_data = json.loads(content)
            except (json.JSONDecodeError, IOError):
                existing_data = []
        
        # Append new entry
        existing_data.append(entry)
        
        # Write back to file
        with open(actions_path, 'w') as f:
            json.dump(existing_data, f, indent=2)
        
    def make_decision(self, planning_steps: List[Dict], game_state: Dict, 
                     relevant_knowledge: List[Dict]) -> Union[GameDecision, str]:
        """Make a decision based on planning steps, game state, and relevant knowledge.
        
        Args:
            planning_steps: List of planning steps from the parser
            game_state: Current game state from game_state.json
            relevant_knowledge: List of relevant knowledge from RAG
            
        Returns:
            A GameDecision object
        """
        # Format game state
        game_state_str = json.dumps(game_state, indent=2)
        
        # Format planning steps
        planning_steps_str = json.dumps(planning_steps, indent=2)
        
        # Format relevant knowledge
        knowledge_str = "\n".join([f"Knowledge {i+1}: {k['text']}" for i, k in enumerate(relevant_knowledge)])

        # Load latest reflection
        reflection = self._load_latest_reflection()
        reflection_str = json.dumps(reflection, indent=2) if reflection else "{}"
        
        # Get screenshots from the game/screenshots folder
        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "game", "screenshots")
        minimap_path = os.path.join(screenshots_dir, "minimap_screenshot.png")
        game_path = os.path.join(screenshots_dir, "game_screenshot.png")
        
        # Read and encode screenshots
        import base64
        try:
            with open(minimap_path, "rb") as image_file:
                minimap_data = base64.b64encode(image_file.read()).decode('utf-8')
            
            with open(game_path, "rb") as image_file:
                game_data = base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            print(f"Error reading screenshots: {str(e)}")
        
        # Create the base message content
        base_content = f"""Current Game State:
        {game_state_str}
        
        Planning Steps:
        {planning_steps_str}
        
        Relevant Knowledge:
        {knowledge_str}

        Latest Reflection:
        {reflection_str}
        
        Generate a decision for the next action:"""
        
        # Create messages with screenshots
        system_messages = """You are a game decision maker that generates specific actions based on:
             1. Current game state
             2. Planning steps from the parser
             3. Relevant game knowledge
             4. Previous decisions
             4b. Latest reflection on plan step completion
             5. Visual context from screenshots:
                - The game screenshot shows immediate surroundings
                - The minimap shows the entire map with:
                  * Black dot: player
                  * Brown circles: walls
                  * Red circles: hearts (health items)
                  * Gold circle: treasure
             
             Your task is to either:
             1. Generate a single decision in the following JSON format:
             {
                 "action_type": "one of: move, delete, generate",
                 "object_id": "the object ID to interact with (for move/delete)",
                 "position": [x,y] (destination for move),
                 "object_type": "type of object to generate (for generate)",
                 "positions": [[x1,y1], ...] (for generate),
                 "width": int (for wall generation),
                 "height": int (for wall generation),
                 "angle": int (for wall generation)
             }
             
             
             OR
             
             2. Generate actions based on the current planning step
             
             Rules:
             1. The decision must be specific and actionable
            2. For MOVE/DELETE: object_id must be a valid game object ID (e.g., "bomb_0", "heart_1")
            3. For GENERATE: object_type must be one of: "bomb", "heart", "wall", "fire", "treasure", "watergun"
             4. For wall generation: must include width, height, and angle
             5. Positions must be within the game map bounds
             6. The decision should align with the current planning step
             7. Consider the game state, especially player health and position
             8. Use visual context from both screenshots to make informed decisions
             9. Return only the JSON decision, no explanation"""
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-5-nano",
                messages=[
                    {"role": "system", "content": system_messages},
                    {"role": "user", "content": [
                        {"type": "text", "text": base_content},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{game_data}"}},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{minimap_data}"}}
                    ]}
                ]
            )
            
            # Get the response content
            content = response.choices[0].message.content.strip()
            
            # Parse the response into a GameDecision
            decision_dict = json.loads(content)
            
            # Create GameDecision object based on action type
            if decision_dict["action_type"] == "move":
                decision = GameDecision(
                    action_type=ActionType.MOVE,
                    object_id=decision_dict["object_id"],
                    position=decision_dict["position"]
                )
            elif decision_dict["action_type"] == "delete":
                decision = GameDecision(
                    action_type=ActionType.DELETE,
                    object_id=decision_dict["object_id"]
                )
            elif decision_dict["action_type"] == "generate":
                positions = decision_dict["positions"]
                decision = GameDecision(
                    action_type=ActionType.GENERATE,
                    object_type=decision_dict["object_type"],
                    positions=positions,
                    width=decision_dict.get("width"),
                    height=decision_dict.get("height"),
                    angle=decision_dict.get("angle")
                )
            else:
                raise ValueError(f"Invalid action type: {decision_dict['action_type']}")
            
            # Add to decision history
            self.decision_history.append(decision)
            if len(self.decision_history) > self.max_history:
                self.decision_history.pop(0)
                
            self._log_decision(decision.to_dict(), executed=False, complete=False)

            return decision
            
        except Exception as e:
            logger.error(f"Error making decision: {str(e)}")
            raise

    def make_decision_groq(self, planning_steps: List[Dict], game_state: Dict, 
                          relevant_knowledge: List[Dict]) -> Union[List[GameDecision], str]:
        """Make decisions using Groq API based on plan.json, game state, and relevant knowledge.
        
        Args:
            planning_steps: List of planning steps from the parser (not used, plan.json is used instead)
            game_state: Current game state from game_state.json
            relevant_knowledge: List of relevant knowledge from RAG
            
        Returns:
            A list of GameDecision objects (action space)
        """
        
        # Calculate viewport bounds based on player position and screen size
        player_pos = game_state.get("player", {}).get("position", [100, 100])
        player_x, player_y = player_pos[0], player_pos[1]
        
        # Get screen dimensions from game state config
        screen_width = game_state.get("config", {}).get("screen_width", 800)
        screen_height = game_state.get("config", {}).get("screen_height", 600)
        map_width = game_state.get("config", {}).get("map_width", 2000)
        map_height = game_state.get("config", {}).get("map_height", 2000)
        
        # Calculate viewport bounds (centered on player, constrained to map)
        viewport_min_x = max(0, player_x - screen_width // 2)
        viewport_max_x = min(map_width, player_x + screen_width // 2)
        viewport_min_y = max(0, player_y - screen_height // 2)
        viewport_max_y = min(map_height, player_y + screen_height // 2)
        
        logger.info(f"Viewport bounds: X=[{viewport_min_x:.0f}, {viewport_max_x:.0f}], Y=[{viewport_min_y:.0f}, {viewport_max_y:.0f}] (Player at [{player_x:.0f}, {player_y:.0f}])")
        
        # Format game state
        game_state_str = json.dumps(game_state, indent=2)
        
        # Format plan - check if this is a loop step
        is_loop_step = len(planning_steps) > 0 and planning_steps[0].get('step') == 'loop'
        plan_step_str = json.dumps(planning_steps, indent=2)
        
        # Format relevant knowledge
        knowledge_str = "\n".join([f"Knowledge {i+1}: {k['text']}" for i, k in enumerate(relevant_knowledge)])
        
        # Extract loop monitoring info if this is a loop step
        loop_instructions = ""
        if is_loop_step and len(planning_steps) > 0:
            loop_step = planning_steps[0]
            monitor_vars = loop_step.get('monitor', [])
            conditions = loop_step.get('conditions', [])
            
            # Calculate monitored values from game state
            monitored_values = {}
            player_pos = game_state.get("player", {}).get("position", [100, 100])
            
            # Find treasure position - check both objects.treasure and search all objects
            treasure_pos = None
            objects = game_state.get("objects", {})
            
            # Check if treasure is in objects.treasure
            if "treasure" in objects:
                treasure_list = objects["treasure"]
                if isinstance(treasure_list, list) and len(treasure_list) > 0:
                    treasure_obj = treasure_list[0]
                    if isinstance(treasure_obj, dict) and "position" in treasure_obj:
                        treasure_pos = treasure_obj["position"]
                    elif isinstance(treasure_obj, list) and len(treasure_obj) >= 2:
                        treasure_pos = treasure_obj
            
            # Also check all object types for treasure (it might be stored differently)
            if not treasure_pos:
                for obj_type, obj_list in objects.items():
                    if isinstance(obj_list, list):
                        for obj in obj_list:
                            if isinstance(obj, dict):
                                obj_id = obj.get("id", "")
                                obj_type_field = obj.get("type", "")
                                if "treasure" in obj_id.lower() or "treasure" in obj_type_field.lower():
                                    if "position" in obj:
                                        treasure_pos = obj["position"]
                                        break
                            if treasure_pos:
                                break
                    if treasure_pos:
                        break
            
            # Calculate player_treasure_distance
            if treasure_pos and isinstance(treasure_pos, (list, tuple)) and len(treasure_pos) >= 2:
                import math
                dx = player_pos[0] - treasure_pos[0]
                dy = player_pos[1] - treasure_pos[1]
                distance = math.sqrt(dx*dx + dy*dy)
                monitored_values["player_treasure_distance"] = distance
                monitored_values["player_position"] = player_pos
                monitored_values["treasure_position"] = treasure_pos
            
            # Count fires
            fires = game_state.get("objects", {}).get("fires", [])
            monitored_values["fire_count"] = len(fires)
            
            # Count fires around treasure
            fires_around_treasure = 0
            if treasure_pos and isinstance(treasure_pos, (list, tuple)) and len(treasure_pos) >= 2:
                for fire in fires:
                    fire_pos = fire.get("position") if isinstance(fire, dict) else fire
                    if isinstance(fire_pos, (list, tuple)) and len(fire_pos) >= 2:
                        import math
                        dx = fire_pos[0] - treasure_pos[0]
                        dy = fire_pos[1] - treasure_pos[1]
                        dist = math.sqrt(dx*dx + dy*dy)
                        if dist < 150:  # Within 150 pixels of treasure
                            fires_around_treasure += 1
            monitored_values["fires_around_treasure"] = fires_around_treasure
            
            loop_instructions = f"""
            
            LOOP MONITORING MODE:
            You are monitoring the game state and evaluating loop conditions.
            
            Monitored Values:
            {json.dumps(monitored_values, indent=2)}
            
            Loop Conditions to Evaluate:
            {json.dumps(conditions, indent=2)}
            
            Instructions:
            1. Evaluate each condition in the "conditions" list based on the monitored values
            2. If a condition is met (e.g., "player_treasure_distance < 100 AND fires_around_treasure < 2"), generate the corresponding action
            3. Use the "then" action description, "object_type", "count", and "placement" from the condition to generate actions
            4. Only generate actions if conditions are met - if no conditions are met, return an empty array []
            5. For fire placement around treasure, calculate positions at angles (0°, 180°) or (90°, 270°) at 50-80 pixels from treasure center
            """
        
        # Get screenshots from the game/screenshots folder
        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "game", "screenshots")
        minimap_path = os.path.join(screenshots_dir, "minimap_screenshot.png")
        game_path = os.path.join(screenshots_dir, "game_screenshot.png")
        
        # Read and encode screenshots
        import base64
        try:
            with open(minimap_path, "rb") as image_file:
                minimap_data = base64.b64encode(image_file.read()).decode('utf-8')
            
            with open(game_path, "rb") as image_file:
                game_data = base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            logger.warning(f"Error reading screenshots: {str(e)}")
            minimap_data = ""
            game_data = ""
        
        # Load latest reflection
        reflection = self._load_latest_reflection()
        reflection_str = json.dumps(reflection, indent=2) if reflection else "{}"

        # Create the base message content with system instructions
        base_content = f"""You are a game decision maker that generates specific actions based on:
             1. Current game state
             2. Plan from plan.json
             3. Relevant game knowledge
             4. Previous decisions
             4b. Latest reflection on plan step completion
             5. Visual context from screenshots:
                - The game screenshot shows immediate surroundings
                - The minimap shows the entire map with:
                  * Black dot: player
                  * Brown circles: walls
                  * Red circles: hearts (health items)
                  * Gold circle: treasure
             
            Available Objects:
            The following object types are available in the game: bomb, heart, wall, fire, treasure, and watergun. Only use these object types when generating actions. Do not create or reference any other object types that are not in this list.
             
             Your task is to generate an action space (list of actions) based on the plan.
             Return a JSON array of actions in the following format:
             [
                 {{
                     "action_type": "one of: move, delete, generate, unity",
                     "object_id": "the object ID to interact with (for move/delete), or null for generate/unity",
                     "position": [x,y] (destination for move, null for delete/generate/unity),
                    "object_type": "type of object to generate (for generate): bomb, heart, wall, fire, treasure, watergun",
                     "positions": [[x1,y1], ...] (for generate, one position per object),
                     "width": int (for wall generation only),
                     "height": int (for wall generation only),
                     "angle": int (for wall generation only),
                     
                     // For Unity actions (action_type="unity"):
                     "unity_action": "Unity action name: PlaceObject, PlaceHuman, MoveObject, DeleteObject, SetTimeOfDay, SetFogEnabled, SetFogDensity, SetLightIntensity, SetLightColor, SetAmbientColor, SetGravity, SetTimeScale, SetHumanAnimationController, PlayHumanAnimationFromResources",
                     "objectName": "prefab name in Resources/Prefabs/ (for PlaceObject/PlaceHuman/MoveObject/DeleteObject)",
                     "humanName": "prefab name in Resources/Prefabs/ (for PlaceHuman)",
                     "x": float (Unity world X coordinate),
                     "y": float (Unity world Y coordinate),
                     "z": float (Unity world Z coordinate),
                     "rotationY": float (rotation in degrees, default 0),
                     "hour": float (0-24, for SetTimeOfDay),
                     "enabled": bool (for SetFogEnabled),
                     "density": float (for SetFogDensity),
                     "r": float (0-1, color red component),
                     "g": float (0-1, color green component),
                     "b": float (0-1, color blue component),
                     "gravityY": float (for SetGravity),
                     "scale": float (for SetTimeScale, SetLightIntensity),
                     "controllerPathInResources": "path in Resources/ (for animation)",
                     "stateName": "animation state name",
                     "crossFadeTime": float (animation blend duration, default 0.25)
                 }},
                 ...
             ]
             
             Rules:
             1. Generate actions based on the plan sequence steps
             2. For GENERATE actions: 
                - Do NOT include object_id (it will be auto-generated)
                - For each object to generate, create a separate action
                - For walls, include width, height, and angle
                - positions should be a list with one position per object
             3. For MOVE actions:
                - object_id must be a valid game object ID (e.g., "bomb_0", "heart_1", "wall_2", "player")
                - position is the destination [x, y]
             4. For DELETE actions:
                - object_id must be a valid game object ID
                - position should be null
             5. For UNITY actions (action_type="unity"):
                - Use unity_action to specify the Unity action name
                - For PlaceObject: require objectName, x, y, z (rotationY optional, default 0)
                - For PlaceHuman: require humanName, x, y, z (rotationY optional, default 0)
                - For SetTimeOfDay: require hour (0-24)
                - For SetFogEnabled: require enabled (bool)
                - For SetFogDensity: require density (float)
                - For SetLightIntensity: require scale (float, 0-2)
                - For SetLightColor/SetAmbientColor: require r, g, b (0-1)
                - For SetGravity: require gravityY (float)
                - For SetTimeScale: require scale (float, 0-2)
                - Unity coordinates are in 3D world space (x, y, z), not 2D game coordinates
             6. IMPORTANT: All game positions (for move/generate) must be within the CURRENT VIEWPORT bounds:
                - X range: {viewport_min_x:.0f} to {viewport_max_x:.0f}
                - Y range: {viewport_min_y:.0f} to {viewport_max_y:.0f}
                - Player is at [{player_x:.0f}, {player_y:.0f}]
                - Screen size: {screen_width}x{screen_height}
                - Only generate objects visible in the current viewport
             7. Generate actions for the current step(s) in the plan
             8. If the planning step conditions are met, no further actions are needed.
             9. Consider the game state, especially player health and position
             10. Use visual context from both screenshots to make informed decisions
             11. Return only the JSON array, no explanation
             12. CRITICAL — DO NOT DUPLICATE OBJECTS: Check the "Already Placed Objects" list below. If an object with the same objectName already exists, do NOT use PlaceObject again. Instead:
                 - To reposition it: use MoveObject with the same objectName, new x/y/z, and rotationY
                 - To remove it: use DeleteObject with the objectName
                 - Only use PlaceObject for objects NOT already in the placed list
             13. For MoveObject: require objectName, x, y, z (rotationY optional, default 0)
             14. For DeleteObject: require objectName only
             15. When the reflection says to move or rotate an object, use MoveObject — NEVER place a duplicate

        Current Game State:
        {game_state_str}

        Current Step:
        {plan_step_str}
        {loop_instructions}

        Latest Reflection:
        {reflection_str}

        Already Placed Objects (DO NOT re-place these — use MoveObject to reposition or DeleteObject to remove):
        {json.dumps(self._load_placed_objects(), indent=2)}

        Generate actions for the current plan step(s). Remember: never PlaceObject for an objectName that is already placed — use MoveObject instead:"""
        
        # Prepare messages for Groq API
        messages = [
            {
                "role": "user",
                "content": base_content
            }
        ]
        
        # Add images if available
        if game_data and minimap_data:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyze these game screenshots to make your decision:"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{game_data}"
                        }
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{minimap_data}"
                        }
                    }
                ]
            })
        
        # Retry logic for invalid JSON responses
        max_retries = 3
        retry_delay = 2.0  # seconds
        actions_list = None
        
        for attempt in range(max_retries):
            try:
                # Call Groq API with streaming
                completion = self.groq_client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=messages,
                    temperature=0,
                    max_completion_tokens=2048,
                    top_p=1,
                    stream=True,
                    stop=None
                )
                
                # Collect streaming response
                content = ""
                for chunk in completion:
                    if chunk.choices and len(chunk.choices) > 0:
                        delta = chunk.choices[0].delta
                        if hasattr(delta, 'content') and delta.content:
                            content += delta.content
                
                # Wait a bit to ensure streaming is complete
                time.sleep(0.1)
                
                content = content.strip()
                
                # Debug: log the raw content to see what Groq returned
                logger.debug(f"Groq API raw response: {content[:500]}...")
                
                # Check if content is empty
                if not content:
                    raise ValueError("Empty response from Groq API")
                
                # Try to extract JSON from the response (in case there's extra text)
                import re
                # First, try to find JSON array in code blocks (```json ... ``` or ``` ... ```)
                # Handle both complete and incomplete code blocks
                code_block_match = re.search(r'```(?:json)?\s*(\[.*?)(?:\s*```|$)', content, re.DOTALL)
                if code_block_match:
                    # Extract JSON from code block, may be incomplete
                    extracted = code_block_match.group(1).strip()
                    content = self._repair_incomplete_json(extracted)
                else:
                    # Look for JSON array - find the first [ and try to match to the last ]
                    # Use a more robust approach: find the start of JSON array
                    start_idx = content.find('[')
                    if start_idx != -1:
                        # Try to find matching closing bracket
                        bracket_count = 0
                        end_idx = start_idx
                        for i in range(start_idx, len(content)):
                            if content[i] == '[':
                                bracket_count += 1
                            elif content[i] == ']':
                                bracket_count -= 1
                                if bracket_count == 0:
                                    end_idx = i + 1
                                    break
                        if bracket_count == 0:
                            content = content[start_idx:end_idx]
                        else:
                            # Incomplete JSON - try to repair it
                            content = self._repair_incomplete_json(content[start_idx:])
                    else:
                        # No JSON array found, try to extract any JSON structure
                        json_match = re.search(r'\[.*', content, re.DOTALL)
                        if json_match:
                            content = self._repair_incomplete_json(json_match.group(0))
                
                # Parse the response into a list of action dictionaries
                try:
                    actions_list = json.loads(content)
                except json.JSONDecodeError as e:
                    # Try to repair incomplete JSON
                    repaired_content = self._repair_incomplete_json(content)
                    try:
                        actions_list = json.loads(repaired_content)
                        logger.info("Successfully repaired incomplete JSON")
                    except json.JSONDecodeError as e2:
                        # If JSON parsing fails, log the content for debugging
                        logger.error(f"Failed to parse JSON even after repair. Original: {content[:200]}... Repaired: {repaired_content[:200]}...")
                        logger.error(f"JSON Error: {str(e2)}")
                        # Raise ValueError to trigger retry
                        raise ValueError(f"Invalid JSON response from Groq API: {str(e2)}")
                
                # Validate that we got a list
                if not isinstance(actions_list, list):
                    raise ValueError(f"Expected a list of actions, got {type(actions_list)}")
                
                # If we get here, JSON parsing and validation succeeded, break out of retry loop
                break
                
            except ValueError as e:
                # Check if this is the last attempt
                if attempt < max_retries - 1:
                    logger.warning(f"Invalid JSON response (attempt {attempt + 1}/{max_retries}). Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                else:
                    # Last attempt failed, re-raise the error
                    logger.error(f"Failed to get valid JSON after {max_retries} attempts")
                    raise
        
        # Convert each action dict to a GameDecision object
        decisions = []
        for action_dict in actions_list:
            action_type_str = action_dict.get("action_type", "").lower()
            
            if action_type_str == "move":
                pos = action_dict.get("position")
                # Clamp move position to viewport bounds
                if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                    x, y = pos[0], pos[1]
                    x = max(viewport_min_x, min(viewport_max_x, x))
                    y = max(viewport_min_y, min(viewport_max_y, y))
                    pos = [x, y]
                
                decision = GameDecision(
                    action_type=ActionType.MOVE,
                    object_id=action_dict.get("object_id"),
                    position=pos  # Clamped to viewport
                )
                decisions.append(decision)
            elif action_type_str == "delete":
                decision = GameDecision(
                    action_type=ActionType.DELETE,
                    object_id=action_dict.get("object_id")
                )
                decisions.append(decision)
            elif action_type_str == "generate":
                object_type = action_dict.get("object_type")
                positions = action_dict.get("positions", [])
                
                # Get width, height, angle - handle both single values and arrays
                width_raw = action_dict.get("width")
                height_raw = action_dict.get("height")
                angle_raw = action_dict.get("angle")
                
                # Generate object_id for each position if generating new objects
                # For generate actions, we'll create one decision per object
                for idx, pos in enumerate(positions):
                    # Clamp position to viewport bounds
                    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                        x, y = pos[0], pos[1]
                        # Clamp to viewport bounds
                        x = max(viewport_min_x, min(viewport_max_x, x))
                        y = max(viewport_min_y, min(viewport_max_y, y))
                        pos = [x, y]
                    else:
                        logger.warning(f"Invalid position format: {pos}, skipping")
                        continue
                    
                    object_id = self._get_next_object_id(object_type, game_state)
                    
                    # Extract single value from arrays if they are arrays
                    width = width_raw[idx] if isinstance(width_raw, list) else width_raw
                    height = height_raw[idx] if isinstance(height_raw, list) else height_raw
                    angle = angle_raw[idx] if isinstance(angle_raw, list) else angle_raw
                    
                    decision = GameDecision(
                        action_type=ActionType.GENERATE,
                        object_type=object_type,
                        object_id=object_id,  # Assign generated ID
                        positions=[pos],  # Single position per decision (clamped to viewport)
                        width=width,
                        height=height,
                        angle=angle
                    )
                    decisions.append(decision)
            elif action_type_str == "unity":
                # Unity action - extract unity_action (or use "action" field as fallback)
                unity_action = action_dict.get("unity_action") or action_dict.get("action")
                if not unity_action:
                    logger.warning("Unity action missing unity_action field, skipping")
                    continue
                
                decision = GameDecision(
                    action_type=ActionType.UNITY,
                    unity_action=unity_action,
                    object_name=action_dict.get("objectName"),
                    human_name=action_dict.get("humanName"),
                    x=action_dict.get("x"),
                    y=action_dict.get("y"),
                    z=action_dict.get("z"),
                    rotation_y=action_dict.get("rotationY"),
                    hour=action_dict.get("hour"),
                    enabled=action_dict.get("enabled"),
                    density=action_dict.get("density"),
                    r=action_dict.get("r"),
                    g=action_dict.get("g"),
                    b=action_dict.get("b"),
                    gravity_y=action_dict.get("gravityY"),
                    scale=action_dict.get("scale"),
                    controller_path=action_dict.get("controllerPathInResources"),
                    state_name=action_dict.get("stateName"),
                    cross_fade_time=action_dict.get("crossFadeTime")
                )
                decisions.append(decision)
            else:
                raise ValueError(f"Invalid action type: {action_type_str}")
        
        # Add to decision history
        for decision in decisions:
            self.decision_history.append(decision)
            if len(self.decision_history) > self.max_history:
                self.decision_history.pop(0)
        
        # Log all decisions
        decisions_dict = [d.to_dict() for d in decisions]
        self._log_decision(decisions_dict, executed=False, complete=False)

        return decisions



def main():
    """Generate actions based on plan from Parser's generate_plan and save to actions.json."""
    print("test...")
    

if __name__ == "__main__":
    main()
