import numpy as np
from typing import List, Dict, Tuple, Optional, Union
from dataclasses import dataclass
from enum import Enum
import pygame
import json
import os
from pathlib import Path
import time

try:
    from .logger import get_logger
except ImportError:
    from agent.logger import get_logger

logger = get_logger("SensingModule")

@dataclass
class Position:
    x: float
    y: float

    def distance_to(self, other: 'Position') -> float:
        """Calculate Euclidean distance to another position."""
        return np.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    @staticmethod
    def from_pygame_pos(pos: Tuple[int, int]) -> 'Position':
        """Convert pygame position to Position object."""
        return Position(float(pos[0]), float(pos[1]))

class ObjectType(Enum):
    USER = "user"
    HEART = "heart"
    BOMB = "bomb"
    FIRE = "fire"
    TREASURE = "treasure"
    WALL = "wall"
    
    @staticmethod
    def from_sprite_type(sprite_type: str) -> 'ObjectType':
        """Convert sprite type string to ObjectType."""
        type_map = {
            'player': ObjectType.USER,
            'heart': ObjectType.HEART,
            'bomb': ObjectType.BOMB,
            'fire': ObjectType.FIRE,
            'treasure': ObjectType.TREASURE,
            'wall': ObjectType.WALL
        }
        return type_map.get(sprite_type.lower(), None)

@dataclass
class GameObject:
    obj_type: ObjectType
    position: Position
    active: bool = True
    # Additional properties specific to object types
    properties: Dict = None

    def __post_init__(self):
        if self.properties is None:
            self.properties = {}

class UserState:
    def __init__(self):
        # Load game configuration
        config_path = Path("game/game_config.json")
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            self.health: int = config.get('initial_health', 5)
            initial_pos = config.get('initial_player_pos', [0, 0])
            self.position: Position = Position(initial_pos[0], initial_pos[1])
            self.game_over: bool = False
            self.game_won: bool = False
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error loading game config: {str(e)}. Using default values.")
            self.health: int = 5
            self.position: Position = Position(0, 0)
            self.game_over: bool = False
            self.game_won: bool = False

    def update(self, health: Optional[int] = None, position: Optional[Position] = None,
               game_over: Optional[bool] = None, game_won: Optional[bool] = None):
        """Update user state with new values."""
        if health is not None:
            self.health = health
        if position is not None:
            self.position = position
        if game_over is not None:
            self.game_over = game_over
        if game_won is not None:
            self.game_won = game_won

    def to_dict(self) -> Dict:
        """Convert user state to dictionary for easy serialization."""
        return {
            "health": self.health,
            "position": {"x": self.position.x, "y": self.position.y},
            "game_over": self.game_over,
            "game_won": self.game_won
        }

class GameSensor:
    def __init__(self, state_file: Union[str, Path] = None):
        self.user_state = UserState()
        self.game_objects: List[GameObject] = []
        self.map_size: Tuple[float, float] = (100.0, 100.0)  # Default map size
        self.last_update_time: float = 0
        
        # State file for communication with game
        if state_file is None:
            self.state_file = Path("game/game_state.json")
        else:
            self.state_file = Path(state_file) if isinstance(state_file, str) else state_file
        self.state_update_interval = 0.1  # Update every 100ms

    def update_from_game_state(self) -> bool:
        """Update sensor state from game state file."""
        try:
            # Check if file exists, if not return warning
            if not self.state_file.exists():
                logger.warning(f"Game state file not found at {self.state_file}")
                return False
            
            # Read game state from file
            with open(self.state_file, 'r') as f:
                content = f.read().strip()
                
            if not content:  # File is empty
                logger.warning(f"Game state file is empty at {self.state_file}")
                return False
                
            game_data = json.loads(content)
            
            # Update user state
            player_data = game_data.get('player', {})
            self.user_state.update(
                health=player_data.get('health'),
                position=Position(
                    player_data.get('position', [0, 0])[0],
                    player_data.get('position', [0, 0])[1]
                ),
                game_over=player_data.get('game_over'),
                game_won=player_data.get('game_won')
            )
            
            # Clear existing game objects
            self.game_objects.clear()
            
            # Get objects data
            objects_data = game_data.get('objects', {})
            
            # Process each object type
            object_types = {
                'walls': ObjectType.WALL,
                'fires': ObjectType.FIRE,
                'bombs': ObjectType.BOMB,
                'hearts': ObjectType.HEART,
                'treasure': ObjectType.TREASURE
            }
            
            for obj_key, obj_type in object_types.items():
                for obj_data in objects_data.get(obj_key, []):
                    position = Position(
                        obj_data.get('position', [0, 0])[0],
                        obj_data.get('position', [0, 0])[1]
                    )
                    
                    # Create properties dictionary based on object type
                    properties = {}
                    if obj_type == ObjectType.WALL:
                        properties = {
                            'id': obj_data.get('id'),
                            'width': obj_data.get('width'),
                            'height': obj_data.get('height'),
                            'angle': obj_data.get('angle')
                        }
                    elif obj_type == ObjectType.FIRE:
                        properties = {
                            'spawn_time': obj_data.get('spawn_time')
                        }
                    else:  # For bombs, hearts, and treasure
                        properties = {
                            'id': obj_data.get('id'),
                            'collected': obj_data.get('collected'),
                            'status': obj_data.get('status')
                        }
                    
                    self.add_game_object(obj_type, position, properties)
            
            # Update map size from config
            config_data = game_data.get('config', {})
            self.map_size = (
                config_data.get('map_width', 100),
                config_data.get('map_height', 100)
            )
            
            self.last_update_time = time.time()
            return True
            
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing game state file: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error updating game state: {str(e)}")
            return False

    def update_user_state(self, **kwargs):
        """Update user state with new values."""
        self.user_state.update(**kwargs)

    def add_game_object(self, obj_type: ObjectType, position: Position, properties: Dict = None) -> GameObject:
        """Add a new game object to the world."""
        game_object = GameObject(obj_type, position, True, properties)
        self.game_objects.append(game_object)
        return game_object

    def remove_game_object(self, game_object: GameObject):
        """Remove a game object from the world."""
        if game_object in self.game_objects:
            self.game_objects.remove(game_object)

    def get_objects_by_type(self, obj_type: ObjectType) -> List[GameObject]:
        """Get all active objects of a specific type."""
        # Update state before returning objects
        self.update_from_game_state()
        return [obj for obj in self.game_objects if obj.obj_type == obj_type and obj.active]

    def get_nearest_object(self, position: Position, obj_type: Optional[ObjectType] = None) -> Optional[GameObject]:
        """Find the nearest object to a position, optionally filtered by type."""
        # Update state before finding nearest object
        self.update_from_game_state()
        valid_objects = self.game_objects if obj_type is None else self.get_objects_by_type(obj_type)
        if not valid_objects:
            return None

        return min(valid_objects, key=lambda obj: obj.position.distance_to(position))

    def get_objects_in_range(self, position: Position, range_distance: float, 
                           obj_type: Optional[ObjectType] = None) -> List[GameObject]:
        """Get all objects within a certain range of a position."""
        # Update state before checking range
        self.update_from_game_state()
        valid_objects = self.game_objects if obj_type is None else self.get_objects_by_type(obj_type)
        return [obj for obj in valid_objects 
                if obj.active and obj.position.distance_to(position) <= range_distance]

    def get_game_state(self) -> Dict:
        """Get the current game state including user state and all game objects."""
        # Update state before returning
        self.update_from_game_state()
        return {
            "user_state": self.user_state.to_dict(),
            "objects": [
                {
                    "type": obj.obj_type.value,
                    "position": {"x": obj.position.x, "y": obj.position.y},
                    "active": obj.active,
                    "properties": obj.properties
                }
                for obj in self.game_objects
            ],
            "map_size": self.map_size,
            "last_update_time": self.last_update_time
        }

    def check_collision(self, position: Position, radius: float = 1.0) -> List[GameObject]:
        """Check for collisions at a position within a radius."""
        # Update state before checking collisions
        self.update_from_game_state()
        return self.get_objects_in_range(position, radius)

    def is_position_valid(self, position: Position) -> bool:
        """Check if a position is valid (within map bounds and not colliding with walls)."""
        # Update state before checking position
        self.update_from_game_state()
        # Check map bounds
        if not (0 <= position.x <= self.map_size[0] and 0 <= position.y <= self.map_size[1]):
            return False
        
        # Check wall collisions
        walls = self.get_objects_in_range(position, 1.0, ObjectType.WALL)
        return len(walls) == 0

def main():
    """Test the GameSensor functionality with real-time game state updates."""
    sensor = GameSensor()
    
    print("\nWaiting for game state updates...")
    print("-" * 50)
    
    try:
        while True:
            if sensor.update_from_game_state():
                game_state = sensor.get_game_state()
                os.system('clear' if os.name == 'posix' else 'cls')  # Clear console
                print("\nGame State:")
                print("-" * 50)
                print(f"User Position: ({game_state['user_state']['position']['x']:.1f}, {game_state['user_state']['position']['y']:.1f})")
                print(f"User Health: {game_state['user_state']['health']}")
                print(f"Game Over: {game_state['user_state']['game_over']}")
                print(f"Game Won: {game_state['user_state']['game_won']}")
                print("\nGame Objects:")
                for obj in game_state['objects']:
                    print(f"Type: {obj['type']}, Position: ({obj['position']['x']:.1f}, {obj['position']['y']:.1f})")
            
            pygame.time.wait(int(sensor.state_update_interval * 1000))  # Wait before next update
            
    except KeyboardInterrupt:
        print("\nStopped monitoring game state.")

if __name__ == "__main__":
    main() 