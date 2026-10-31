"""
Example: create Unity objects by name, with or without prefabs.

``create_object_by_name`` (Agent/main.py) resolves a name in this order:
a prefab under ``Resources/Prefabs/`` with that name, then a matching Unity
primitive (cube/box, sphere/ball, capsule, cylinder, plane, quad), and finally
an empty GameObject with that name. Each call appends a ``CreateObjectByName``
command to ``firefighter project/Assets/Scripts/unity_commands.json``, which
Unity's AgentNetworkBridge executes while the scene is in Play mode.

Note: running this modifies the real command file (and therefore the scene).

Usage:
    python Agent/examples/create_by_name_example.py
"""

import os
import sys

# Make Agent/ (the parent of this examples/ folder) importable.
agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, agent_dir)

from main import create_object_by_name, get_unexecuted_commands  # noqa: E402


def example_basic_primitives():
    """Example 1: create each supported Unity primitive."""
    print("=" * 50)
    print("Example 1: Create Unity primitive geometry.")
    print("=" * 50)

    # Create a cube.
    create_object_by_name("Cube", x=0.0, y=0.0, z=2.0)
    print("Created Cube.")

    # Create a sphere (with scaling).
    create_object_by_name("Sphere", x=2.0, y=0.0, z=2.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    print("Created Sphere (2x scale).")

    # Create a capsule.
    create_object_by_name("Capsule", x=4.0, y=0.0, z=2.0)
    print("Created Capsule.")

    # Create a cylinder.
    create_object_by_name("Cylinder", x=6.0, y=0.0, z=2.0)
    print("Created Cylinder.")

    # Create a plane.
    create_object_by_name("Plane", x=8.0, y=0.0, z=2.0, scale_x=2.0, scale_z=2.0)
    print("Created Plane (2x scale).")

    # Create a quad.
    create_object_by_name("Quad", x=10.0, y=0.0, z=2.0)
    print("Created Quad.")


def example_variations():
    """Example 2: primitive names are matched exactly, ignoring case."""
    print("\n" + "=" * 50)
    print("Example 2: Use different name variants.")
    print("=" * 50)

    # "box" and "CUBE" both create a cube. "MyCube" is not an exact match, so
    # (unless a prefab with that name exists) it becomes an empty GameObject.
    create_object_by_name("box", x=0.0, y=0.0, z=4.0)
    create_object_by_name("CUBE", x=2.0, y=0.0, z=4.0)
    create_object_by_name("MyCube", x=4.0, y=0.0, z=4.0)

    # Likewise "ball" and "SPHERE" create spheres; "MySphere" does not.
    create_object_by_name("ball", x=6.0, y=0.0, z=4.0)
    create_object_by_name("SPHERE", x=8.0, y=0.0, z=4.0)
    create_object_by_name("MySphere", x=10.0, y=0.0, z=4.0)

    print("Created objects using various name variants.")


def example_custom_objects():
    """Example 3: unknown names produce empty GameObjects."""
    print("\n" + "=" * 50)
    print("Example 3: Create custom-named objects.")
    print("=" * 50)

    # A name that matches no prefab or primitive creates an empty GameObject.
    create_object_by_name("MyCustomObject", x=0.0, y=0.0, z=6.0)
    create_object_by_name("FireExtinguisher", x=2.0, y=0.0, z=6.0)
    create_object_by_name("EmergencyExit", x=4.0, y=0.0, z=6.0)

    print("Created empty GameObjects with custom names.")
    print("Tip: you can manually add components or child objects in Unity.")


def example_scaled_objects():
    """Example 4: non-uniform and uniform scaling."""
    print("\n" + "=" * 50)
    print("Example 4: Create objects with different scales.")
    print("=" * 50)

    # Small cube.
    create_object_by_name("Cube", x=0.0, y=0.0, z=8.0, scale_x=0.5, scale_y=0.5, scale_z=0.5)

    # Large cube.
    create_object_by_name("Cube", x=2.0, y=0.0, z=8.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)

    # Flat cube.
    create_object_by_name("Cube", x=4.0, y=0.0, z=8.0, scale_x=2.0, scale_y=0.5, scale_z=2.0)

    # Tall cube.
    create_object_by_name("Cube", x=6.0, y=0.0, z=8.0, scale_x=1.0, scale_y=3.0, scale_z=1.0)

    print("Created cubes with different scales.")


def example_rotated_objects():
    """Example 5: rotation around the Y axis."""
    print("\n" + "=" * 50)
    print("Example 5: Create objects with different rotations.")
    print("=" * 50)

    # Cubes at different angles.
    for i, angle in enumerate([0, 45, 90, 135, 180]):
        create_object_by_name("Cube", x=i * 2.0, y=0.0, z=10.0, rotation_y=angle)

    print("Created cubes with different rotation angles.")


def example_prefab_priority():
    """Example 6: a prefab with the same name takes priority over the primitive."""
    print("\n" + "=" * 50)
    print("Example 6: Prefab-first mechanism.")
    print("=" * 50)

    # If Resources/Prefabs/Cube.prefab exists, the prefab is used.
    # Otherwise, a Unity Cube primitive is created.
    create_object_by_name("Cube", x=0.0, y=0.0, z=12.0)

    print("CreateObjectByName prefers prefabs; if unavailable, it creates primitives.")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Unity object-by-name creation example.")
    print("=" * 50)
    print("Supported Unity primitive names (exact match, case-insensitive):")
    print("  - Cube / Box")
    print("  - Sphere / Ball")
    print("  - Capsule")
    print("  - Cylinder")
    print("  - Plane")
    print("  - Quad")
    print("If a name does not match, an empty GameObject is created.")
    print("=" * 50 + "\n")

    try:
        example_basic_primitives()
        example_variations()
        example_custom_objects()
        example_scaled_objects()
        example_rotated_objects()
        example_prefab_priority()

        print("\n" + "=" * 50)
        print("All examples completed.")
        print("=" * 50)
        print(f"\nUnexecuted commands: {len(get_unexecuted_commands())}")
        print("Unity will automatically read and execute these commands.")

    except Exception as e:
        print(f"\nError: {str(e)}")
        import traceback
        traceback.print_exc()
