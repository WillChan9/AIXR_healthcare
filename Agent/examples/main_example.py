"""
Example using main.py to control Unity object generation.

After running this script, commands are written to
firefighter project/Assets/Scripts/unity_commands.json, and Unity automatically
reads and executes these commands.
"""

import sys
import os

# Add the project root directory to the import path.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from main import (
    place_object,
    place_human,
    set_time_of_day,
    set_fog_enabled,
    set_light_intensity,
    play_human_animation,
    get_unexecuted_commands,
    clear_commands
)


def example_basic_objects():
    """Example: generate basic objects."""
    print("=" * 50)
    print("Example 1: Generate basic objects.")
    print("=" * 50)
    
    # Place several chairs.
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=0.0)
    place_object("Chair1", x=2.0, y=0.0, z=2.0, rotation_y=45.0)
    place_object("Chair1", x=4.0, y=0.0, z=2.0, rotation_y=90.0)
    
    # Place one table.
    place_object("Table1", x=3.0, y=0.0, z=2.0, rotation_y=0.0)
    
    print("Generated 4 object commands.")


def example_humans():
    """Example: generate human models."""
    print("\n" + "=" * 50)
    print("Example 2: Generate human models.")
    print("=" * 50)
    
    # Place several human models.
    place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0, rotation_y=0.0)
    place_human("HumanM_Idle01", x=5.0, y=0.0, z=3.0, rotation_y=180.0)
    
    print("Generated 2 human commands.")


def example_environment():
    """Example: set environment parameters."""
    print("\n" + "=" * 50)
    print("Example 3: Set environment parameters.")
    print("=" * 50)
    
    # Set time to evening.
    set_time_of_day(18.0)
    
    # Enable fog.
    set_fog_enabled(True)
    
    # Set light intensity.
    set_light_intensity(1.5)
    
    print("Environment parameters set.")


def example_animation():
    """Example: control animation."""
    print("\n" + "=" * 50)
    print("Example 4: Control animation.")
    print("=" * 50)
    
    # Play walking animation.
    play_human_animation(
        controller_path="AnimatorControllers/HumanM@Walk01_Forward",
        state_name="HumanM@Walk01_Forward",
        cross_fade_time=0.25
    )
    
    print("Animation command set.")


def example_batch_generation():
    """Example: batch-generate objects."""
    print("\n" + "=" * 50)
    print("Example 5: Batch-generate objects.")
    print("=" * 50)
    
    # Generate chairs in a circular layout.
    import math
    
    center_x, center_z = 5.0, 5.0
    radius = 3.0
    num_chairs = 8
    
    for i in range(num_chairs):
        angle = (2 * math.pi * i) / num_chairs
        x = center_x + radius * math.cos(angle)
        z = center_z + radius * math.sin(angle)
        rotation_y = math.degrees(angle) + 90.0  # Face toward the center.
        
        place_object("Chair1", x=x, y=0.0, z=z, rotation_y=rotation_y)
    
    # Place a table at the center.
    place_object("Table1", x=center_x, y=0.0, z=center_z, rotation_y=0.0)
    
    print(f"✓  {num_chairs + 1} （）")  # Generated {num_chairs + 1} objects (circular layout).


def example_check_status():
    """Example: check command status."""
    print("\n" + "=" * 50)
    print("Example 6: Check command status.")
    print("=" * 50)
    
    unexecuted = get_unexecuted_commands()
    print(f": {len(unexecuted)}")  # Number of unexecuted commands: {len(unexecuted)}.
    
    if unexecuted:
        print("Unexecuted commands:")
        for i, cmd in enumerate(unexecuted[:5], 1):  # Show only the first 5.
            print(f"  {i}. {cmd.get('action')} (ID: {cmd.get('id')})")
        if len(unexecuted) > 5:
            print(f"  ...  {len(unexecuted) - 5} ")  # ... and {len(unexecuted) - 5} more commands.


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Unity object generation example.")
    print("=" * 50)
    print("Note: Before running, ensure:")
    print("1. AgentNetworkBridge is set in the Unity scene.")
    print("2. The scripts folder exists (or will be auto-created).")
    print("3. Prefabs are ready (under Resources/Prefabs/).")
    print("\n" + "=" * 50 + "\n")
    
    try:
        # Run examples.
        example_basic_objects()
        example_humans()
        example_environment()
        example_animation()
        example_batch_generation()
        example_check_status()
        
        print("\n" + "=" * 50)
        print("All examples completed.")
        print("=" * 50)
        print(f"\n，Unity 。")  # Commands were written to file; Unity will read and execute them automatically.
        print(f": {len(get_unexecuted_commands())}")  # Number of unexecuted commands: {len(get_unexecuted_commands())}.
        
    except Exception as e:
        print(f"\n：{str(e)}")  # Error: {str(e)}.
        import traceback
        traceback.print_exc()
