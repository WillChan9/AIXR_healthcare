"""
示例：通过名字创建 Unity 物体（无需预制体）

这个示例展示了如何使用 create_object_by_name 函数，
它可以在没有预制体的情况下创建 Unity 基本几何体。
"""

import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from main import create_object_by_name, get_unexecuted_commands


def example_basic_primitives():
    """示例：创建 Unity 基本几何体"""
    print("=" * 50)
    print("示例 1: 创建 Unity 基本几何体")
    print("=" * 50)
    
    # 创建立方体
    create_object_by_name("Cube", x=0.0, y=0.0, z=2.0)
    print("✓ 创建了 Cube")
    
    # 创建球体（带缩放）
    create_object_by_name("Sphere", x=2.0, y=0.0, z=2.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    print("✓ 创建了 Sphere (2x scale)")
    
    # 创建胶囊体
    create_object_by_name("Capsule", x=4.0, y=0.0, z=2.0)
    print("✓ 创建了 Capsule")
    
    # 创建圆柱体
    create_object_by_name("Cylinder", x=6.0, y=0.0, z=2.0)
    print("✓ 创建了 Cylinder")
    
    # 创建平面
    create_object_by_name("Plane", x=8.0, y=0.0, z=2.0, scale_x=2.0, scale_z=2.0)
    print("✓ 创建了 Plane (2x scale)")
    
    # 创建四边形
    create_object_by_name("Quad", x=10.0, y=0.0, z=2.0)
    print("✓ 创建了 Quad")


def example_variations():
    """示例：使用不同的名字变体"""
    print("\n" + "=" * 50)
    print("示例 2: 使用不同的名字变体")
    print("=" * 50)
    
    # 这些名字都会被识别为立方体
    create_object_by_name("box", x=0.0, y=0.0, z=4.0)
    create_object_by_name("CUBE", x=2.0, y=0.0, z=4.0)
    create_object_by_name("MyCube", x=4.0, y=0.0, z=4.0)
    
    # 这些名字都会被识别为球体
    create_object_by_name("ball", x=6.0, y=0.0, z=4.0)
    create_object_by_name("SPHERE", x=8.0, y=0.0, z=4.0)
    create_object_by_name("MySphere", x=10.0, y=0.0, z=4.0)
    
    print("✓ 创建了各种名字变体的几何体")


def example_custom_objects():
    """示例：创建自定义名字的物体（会创建空 GameObject）"""
    print("\n" + "=" * 50)
    print("示例 3: 创建自定义名字的物体")
    print("=" * 50)
    
    # 如果名字不匹配任何预制体或基本几何体，会创建空 GameObject
    create_object_by_name("MyCustomObject", x=0.0, y=0.0, z=6.0)
    create_object_by_name("FireExtinguisher", x=2.0, y=0.0, z=6.0)
    create_object_by_name("EmergencyExit", x=4.0, y=0.0, z=6.0)
    
    print("✓ 创建了自定义名字的空 GameObject")
    print("  提示：你可以在 Unity 中手动添加组件或子对象")


def example_scaled_objects():
    """示例：创建不同缩放的物体"""
    print("\n" + "=" * 50)
    print("示例 4: 创建不同缩放的物体")
    print("=" * 50)
    
    # 小立方体
    create_object_by_name("Cube", x=0.0, y=0.0, z=8.0, scale_x=0.5, scale_y=0.5, scale_z=0.5)
    
    # 大立方体
    create_object_by_name("Cube", x=2.0, y=0.0, z=8.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    
    # 扁平立方体
    create_object_by_name("Cube", x=4.0, y=0.0, z=8.0, scale_x=2.0, scale_y=0.5, scale_z=2.0)
    
    # 高立方体
    create_object_by_name("Cube", x=6.0, y=0.0, z=8.0, scale_x=1.0, scale_y=3.0, scale_z=1.0)
    
    print("✓ 创建了不同缩放的立方体")


def example_rotated_objects():
    """示例：创建不同旋转的物体"""
    print("\n" + "=" * 50)
    print("示例 5: 创建不同旋转的物体")
    print("=" * 50)
    
    # 不同角度的立方体
    for i, angle in enumerate([0, 45, 90, 135, 180]):
        create_object_by_name("Cube", x=i * 2.0, y=0.0, z=10.0, rotation_y=angle)
    
    print("✓ 创建了不同旋转角度的立方体")


def example_prefab_priority():
    """示例：预制体优先（如果预制体存在，会使用预制体而不是基本几何体）"""
    print("\n" + "=" * 50)
    print("示例 6: 预制体优先机制")
    print("=" * 50)
    
    # 如果 Resources/Prefabs/Cube.prefab 存在，会使用预制体
    # 如果不存在，会创建 Unity 基本几何体 Cube
    create_object_by_name("Cube", x=0.0, y=0.0, z=12.0)
    
    print("✓ CreateObjectByName 会优先使用预制体，如果没有则创建基本几何体")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("通过名字创建 Unity 物体示例")
    print("=" * 50)
    print("\n支持的 Unity 基本几何体名称（不区分大小写）：")
    print("  - Cube / Box - 立方体")
    print("  - Sphere / Ball - 球体")
    print("  - Capsule - 胶囊体")
    print("  - Cylinder - 圆柱体")
    print("  - Plane - 平面")
    print("  - Quad - 四边形")
    print("\n如果名字不匹配，会创建空 GameObject")
    print("=" * 50 + "\n")
    
    try:
        example_basic_primitives()
        example_variations()
        example_custom_objects()
        example_scaled_objects()
        example_rotated_objects()
        example_prefab_priority()
        
        print("\n" + "=" * 50)
        print("所有示例执行完成！")
        print("=" * 50)
        print(f"\n未执行的命令数量: {len(get_unexecuted_commands())}")
        print("Unity 会自动读取并执行这些命令！")
        
    except Exception as e:
        print(f"\n错误：{str(e)}")
        import traceback
        traceback.print_exc()
