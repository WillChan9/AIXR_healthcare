"""
Unity command file writer.

Writes Unity commands to JSON so Unity AgentNetworkBridge can read and execute them.

Usage:
    from main import place_object, place_human, set_time_of_day, move_character, animate_character
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=45.0)
    place_human("Doctor_01", x=1.0, y=0.0, z=3.0)
    animate_character("Doctor_01", "AnimatorControllers/HumanM@Talking", "HumanM@Talking")
    move_character("Doctor_01", x=2.0, y=0.0, z=4.0)
    set_time_of_day(18.0)
"""

from __future__ import annotations

import os
import json
import threading
import time
from typing import Dict, List, Optional, Any
from pathlib import Path

# Serialise all file writes from any thread (main + AnimationPlannerThread)
_file_lock = threading.Lock()


# Get the project root directory (main.py is under Agent/).
_AGENT_DIR = Path(__file__).parent
_PROJECT_ROOT = _AGENT_DIR.parent
_UNITY_PROJECT = _PROJECT_ROOT / "firefighter project"
_COMMANDS_FILE = _UNITY_PROJECT / "Assets" / "Scripts" / "unity_commands.json"


def _ensure_scripts_dir():
    """Ensure the Scripts directory exists."""
    scripts_dir = _COMMANDS_FILE.parent
    scripts_dir.mkdir(exist_ok=True, parents=True)
    return scripts_dir


def _load_commands() -> List[Dict[str, Any]]:
    """Load existing command list."""
    if not _COMMANDS_FILE.exists():
        return []
    with _file_lock:
        try:
            with open(_COMMANDS_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return []
                commands = json.loads(content)
                if not isinstance(commands, list):
                    return []
                return commands
        except (json.JSONDecodeError, IOError):
            return []


def _save_commands(commands: List[Dict[str, Any]]):
    """Save command list to file, serialised across threads and retrying if Unity has it locked."""
    _ensure_scripts_dir()
    with _file_lock:
        for attempt in range(20):
            try:
                with open(_COMMANDS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(commands, f, indent=2, ensure_ascii=False)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)


def _add_command(action: str, **kwargs) -> Dict[str, Any]:
    """Add one Unity command to file."""
    commands = _load_commands()
    
    # Create a new command.
    new_command = {
        "id": f"cmd_{int(time.time() * 1000)}_{len(commands)}",
        "timestamp": time.time(),
        "executed": False,
        "action": action,
        **{k: v for k, v in kwargs.items() if v is not None}
    }
    
    commands.append(new_command)
    _save_commands(commands)
    
    return new_command


# Convenience functions: object creation.

def place_object(
    object_name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 2.0,
    rotation_y: float = 0.0
) -> Dict[str, Any]:
    """Place a prefab object in the Unity scene."""
    return _add_command(
        action="PlaceObject",
        objectName=object_name,
        x=x,
        y=y,
        z=z,
        rotationY=rotation_y
    )


def create_object_by_name(
    object_name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 2.0,
    rotation_y: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    scale_z: float = 1.0
) -> Dict[str, Any]:
    """Create an object by name (prefab > primitive > empty GameObject)."""
    return _add_command(
        action="CreateObjectByName",
        objectName=object_name,
        x=x,
        y=y,
        z=z,
        rotationY=rotation_y,
        scaleX=scale_x if scale_x != 1.0 else 0.0,
        scaleY=scale_y if scale_y != 1.0 else 0.0,
        scaleZ=scale_z if scale_z != 1.0 else 0.0
    )


def place_human(
    human_name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 3.0,
    rotation_y: float = 0.0
) -> Dict[str, Any]:
    """Place a human prefab in the Unity scene."""
    return _add_command(
        action="PlaceHuman",
        humanName=human_name,
        x=x,
        y=y,
        z=z,
        rotationY=rotation_y
    )


# Convenience functions: environment control.

def set_time_of_day(hour: float) -> Dict[str, Any]:
    """Set scene time (0-24)."""
    return _add_command(action="SetTimeOfDay", hour=hour)


def set_fog_enabled(enabled: bool) -> Dict[str, Any]:
    """Enable or disable fog effect."""
    return _add_command(action="SetFogEnabled", enabled=enabled)


def set_fog_density(density: float) -> Dict[str, Any]:
    """Set fog density (recommended range 0-0.1)."""
    return _add_command(action="SetFogDensity", density=density)


def set_light_intensity(intensity: float) -> Dict[str, Any]:
    """Set light intensity (recommended range 0-2)."""
    return _add_command(action="SetLightIntensity", scale=intensity)


def set_light_color(r: float, g: float, b: float) -> Dict[str, Any]:
    """Set light color (RGB 0-1)."""
    return _add_command(action="SetLightColor", r=r, g=g, b=b)


def set_ambient_color(r: float, g: float, b: float) -> Dict[str, Any]:
    """Set ambient light color (RGB 0-1)."""
    return _add_command(action="SetAmbientColor", r=r, g=g, b=b)


def set_gravity(gravity_y: float) -> Dict[str, Any]:
    """Set gravity (default -9.81)."""
    return _add_command(action="SetGravity", gravityY=gravity_y)


def set_time_scale(scale: float) -> Dict[str, Any]:
    """Set time scale (0=pause, 1=normal, max 2)."""
    return _add_command(action="SetTimeScale", scale=scale)


# Convenience functions: animation control.

def set_human_animation_controller(controller_path: str) -> Dict[str, Any]:
    """Set the human animation controller (Resources/ path)."""
    return _add_command(
        action="SetHumanAnimationController",
        controllerPathInResources=controller_path
    )


def play_human_animation(
    controller_path: str,
    state_name: str,
    cross_fade_time: float = 0.25,
    target_model: str = None,
) -> Dict[str, Any]:
    """Play a human animation state (controller ensured from Resources/).

    target_model: "player" to animate the player avatar; omit/None to animate
                  the default NPC character (defaultHumanModel).
    """
    kwargs: Dict[str, Any] = dict(
        action="PlayHumanAnimationFromResources",
        controllerPathInResources=controller_path,
        stateName=state_name,
        crossFadeTime=cross_fade_time,
    )
    if target_model:
        kwargs["targetModel"] = target_model
    return _add_command(**kwargs)


def move_character(
    character_name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    rotation_y: float = 0.0,
) -> Dict[str, Any]:
    """Move a named character (previously placed with place_human) to a new position.
    Teleports instantly. For smooth walking with animation, use walk_character_to()."""
    return _add_command(
        action="MoveCharacter",
        objectName=character_name,
        x=x,
        y=y,
        z=z,
        rotationY=rotation_y,
    )


def walk_character_to(
    character_name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    rotation_y: float = 0.0,
    duration: float = 2.0,
    walk_controller: str = "AnimatorControllers/HumanM@Walk01_Forward",
    idle_controller: str = "AnimatorControllers/HumanM@Idles",
) -> Dict[str, Any]:
    """Make a named character walk to a target: plays walk animation, smoothly
    interpolates position + rotation over `duration` seconds, then crossfades
    back to the idle animation when arrived.

    For female characters (Nurse_*), pass walk_controller='AnimatorControllers/HumanF@Walk01_Forward'
    and idle_controller='AnimatorControllers/HumanF@Idles'.
    """
    return _add_command(
        action="WalkCharacterTo",
        objectName=character_name,
        x=x,
        y=y,
        z=z,
        rotationY=rotation_y,
        duration=duration,
        controllerPathInResources=walk_controller,
        idleControllerPath=idle_controller,
    )


def animate_character(
    character_name: str,
    controller_path: str,
    state_name: str,
    cross_fade_time: float = 0.25,
) -> Dict[str, Any]:
    """Play an animation on a specific named character placed with place_human.

    character_name: the humanName used when the character was placed (e.g. "Doctor_01")
    controller_path: Resources path, e.g. "AnimatorControllers/HumanM@Walk01_Forward"
    state_name: Animator state to cross-fade into
    """
    return _add_command(
        action="PlayHumanAnimationFromResources",
        controllerPathInResources=controller_path,
        stateName=state_name,
        crossFadeTime=cross_fade_time,
        targetModel=character_name,
    )


# Generic functions.

def add_command(action: str, **kwargs) -> Dict[str, Any]:
    """Add a custom Unity command."""
    return _add_command(action=action, **kwargs)


def clear_commands():
    """Clear all commands."""
    _save_commands([])


def get_commands() -> List[Dict[str, Any]]:
    """Get all commands."""
    return _load_commands()


def get_unexecuted_commands() -> List[Dict[str, Any]]:
    """Get all unexecuted commands."""
    commands = _load_commands()
    return [cmd for cmd in commands if not cmd.get("executed", False)]


# Examples and tests.

if __name__ == "__main__":
    # Set output encoding to UTF-8 (Windows compatible).
    import sys
    import io
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        except AttributeError:
            pass
    
    # Example: generate multiple objects.
    print("=" * 60)
    print("Unity Object Generation Example")
    print("=" * 60)
    print("\n[IMPORTANT] Please:")
    print("   1. Open Unity and run the scene (click Play button)")
    print("   2. Make sure AgentNetworkBridge component is in the scene")
    print("   3. Then run this script")
    print("\n" + "-" * 60 + "\n")
    
    # Method 1: load from prefab (prefab required).
    print("Creating objects...")
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=0.0)
    
    # Method 2: create by name (no prefab needed; auto-creates Unity primitives).
    create_object_by_name("Cube", x=2.0, y=0.0, z=2.0, rotation_y=45.0)
    create_object_by_name("Sphere", x=4.0, y=0.0, z=2.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    create_object_by_name("Capsule", x=6.0, y=0.0, z=2.0)
    create_object_by_name("Cylinder", x=8.0, y=0.0, z=2.0)
    
    # Place one human model.
    place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0)
    
    # Configure environment.
    set_time_of_day(18.0)  # evening.
    set_fog_enabled(True)
    set_light_intensity(1.5)
    
    print(f"\n[OK] Generated {len(get_unexecuted_commands())} unexecuted commands")
    print(f"[OK] Command file: {_COMMANDS_FILE}")
    print("\n" + "=" * 60)
    print("Unity will automatically read and execute these commands (max 1 second wait)")
    print("=" * 60)
    print("\nTips:")
    print("  - PlaceObject: Load prefab from Resources/Prefabs/")
    print("  - CreateObjectByName: Auto-create (prefab > primitive > empty GameObject)")
    print("\nIf Unity doesn't execute commands, check:")
    print("  1. Is Unity scene running?")
    print("  2. Is AgentNetworkBridge component added?")
    print("  3. Check Unity Console for errors")

