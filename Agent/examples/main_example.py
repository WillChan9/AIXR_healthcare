"""
使用 main.py 控制 Unity 物体生成的示例

运行此脚本后，命令会被写入 firefighter project/Assets/Scripts/unity_commands.json，
Unity 会自动读取并执行这些命令。
"""

import sys
import os

# 添加项目根目录到路径
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
    """示例：生成基本物体"""
    print("=" * 50)
    print("示例 1: 生成基本物体")
    print("=" * 50)
    
    # 放置几个椅子
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=0.0)
    place_object("Chair1", x=2.0, y=0.0, z=2.0, rotation_y=45.0)
    place_object("Chair1", x=4.0, y=0.0, z=2.0, rotation_y=90.0)
    
    # 放置一张桌子
    place_object("Table1", x=3.0, y=0.0, z=2.0, rotation_y=0.0)
    
    print("✓ 已生成 4 个物体命令")


def example_humans():
    """示例：生成人形"""
    print("\n" + "=" * 50)
    print("示例 2: 生成人形")
    print("=" * 50)
    
    # 放置几个人形
    place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0, rotation_y=0.0)
    place_human("HumanM_Idle01", x=5.0, y=0.0, z=3.0, rotation_y=180.0)
    
    print("✓ 已生成 2 个人形命令")


def example_environment():
    """示例：设置环境参数"""
    print("\n" + "=" * 50)
    print("示例 3: 设置环境参数")
    print("=" * 50)
    
    # 设置时间为傍晚
    set_time_of_day(18.0)
    
    # 启用雾效
    set_fog_enabled(True)
    
    # 设置光照强度
    set_light_intensity(1.5)
    
    print("✓ 已设置环境参数")


def example_animation():
    """示例：控制动画"""
    print("\n" + "=" * 50)
    print("示例 4: 控制动画")
    print("=" * 50)
    
    # 播放行走动画
    play_human_animation(
        controller_path="AnimatorControllers/HumanM@Walk01_Forward",
        state_name="HumanM@Walk01_Forward",
        cross_fade_time=0.25
    )
    
    print("✓ 已设置动画命令")


def example_batch_generation():
    """示例：批量生成物体"""
    print("\n" + "=" * 50)
    print("示例 5: 批量生成物体")
    print("=" * 50)
    
    # 生成一个圆形排列的椅子
    import math
    
    center_x, center_z = 5.0, 5.0
    radius = 3.0
    num_chairs = 8
    
    for i in range(num_chairs):
        angle = (2 * math.pi * i) / num_chairs
        x = center_x + radius * math.cos(angle)
        z = center_z + radius * math.sin(angle)
        rotation_y = math.degrees(angle) + 90.0  # 面向中心
        
        place_object("Chair1", x=x, y=0.0, z=z, rotation_y=rotation_y)
    
    # 在中心放置一张桌子
    place_object("Table1", x=center_x, y=0.0, z=center_z, rotation_y=0.0)
    
    print(f"✓ 已生成 {num_chairs + 1} 个物体（圆形排列）")


def example_check_status():
    """示例：检查命令状态"""
    print("\n" + "=" * 50)
    print("示例 6: 检查命令状态")
    print("=" * 50)
    
    unexecuted = get_unexecuted_commands()
    print(f"未执行的命令数量: {len(unexecuted)}")
    
    if unexecuted:
        print("\n未执行的命令:")
        for i, cmd in enumerate(unexecuted[:5], 1):  # 只显示前5个
            print(f"  {i}. {cmd.get('action')} (ID: {cmd.get('id')})")
        if len(unexecuted) > 5:
            print(f"  ... 还有 {len(unexecuted) - 5} 个命令")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Unity 物体生成示例")
    print("=" * 50)
    print("\n注意：运行前请确保：")
    print("1. Unity 场景中已设置 AgentNetworkBridge 组件")
    print("2. scripts 文件夹已创建（会自动创建）")
    print("3. 预制体已准备好（在 Resources/Prefabs/ 目录下）")
    print("\n" + "=" * 50 + "\n")
    
    try:
        # 运行示例
        example_basic_objects()
        example_humans()
        example_environment()
        example_animation()
        example_batch_generation()
        example_check_status()
        
        print("\n" + "=" * 50)
        print("所有示例执行完成！")
        print("=" * 50)
        print(f"\n命令已写入文件，Unity 会自动读取并执行。")
        print(f"未执行的命令数量: {len(get_unexecuted_commands())}")
        
    except Exception as e:
        print(f"\n错误：{str(e)}")
        import traceback
        traceback.print_exc()
