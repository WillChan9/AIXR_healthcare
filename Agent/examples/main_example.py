"""
使用 main.py 控制 Unity 物体生成的示例 (EN: Example using main.py to control Unity object generation.)
# EN: Example of using main.py to control Unity object generation.

运行此脚本后，命令会被写入 firefighter project/Assets/Scripts/unity_commands.json， (EN: After running this script, commands are written to firefighter project/Assets/Scripts/unity_commands.json,)
# EN: After running this script, commands are written to firefighter project/Assets/Scripts/unity_commands.json,
Unity 会自动读取并执行这些命令。 (EN: Unity automatically reads and executes these commands.)
# EN: and Unity will automatically read and execute these commands.
"""

import sys
import os

# 添加项目根目录到路径  # EN: Add the project root directory to the import path.
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
    """示例：生成基本物体"""  # EN: Example: generate basic objects.
    print("=" * 50)
    print("示例 1: 生成基本物体")  # EN: Example 1: Generate basic objects.
    print("=" * 50)
    
    # 放置几个椅子  # EN: Place several chairs.
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=0.0)
    place_object("Chair1", x=2.0, y=0.0, z=2.0, rotation_y=45.0)
    place_object("Chair1", x=4.0, y=0.0, z=2.0, rotation_y=90.0)
    
    # 放置一张桌子  # EN: Place one table.
    place_object("Table1", x=3.0, y=0.0, z=2.0, rotation_y=0.0)
    
    print("✓ 已生成 4 个物体命令")  # EN: Generated 4 object commands.


def example_humans():
    """示例：生成人形"""  # EN: Example: generate human models.
    print("\n" + "=" * 50)
    print("示例 2: 生成人形")  # EN: Example 2: Generate human models.
    print("=" * 50)
    
    # 放置几个人形  # EN: Place several human models.
    place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0, rotation_y=0.0)
    place_human("HumanM_Idle01", x=5.0, y=0.0, z=3.0, rotation_y=180.0)
    
    print("✓ 已生成 2 个人形命令")  # EN: Generated 2 human commands.


def example_environment():
    """示例：设置环境参数"""  # EN: Example: set environment parameters.
    print("\n" + "=" * 50)
    print("示例 3: 设置环境参数")  # EN: Example 3: Set environment parameters.
    print("=" * 50)
    
    # 设置时间为傍晚  # EN: Set time to evening.
    set_time_of_day(18.0)
    
    # 启用雾效  # EN: Enable fog.
    set_fog_enabled(True)
    
    # 设置光照强度  # EN: Set light intensity.
    set_light_intensity(1.5)
    
    print("✓ 已设置环境参数")  # EN: Environment parameters set.


def example_animation():
    """示例：控制动画"""  # EN: Example: control animation.
    print("\n" + "=" * 50)
    print("示例 4: 控制动画")  # EN: Example 4: Control animation.
    print("=" * 50)
    
    # 播放行走动画  # EN: Play walking animation.
    play_human_animation(
        controller_path="AnimatorControllers/HumanM@Walk01_Forward",
        state_name="HumanM@Walk01_Forward",
        cross_fade_time=0.25
    )
    
    print("✓ 已设置动画命令")  # EN: Animation command set.


def example_batch_generation():
    """示例：批量生成物体"""  # EN: Example: batch-generate objects.
    print("\n" + "=" * 50)
    print("示例 5: 批量生成物体")  # EN: Example 5: Batch-generate objects.
    print("=" * 50)
    
    # 生成一个圆形排列的椅子  # EN: Generate chairs in a circular layout.
    import math
    
    center_x, center_z = 5.0, 5.0
    radius = 3.0
    num_chairs = 8
    
    for i in range(num_chairs):
        angle = (2 * math.pi * i) / num_chairs
        x = center_x + radius * math.cos(angle)
        z = center_z + radius * math.sin(angle)
        rotation_y = math.degrees(angle) + 90.0  # 面向中心  # EN: Face toward the center.
        
        place_object("Chair1", x=x, y=0.0, z=z, rotation_y=rotation_y)
    
    # 在中心放置一张桌子  # EN: Place a table at the center.
    place_object("Table1", x=center_x, y=0.0, z=center_z, rotation_y=0.0)
    
    print(f"✓ 已生成 {num_chairs + 1} 个物体（圆形排列）")  # EN: Generated {num_chairs + 1} objects (circular layout).


def example_check_status():
    """示例：检查命令状态"""  # EN: Example: check command status.
    print("\n" + "=" * 50)
    print("示例 6: 检查命令状态")  # EN: Example 6: Check command status.
    print("=" * 50)
    
    unexecuted = get_unexecuted_commands()
    print(f"未执行的命令数量: {len(unexecuted)}")  # EN: Number of unexecuted commands: {len(unexecuted)}.
    
    if unexecuted:
        print("\n未执行的命令:")  # EN: Unexecuted commands:
        for i, cmd in enumerate(unexecuted[:5], 1):  # 只显示前5个  # EN: Show only the first 5.
            print(f"  {i}. {cmd.get('action')} (ID: {cmd.get('id')})")
        if len(unexecuted) > 5:
            print(f"  ... 还有 {len(unexecuted) - 5} 个命令")  # EN: ... and {len(unexecuted) - 5} more commands.


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Unity 物体生成示例")  # EN: Unity object generation example.
    print("=" * 50)
    print("\n注意：运行前请确保：")  # EN: Note: Before running, ensure:
    print("1. Unity 场景中已设置 AgentNetworkBridge 组件")  # EN: 1. AgentNetworkBridge is set in the Unity scene.
    print("2. scripts 文件夹已创建（会自动创建）")  # EN: 2. The scripts folder exists (or will be auto-created).
    print("3. 预制体已准备好（在 Resources/Prefabs/ 目录下）")  # EN: 3. Prefabs are ready (under Resources/Prefabs/).
    print("\n" + "=" * 50 + "\n")
    
    try:
        # 运行示例  # EN: Run examples.
        example_basic_objects()
        example_humans()
        example_environment()
        example_animation()
        example_batch_generation()
        example_check_status()
        
        print("\n" + "=" * 50)
        print("所有示例执行完成！")  # EN: All examples completed.
        print("=" * 50)
        print(f"\n命令已写入文件，Unity 会自动读取并执行。")  # EN: Commands were written to file; Unity will read and execute them automatically.
        print(f"未执行的命令数量: {len(get_unexecuted_commands())}")  # EN: Number of unexecuted commands: {len(get_unexecuted_commands())}.
        
    except Exception as e:
        print(f"\n错误：{str(e)}")  # EN: Error: {str(e)}.
        import traceback
        traceback.print_exc()
