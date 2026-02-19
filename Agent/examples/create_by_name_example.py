"""
示例：通过名字创建 Unity 物体（无需预制体） (EN: Example: create Unity objects by name without prefabs.)
# EN: Example: create Unity objects by name (no prefab required).

这个示例展示了如何使用 create_object_by_name 函数， (EN: This example shows how to use create_object_by_name,)
# EN: This example shows how to use the create_object_by_name function,
它可以在没有预制体的情况下创建 Unity 基本几何体。 (EN: which can create Unity primitives when no prefab exists.)
# EN: which can create Unity primitive geometry without prefabs.
"""

import sys
import os

# 添加项目根目录到路径  # EN: Add the project root directory to the import path.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from main import create_object_by_name, get_unexecuted_commands


def example_basic_primitives():
    """示例：创建 Unity 基本几何体"""  # EN: Example: create Unity primitive geometry.
    print("=" * 50)
    print("示例 1: 创建 Unity 基本几何体")  # EN: Example 1: Create Unity primitive geometry.
    print("=" * 50)
    
    # 创建立方体  # EN: Create a cube.
    create_object_by_name("Cube", x=0.0, y=0.0, z=2.0)
    print("✓ 创建了 Cube")  # EN: Created Cube.
    
    # 创建球体（带缩放）  # EN: Create a sphere (with scaling).
    create_object_by_name("Sphere", x=2.0, y=0.0, z=2.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    print("✓ 创建了 Sphere (2x scale)")  # EN: Created Sphere (2x scale).
    
    # 创建胶囊体  # EN: Create a capsule.
    create_object_by_name("Capsule", x=4.0, y=0.0, z=2.0)
    print("✓ 创建了 Capsule")  # EN: Created Capsule.
    
    # 创建圆柱体  # EN: Create a cylinder.
    create_object_by_name("Cylinder", x=6.0, y=0.0, z=2.0)
    print("✓ 创建了 Cylinder")  # EN: Created Cylinder.
    
    # 创建平面  # EN: Create a plane.
    create_object_by_name("Plane", x=8.0, y=0.0, z=2.0, scale_x=2.0, scale_z=2.0)
    print("✓ 创建了 Plane (2x scale)")  # EN: Created Plane (2x scale).
    
    # 创建四边形  # EN: Create a quad.
    create_object_by_name("Quad", x=10.0, y=0.0, z=2.0)
    print("✓ 创建了 Quad")  # EN: Created Quad.


def example_variations():
    """示例：使用不同的名字变体"""  # EN: Example: use different name variants.
    print("\n" + "=" * 50)
    print("示例 2: 使用不同的名字变体")  # EN: Example 2: Use different name variants.
    print("=" * 50)
    
    # 这些名字都会被识别为立方体  # EN: These names are all recognized as cubes.
    create_object_by_name("box", x=0.0, y=0.0, z=4.0)
    create_object_by_name("CUBE", x=2.0, y=0.0, z=4.0)
    create_object_by_name("MyCube", x=4.0, y=0.0, z=4.0)
    
    # 这些名字都会被识别为球体  # EN: These names are all recognized as spheres.
    create_object_by_name("ball", x=6.0, y=0.0, z=4.0)
    create_object_by_name("SPHERE", x=8.0, y=0.0, z=4.0)
    create_object_by_name("MySphere", x=10.0, y=0.0, z=4.0)
    
    print("✓ 创建了各种名字变体的几何体")  # EN: Created primitives using various name variants.


def example_custom_objects():
    """示例：创建自定义名字的物体（会创建空 GameObject）"""  # EN: Example: create custom-named objects (creates empty GameObject).
    print("\n" + "=" * 50)
    print("示例 3: 创建自定义名字的物体")  # EN: Example 3: Create custom-named objects.
    print("=" * 50)
    
    # 如果名字不匹配任何预制体或基本几何体，会创建空 GameObject  # EN: If name matches no prefab or primitive, an empty GameObject is created.
    create_object_by_name("MyCustomObject", x=0.0, y=0.0, z=6.0)
    create_object_by_name("FireExtinguisher", x=2.0, y=0.0, z=6.0)
    create_object_by_name("EmergencyExit", x=4.0, y=0.0, z=6.0)
    
    print("✓ 创建了自定义名字的空 GameObject")  # EN: Created empty GameObjects with custom names.
    print("  提示：你可以在 Unity 中手动添加组件或子对象")  # EN: Tip: you can manually add components or child objects in Unity.


def example_scaled_objects():
    """示例：创建不同缩放的物体"""  # EN: Example: create objects with different scales.
    print("\n" + "=" * 50)
    print("示例 4: 创建不同缩放的物体")  # EN: Example 4: Create objects with different scales.
    print("=" * 50)
    
    # 小立方体  # EN: Small cube.
    create_object_by_name("Cube", x=0.0, y=0.0, z=8.0, scale_x=0.5, scale_y=0.5, scale_z=0.5)
    
    # 大立方体  # EN: Large cube.
    create_object_by_name("Cube", x=2.0, y=0.0, z=8.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    
    # 扁平立方体  # EN: Flat cube.
    create_object_by_name("Cube", x=4.0, y=0.0, z=8.0, scale_x=2.0, scale_y=0.5, scale_z=2.0)
    
    # 高立方体  # EN: Tall cube.
    create_object_by_name("Cube", x=6.0, y=0.0, z=8.0, scale_x=1.0, scale_y=3.0, scale_z=1.0)
    
    print("✓ 创建了不同缩放的立方体")  # EN: Created cubes with different scales.


def example_rotated_objects():
    """示例：创建不同旋转的物体"""  # EN: Example: create objects with different rotations.
    print("\n" + "=" * 50)
    print("示例 5: 创建不同旋转的物体")  # EN: Example 5: Create objects with different rotations.
    print("=" * 50)
    
    # 不同角度的立方体  # EN: Cubes at different angles.
    for i, angle in enumerate([0, 45, 90, 135, 180]):
        create_object_by_name("Cube", x=i * 2.0, y=0.0, z=10.0, rotation_y=angle)
    
    print("✓ 创建了不同旋转角度的立方体")  # EN: Created cubes with different rotation angles.


def example_prefab_priority():
    """示例：预制体优先（如果预制体存在，会使用预制体而不是基本几何体）"""  # EN: Example: prefab-first behavior (use prefab if present, otherwise primitive).
    print("\n" + "=" * 50)
    print("示例 6: 预制体优先机制")  # EN: Example 6: Prefab-first mechanism.
    print("=" * 50)
    
    # 如果 Resources/Prefabs/Cube.prefab 存在，会使用预制体  # EN: If Resources/Prefabs/Cube.prefab exists, the prefab is used.
    # 如果不存在，会创建 Unity 基本几何体 Cube  # EN: Otherwise, a Unity Cube primitive is created.
    create_object_by_name("Cube", x=0.0, y=0.0, z=12.0)
    
    print("✓ CreateObjectByName 会优先使用预制体，如果没有则创建基本几何体")  # EN: CreateObjectByName prefers prefabs; if unavailable, it creates primitives.


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("通过名字创建 Unity 物体示例")  # EN: Unity object-by-name creation example.
    print("=" * 50)
    print("\n支持的 Unity 基本几何体名称（不区分大小写）：")  # EN: Supported Unity primitive names (case-insensitive):
    print("  - Cube / Box - 立方体")  # EN: Cube / Box.
    print("  - Sphere / Ball - 球体")  # EN: Sphere / Ball.
    print("  - Capsule - 胶囊体")  # EN: Capsule.
    print("  - Cylinder - 圆柱体")  # EN: Cylinder.
    print("  - Plane - 平面")  # EN: Plane.
    print("  - Quad - 四边形")  # EN: Quad.
    print("\n如果名字不匹配，会创建空 GameObject")  # EN: If name does not match, an empty GameObject is created.
    print("=" * 50 + "\n")
    
    try:
        example_basic_primitives()
        example_variations()
        example_custom_objects()
        example_scaled_objects()
        example_rotated_objects()
        example_prefab_priority()
        
        print("\n" + "=" * 50)
        print("所有示例执行完成！")  # EN: All examples completed.
        print("=" * 50)
        print(f"\n未执行的命令数量: {len(get_unexecuted_commands())}")  # EN: Number of unexecuted commands: {len(get_unexecuted_commands())}.
        print("Unity 会自动读取并执行这些命令！")  # EN: Unity will automatically read and execute these commands.
        
    except Exception as e:
        print(f"\n错误：{str(e)}")  # EN: Error: {str(e)}.
        import traceback
        traceback.print_exc()
