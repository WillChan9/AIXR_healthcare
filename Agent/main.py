"""
Unity command-file writer.

Despite its name, this module is mainly a small library (plus a demo when run
directly). It is the single place where the Python pipeline talks to Unity:
every helper appends one JSON command to

    firefighter project/Assets/Scripts/unity_commands.json

Unity's ``AgentNetworkBridge`` component polls that file, executes each command
whose ``"executed"`` flag is false, and marks it as executed. Each command is a
dict of the form::

    {"id": "cmd_<ms>_<n>", "timestamp": <epoch>, "executed": false,
     "action": "<UnityAction>", ...action-specific fields...}

Keyword arguments whose value is ``None`` are omitted from the written command.

Available helpers
-----------------
Object creation
    place_object(object_name, x, y, z, rotation_y)
    create_object_by_name(object_name, x, y, z, rotation_y, scale_x, scale_y, scale_z)
    place_human(human_name, x, y, z, rotation_y)

Environment control
    set_time_of_day(hour)
    set_fog_enabled(enabled), set_fog_density(density)
    set_light_intensity(intensity), set_light_color(r, g, b), set_ambient_color(r, g, b)
    set_gravity(gravity_y), set_time_scale(scale)

Animation and character control
    set_human_animation_controller(controller_path)
    play_human_animation(controller_path, state_name, cross_fade_time, target_model)
    move_character(character_name, x, y, z, rotation_y)
    walk_character_to(character_name, x, y, z, rotation_y, duration, walk_controller,
                      idle_controller)
    animate_character(character_name, controller_path, state_name, cross_fade_time)

Generic
    add_command(action, **kwargs)   -- any action understood by AgentNetworkBridge
    clear_commands()
    get_commands()
    get_unexecuted_commands()

Usage::

    from main import place_object, place_human, animate_character, move_character
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=45.0)
    place_human("Doctor_01", x=1.0, y=0.0, z=3.0)
    animate_character("Doctor_01", "AnimatorControllers/HumanM@Talking", "HumanM@Talking")
    move_character("Doctor_01", x=2.0, y=0.0, z=4.0)

Running ``python Agent/main.py`` writes a few demo commands into the real
command file, so only do that with a Unity scene you are happy to modify.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths and file I/O
# ---------------------------------------------------------------------------

# Serialises file access from every thread that writes commands
# (the main pipeline thread and the AnimationPlanner background thread).
_file_lock = threading.Lock()

_AGENT_DIR = Path(__file__).parent            # Agent/
_PROJECT_ROOT = _AGENT_DIR.parent             # repo root
_UNITY_PROJECT = _PROJECT_ROOT / "firefighter project"
_COMMANDS_FILE = _UNITY_PROJECT / "Assets" / "Scripts" / "unity_commands.json"


def _ensure_scripts_dir():
    """Create the directory that holds the command file, if missing."""
    scripts_dir = _COMMANDS_FILE.parent
    scripts_dir.mkdir(exist_ok=True, parents=True)
    return scripts_dir


def _load_commands() -> List[Dict[str, Any]]:
    """Return the current command list (empty if the file is missing or invalid)."""
    if not _COMMANDS_FILE.exists():
        return []
    with _file_lock:
        try:
            with open(_COMMANDS_FILE, "r", encoding="utf-8") as f:
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
    """Write the command list, retrying briefly if Unity has the file locked."""
    _ensure_scripts_dir()
    with _file_lock:
        for attempt in range(20):
            try:
                with open(_COMMANDS_FILE, "w", encoding="utf-8") as f:
                    json.dump(commands, f, indent=2, ensure_ascii=False)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)


def _add_command(action: str, **kwargs) -> Dict[str, Any]:
    """Append one command (dropping ``None``-valued fields) and return it."""
    commands = _load_commands()

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


# ---------------------------------------------------------------------------
# Object creation
# ---------------------------------------------------------------------------

def place_object(
    object_name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 2.0,
    rotation_y: float = 0.0
) -> Dict[str, Any]:
    """Instantiate a prefab from ``Resources/Prefabs/`` at the given position."""
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
    """Create an object by name, preferring prefab > Unity primitive > empty GameObject."""
    # Unity treats a scale of 0 (or less) as "keep the default of 1", so an
    # unchanged scale is sent as 0.0.
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
    """Place a human character prefab; ``human_name`` later identifies it in other commands."""
    return _add_command(
        action="PlaceHuman",
        humanName=human_name,
        x=x,
        y=y,
        z=z,
        rotationY=rotation_y
    )


# ---------------------------------------------------------------------------
# Environment control
# ---------------------------------------------------------------------------

def set_time_of_day(hour: float) -> Dict[str, Any]:
    """Set the scene time of day (0-24)."""
    return _add_command(action="SetTimeOfDay", hour=hour)


def set_fog_enabled(enabled: bool) -> Dict[str, Any]:
    """Enable or disable fog."""
    return _add_command(action="SetFogEnabled", enabled=enabled)


def set_fog_density(density: float) -> Dict[str, Any]:
    """Set fog density (recommended range 0-0.1)."""
    return _add_command(action="SetFogDensity", density=density)


def set_light_intensity(intensity: float) -> Dict[str, Any]:
    """Set the main light intensity (recommended range 0-2)."""
    return _add_command(action="SetLightIntensity", scale=intensity)


def set_light_color(r: float, g: float, b: float) -> Dict[str, Any]:
    """Set the main light color (RGB, each 0-1)."""
    return _add_command(action="SetLightColor", r=r, g=g, b=b)


def set_ambient_color(r: float, g: float, b: float) -> Dict[str, Any]:
    """Set the ambient light color (RGB, each 0-1)."""
    return _add_command(action="SetAmbientColor", r=r, g=g, b=b)


def set_gravity(gravity_y: float) -> Dict[str, Any]:
    """Set vertical gravity (Unity default is -9.81)."""
    return _add_command(action="SetGravity", gravityY=gravity_y)


def set_time_scale(scale: float) -> Dict[str, Any]:
    """Set the simulation time scale (0 = pause, 1 = normal, max 2)."""
    return _add_command(action="SetTimeScale", scale=scale)


# ---------------------------------------------------------------------------
# Animation and character control
# ---------------------------------------------------------------------------

def set_human_animation_controller(controller_path: str) -> Dict[str, Any]:
    """Assign an Animator controller (path relative to ``Resources/``) to the default human."""
    return _add_command(
        action="SetHumanAnimationController",
        controllerPathInResources=controller_path
    )


def play_human_animation(
    controller_path: str,
    state_name: str,
    cross_fade_time: float = 0.25,
    target_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Cross-fade a human into an Animator state, loading the controller from ``Resources/``.

    ``target_model`` selects who is animated: ``"player"`` for the player
    avatar, a character name (e.g. ``"Nurse_01"``), or ``None`` for the default
    NPC (``defaultHumanModel`` in Unity).
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
    """Teleport a character placed with ``place_human`` to a new pose.

    For a smooth, animated walk use ``walk_character_to`` instead.
    """
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
    """Walk a named character to a target pose over ``duration`` seconds.

    Unity plays the walk animation, interpolates position and rotation, then
    cross-fades back to the idle controller on arrival. For female characters
    (``Nurse_*``) pass the ``HumanF@Walk01_Forward`` / ``HumanF@Idles``
    controllers instead.
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
    """Play an animation on a specific character placed with ``place_human``.

    Args:
        character_name: The ``human_name`` used when placing it (e.g. ``"Doctor_01"``).
        controller_path: Path under ``Resources/``, e.g.
            ``"AnimatorControllers/HumanM@Walk01_Forward"``.
        state_name: Animator state to cross-fade into.
        cross_fade_time: Cross-fade duration in seconds.
    """
    return _add_command(
        action="PlayHumanAnimationFromResources",
        controllerPathInResources=controller_path,
        stateName=state_name,
        crossFadeTime=cross_fade_time,
        targetModel=character_name,
    )


# ---------------------------------------------------------------------------
# Generic command access
# ---------------------------------------------------------------------------

def add_command(action: str, **kwargs) -> Dict[str, Any]:
    """Append an arbitrary command; ``kwargs`` become JSON fields as given."""
    return _add_command(action=action, **kwargs)


def clear_commands():
    """Remove all commands from the command file."""
    _save_commands([])


def get_commands() -> List[Dict[str, Any]]:
    """Return every command in the file, executed or not."""
    return _load_commands()


def get_unexecuted_commands() -> List[Dict[str, Any]]:
    """Return the commands Unity has not yet executed."""
    commands = _load_commands()
    return [cmd for cmd in commands if not cmd.get("executed", False)]


# ---------------------------------------------------------------------------
# Demo (writes into the real command file)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import io
    import sys

    # Force UTF-8 console output on Windows.
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except AttributeError:
            pass

    print("=" * 60)
    print("Unity Object Generation Example")
    print("=" * 60)
    print("\n[IMPORTANT] Please:")
    print("   1. Open Unity and run the scene (click Play button)")
    print("   2. Make sure AgentNetworkBridge component is in the scene")
    print("   3. Then run this script")
    print("\n" + "-" * 60 + "\n")

    print("Creating objects...")
    # Method 1: load a prefab (the prefab must exist in Resources/Prefabs/).
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=0.0)

    # Method 2: create by name (no prefab needed; falls back to Unity primitives).
    create_object_by_name("Cube", x=2.0, y=0.0, z=2.0, rotation_y=45.0)
    create_object_by_name("Sphere", x=4.0, y=0.0, z=2.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    create_object_by_name("Capsule", x=6.0, y=0.0, z=2.0)
    create_object_by_name("Cylinder", x=8.0, y=0.0, z=2.0)

    place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0)

    set_time_of_day(18.0)  # Evening.
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
