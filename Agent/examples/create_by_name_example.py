"""
Example: create Unity objects by name without prefabs.

This example shows how to use create_object_by_name, which can create Unity
primitives when no prefab exists.
"""

import sys
import os

# Add the project root directory to the import path.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from main import create_object_by_name, get_unexecuted_commands


def example_basic_primitives():
    """Example: create Unity primitive geometry."""
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
    """Example: use different name variants."""
    print("\n" + "=" * 50)
    print("Example 2: Use different name variants.")
    print("=" * 50)
    
    # These names are all recognized as cubes.
    create_object_by_name("box", x=0.0, y=0.0, z=4.0)
    create_object_by_name("CUBE", x=2.0, y=0.0, z=4.0)
    create_object_by_name("MyCube", x=4.0, y=0.0, z=4.0)
    
    # These names are all recognized as spheres.
    create_object_by_name("ball", x=6.0, y=0.0, z=4.0)
    create_object_by_name("SPHERE", x=8.0, y=0.0, z=4.0)
    create_object_by_name("MySphere", x=10.0, y=0.0, z=4.0)
    
    print("Created primitives using various name variants.")


def example_custom_objects():
    """Example: create custom-named objects (creates empty GameObject)."""
    print("\n" + "=" * 50)
    print("Example 3: Create custom-named objects.")
    print("=" * 50)
    
    # If name matches no prefab or primitive, an empty GameObject is created.
    create_object_by_name("MyCustomObject", x=0.0, y=0.0, z=6.0)
    create_object_by_name("FireExtinguisher", x=2.0, y=0.0, z=6.0)
    create_object_by_name("EmergencyExit", x=4.0, y=0.0, z=6.0)
    
    print("Created empty GameObjects with custom names.")
    print("Tip: you can manually add components or child objects in Unity.")


def example_scaled_objects():
    """Example: create objects with different scales."""
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
    """Example: create objects with different rotations."""
    print("\n" + "=" * 50)
    print("Example 5: Create objects with different rotations.")
    print("=" * 50)
    
    # Cubes at different angles.
    for i, angle in enumerate([0, 45, 90, 135, 180]):
        create_object_by_name("Cube", x=i * 2.0, y=0.0, z=10.0, rotation_y=angle)
    
    print("Created cubes with different rotation angles.")


def example_prefab_priority():
    """Example: prefab-first behavior (use prefab if present, otherwise primitive)."""
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
    print("Supported Unity primitive names (case-insensitive):")
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
