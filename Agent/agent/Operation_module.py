import os
import json
import time
import pygame
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum
import numpy as np
from pathlib import Path
from openai import OpenAI

from .Decision_module import GameDecision, ActionType
from .Sensing_module import GameSensor, Position, ObjectType, GameObject
from game.game_state import GameState
from . import unity_bridge

class OperationResult:
    """Result of an operation execution."""
    def __init__(self, success: bool, message: str = "", data: Dict = None):
        self.success = success
        self.message = message
        self.data = data or {}
    
    def to_dict(self) -> Dict:
        """Convert result to dictionary for serialization."""
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data
        }

class GameOperator:
    def __init__(self):
        # Initialize game state for direct game control
        self.game_state = GameState(load_images=False)
        
        # Initialize game sensor with the same state file
        self.sensor = GameSensor(state_file=self.game_state.state_file)
        
        # Movement speed multiplier
        self.move_speed = 5
        
        # Last operation times
        self.last_operation_times = {action_type: 0 for action_type in ActionType}
        
        # Initialize pygame for keyboard simulation if needed
        pygame.init()
        
        # Path to actions.json
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.actions_file = os.path.join(project_root, "api_files", "actions.json")
    
    def is_cooldown_ready(self, action_type: ActionType) -> bool:
        """Check if an action type is ready (cooldown has passed)."""
        current_time = time.time()
        cooldown_duration = 0.1  # 100ms cooldown
        return (current_time - self.last_operation_times.get(action_type, 0)) >= cooldown_duration
    
    def update_cooldown(self, action_type: ActionType):
        """Update the cooldown time for an action type."""
        self.last_operation_times[action_type] = time.time()
    
    def execute_move(self, decision: GameDecision) -> OperationResult:
        """Execute a move action by sending a command to move a specific object to a target position."""
        try:
            # Extract target position
            if not decision.position:
                return OperationResult(False, "Missing position in decision")
            
            # Handle position as list or Position object
            if isinstance(decision.position, list):
                target_x = decision.position[0]
                target_y = decision.position[1]
            else:
                # Position object
                target_x = decision.position.x
                target_y = decision.position.y
            
            # Extract Object ID (default to "player")
            target_id = decision.object_id or "player"

            # Send Command to Game State
            self.game_state.add_command("move_object", {
                "id": target_id,
                "pos": [target_x, target_y]
            })

            # Update Cooldown
            self.update_cooldown(ActionType.MOVE)
            
            return OperationResult(True, f"Sent command to move '{target_id}' to ({target_x}, {target_y})")
            
        except Exception as e:
            return OperationResult(False, f"Error in execute_move: {str(e)}")
        
    def execute_generate(self, decision: GameDecision) -> OperationResult:
        """Execute a generate action (e.g., wall, fire) based on the decision."""
        try:
            # Extract object type and positions from the GameDecision object
            obj_type = decision.object_type
            positions = decision.positions
            if not obj_type or positions is None:
                return OperationResult(False, "Missing object_type or positions in generate parameters")

            # Handle positions - could be a list of lists or a single list
            if not positions:
                return OperationResult(False, "Empty positions list")
            
            # Normalize positions to list of [x, y] pairs
            if isinstance(positions[0], (list, tuple)):
                # Already a list of positions
                pos_list = positions
            else:
                # Single position, wrap it
                pos_list = [positions]

            generated = []
            for pos in pos_list:
                # Extract x, y from position
                if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                    x, y = pos[0], pos[1]
                else:
                    return OperationResult(False, f"Invalid position format: {pos}")
                
                if obj_type == "wall":
                    width = decision.width
                    height = decision.height
                    angle = decision.angle
                    if width is None or height is None or angle is None:
                        return OperationResult(False, f"Missing wall parameters: width={width}, height={height}, angle={angle}")
                    # Send command: [x, y, width, height, angle]
                    self.game_state.add_command("add_wall", [x, y, width, height, angle])
                elif obj_type == "fire":
                    self.game_state.add_command("add_fire", [x, y])
                elif obj_type == "heart":
                    self.game_state.add_command("add_heart", [x, y])
                elif obj_type == "bomb":
                    self.game_state.add_command("add_bomb", [x, y])
                elif obj_type == "watergun":
                    self.game_state.add_command("add_watergun", [x, y])
                elif obj_type == "treasure":
                    self.game_state.add_command("add_treasure", [x, y])
                else:
                    return OperationResult(False, f"Unknown object type for generate: {obj_type}")
                generated.append(f"({x}, {y})")

            return OperationResult(True, f"Generated {obj_type} at positions: {', '.join(generated)}")
        except Exception as e:
            return OperationResult(False, f"Error in execute_generate: {str(e)}")
    
    def execute_decision(self, decision: GameDecision) -> OperationResult:
        """Execute a decision."""
        # Execute based on action type
        if decision == "FINISH":
            return OperationResult(True, "Operation finish")
        elif decision.action_type == ActionType.MOVE:
            return self.execute_move(decision)
        elif decision.action_type == ActionType.GENERATE:
            return self.execute_generate(decision)
        elif decision.action_type == ActionType.DELETE:
            return self.execute_delete(decision)
        else:
            return OperationResult(False, f"Unknown action type: {decision.action_type}")
    
    def execute_delete(self, decision: GameDecision) -> OperationResult:
        """Execute a delete action."""
        try:
            object_id = decision.object_id
            if not object_id:
                return OperationResult(False, "Missing object_id in delete parameters")
            
            # Send delete command to game state
            self.game_state.add_command("delete_object", {"id": object_id})
            
            return OperationResult(True, f"Deleted object: {object_id}")
        except Exception as e:
            return OperationResult(False, f"Error in execute_delete: {str(e)}")

    def execute_unity_action(self, action_dict: Dict) -> OperationResult:
        """将一条 Unity 环境指令入队（POST 到 FastAPI /enqueue），由 Unity AgentNetworkBridge 轮询执行。"""
        try:
            # 协议与 main.py UnityCommand 一致：action 必填，其余按需
            action_name = action_dict.get("action") or action_dict.get("unity_action")
            if not action_name:
                return OperationResult(False, "Unity action missing 'action' or 'unity_action'")
            # 只传 UnityCommand 支持的键
            allowed = {
                "action", "hour", "enabled", "density", "r", "g", "b",
                "gravityY", "scale", "objectName", "humanName",
                "x", "y", "z", "rotationY",
                "controllerPathInResources", "stateName", "crossFadeTime",
            }
            cmd = {k: v for k, v in action_dict.items() if k in allowed and v is not None}
            cmd["action"] = action_name
            out = unity_bridge.enqueue_command(cmd)
            if out.get("error"):
                return OperationResult(False, f"Unity bridge: {out['error']}")
            return OperationResult(True, f"Queued Unity action '{action_name}' (queue_size={out.get('queue_size', 0)})")
        except Exception as e:
            return OperationResult(False, f"execute_unity_action: {str(e)}")
    
    def load_actions_from_file(self) -> List[Dict]:
        """Load actions from actions.json file."""
        if not os.path.exists(self.actions_file):
            return []
        
        try:
            with open(self.actions_file, 'r') as f:
                content = f.read()
                if not content:
                    return []
                data = json.loads(content)
                
                # Extract all actions from entries
                all_actions = []
                for entry in data:
                    if "actions" in entry:
                        # Multiple actions in one entry
                        for action in entry["actions"]:
                            action["_entry_timestamp"] = entry.get("timestamp")
                            action["_entry_executed"] = entry.get("executed", False)
                            all_actions.append(action)
                    elif "action" in entry:
                        # Single action
                        action = entry["action"]
                        action["_entry_timestamp"] = entry.get("timestamp")
                        action["_entry_executed"] = entry.get("executed", False)
                        all_actions.append(action)
                
                return all_actions
        except Exception as e:
            print(f"Error loading actions from file: {str(e)}")
            return []
    
    def execute_unexecuted_actions(self) -> List[OperationResult]:
        """Execute all unexecuted actions from actions.json."""
        actions = self.load_actions_from_file()
        results = []
        
        # Check individual action's "complete" field, not entry's "executed" field
        unexecuted_count = sum(1 for a in actions if not a.get("complete", False))
        print(f"Found {unexecuted_count} unexecuted actions out of {len(actions)} total actions")
        
        for i, action in enumerate(actions):
            # Skip if already executed (check individual action's complete field)
            if action.get("complete", False):
                continue
            
            try:
                # Unity 环境指令：直接入队，不转 GameDecision
                if action.get("action_type") == "unity":
                    print(f"Executing action {i+1}: unity -> {action.get('action') or action.get('unity_action')}")
                    result = self.execute_unity_action(action)
                else:
                    print(f"Executing action {i+1}: {action.get('action_type')} - {action.get('object_type') or action.get('object_id')}")
                    decision = self._action_dict_to_decision(action)
                    result = self.execute_decision(decision)
                
                results.append(result)
                if result.success:
                    print(f"  ✓ Success: {result.message}")
                else:
                    print(f"  ✗ Failed: {result.message}")
                
                # Mark as executed in actions.json
                self._mark_action_executed(action, result.success)
                
                # Small delay to allow game to process commands
                time.sleep(0.1)
            except Exception as e:
                error_msg = f"Error processing action: {str(e)}"
                print(f"  ✗ Exception: {error_msg}")
                results.append(OperationResult(False, error_msg))
                import traceback
                traceback.print_exc()
        
        return results
    
    def _action_dict_to_decision(self, action_dict: Dict) -> GameDecision:
        """Convert action dictionary to GameDecision object."""
        action_type_str = action_dict.get("action_type", "").lower()
        
        if action_type_str == "move":
            position = action_dict.get("position")
            # Handle position as list [x, y] or Position object
            if isinstance(position, list) and len(position) == 2:
                from .Sensing_module import Position
                position = Position(position[0], position[1])
            return GameDecision(
                action_type=ActionType.MOVE,
                object_id=action_dict.get("object_id"),
                position=position
            )
        elif action_type_str == "delete":
            return GameDecision(
                action_type=ActionType.DELETE,
                object_id=action_dict.get("object_id")
            )
        elif action_type_str == "generate":
            positions = action_dict.get("positions", [])
            # Ensure positions is a list of lists
            if positions and not isinstance(positions[0], (list, tuple)):
                positions = [positions]
            return GameDecision(
                action_type=ActionType.GENERATE,
                object_type=action_dict.get("object_type"),
                object_id=action_dict.get("object_id"),
                positions=positions,
                width=action_dict.get("width"),
                height=action_dict.get("height"),
                angle=action_dict.get("angle")
            )
        else:
            raise ValueError(f"Unknown action type: {action_type_str}")
    
    def _mark_action_executed(self, action: Dict, success: bool):
        """Mark an action as executed (complete) in actions.json."""
        if not os.path.exists(self.actions_file):
            return
        
        try:
            with open(self.actions_file, 'r') as f:
                content = f.read()
                if not content:
                    return
                data = json.loads(content)
            
            # Find and update the individual action's "complete" field
            for entry in data:
                if "actions" in entry:
                    # Multiple actions in one entry
                    for entry_action in entry["actions"]:
                        if self._actions_match(entry_action, action):
                            entry_action["complete"] = success
                            # Also mark entry as executed if all actions are complete
                            if all(a.get("complete", False) for a in entry["actions"]):
                                entry["executed"] = True
                                entry["complete"] = True
                            break
                elif "action" in entry:
                    # Single action entry
                    if self._actions_match(entry["action"], action):
                        entry["action"]["complete"] = success
                        entry["executed"] = success
                        entry["complete"] = success
                        break
            
            # Write back to file
            with open(self.actions_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error marking action as executed: {str(e)}")
    
    def _actions_match(self, action1: Dict, action2: Dict) -> bool:
        """Check if two actions match (same action_type, object_id, etc.)."""
        if action1.get("action_type") != action2.get("action_type"):
            return False

        # Unity 指令：按 action 名 + 关键参数匹配
        if action1.get("action_type") == "unity":
            a1 = action1.get("action") or action1.get("unity_action")
            a2 = action2.get("action") or action2.get("unity_action")
            if a1 != a2:
                return False
            # 同 action 且同 objectName/humanName 或同位置视为同一条
            if action1.get("objectName") or action1.get("humanName"):
                return action1.get("objectName") == action2.get("objectName") and action1.get("humanName") == action2.get("humanName")
            return True
        
        # For generate actions, match by object_id (most reliable) or object_type + first position
        if action1.get("action_type") == "generate":
            # First try to match by object_id (most reliable)
            obj_id1 = action1.get("object_id")
            obj_id2 = action2.get("object_id")
            if obj_id1 and obj_id2 and obj_id1 == obj_id2:
                return True
            # Fallback: match by object_type and first position
            if (action1.get("object_type") == action2.get("object_type")):
                pos1 = action1.get("positions", [])
                pos2 = action2.get("positions", [])
                if pos1 and pos2 and len(pos1) > 0 and len(pos2) > 0:
                    # Compare first position
                    if isinstance(pos1[0], list) and isinstance(pos2[0], list):
                        if len(pos1[0]) >= 2 and len(pos2[0]) >= 2:
                            return pos1[0][0] == pos2[0][0] and pos1[0][1] == pos2[0][1]
            return False
        
        # For move/delete, check object_id
        return action1.get("object_id") == action2.get("object_id")

def main():
    """Test the GameOperator with example decisions."""
    operator = GameOperator()
    
    # Test cases
    test_decisions = [
        GameDecision(ActionType.MOVE, target_position=Position(10, 10)),
        GameDecision(ActionType.WAIT, parameters={"duration": 0.5}),
        GameDecision(ActionType.GENERATE, parameters={
            "object_type": "wall",
            "positions": [100, 100],
            "width": 100,
            "height": 20,
            "angle": 0
        })
    ]
    
    print("Testing Game Operator:")
    print("-" * 50)
    
    for decision in test_decisions:
        print(f"\nDecision: {decision.action_type.value}")
        result = operator.execute_decision(decision)
        print(f"Result: {result.success}, {result.message}")
        print("-" * 50)

if __name__ == "__main__":
    main() 