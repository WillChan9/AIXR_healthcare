"""
Unity 命令文件写入模块
将 Unity 命令写入 JSON 文件，供 Unity AgentNetworkBridge 读取执行。

使用方式：
    from main import place_object, place_human, set_time_of_day
    
    # 放置物体
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=45.0)
    
    # 放置人形
    place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0)
    
    # 设置时间
    set_time_of_day(18.0)
"""

from __future__ import annotations

import os
import json
import time
from typing import Dict, List, Optional, Any
from pathlib import Path


# 获取项目根目录（main.py 在 Agent/ 目录下）
_AGENT_DIR = Path(__file__).parent
_PROJECT_ROOT = _AGENT_DIR.parent
_UNITY_PROJECT = _PROJECT_ROOT / "firefighter project"
_COMMANDS_FILE = _UNITY_PROJECT / "Assets" / "Scripts" / "unity_commands.json"


def _ensure_scripts_dir():
    """确保 Scripts 目录存在"""
    scripts_dir = _COMMANDS_FILE.parent
    scripts_dir.mkdir(exist_ok=True, parents=True)
    return scripts_dir


def _load_commands() -> List[Dict[str, Any]]:
    """加载现有命令列表"""
    if not _COMMANDS_FILE.exists():
        return []
    
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
    """保存命令列表到文件"""
    _ensure_scripts_dir()
    with open(_COMMANDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(commands, f, indent=2, ensure_ascii=False)


def _add_command(action: str, **kwargs) -> Dict[str, Any]:
    """添加一条 Unity 命令到文件"""
    commands = _load_commands()
    
    # 创建新命令
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


# ========== 便捷函数：物体生成 ==========

def place_object(
    object_name: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 2.0,
    rotation_y: float = 0.0
) -> Dict[str, Any]:
    """
    在 Unity 场景中放置一个物体（从预制体加载）。
    
    Args:
        object_name: 预制体名称（必须在 Resources/Prefabs/ 目录下）
        x, y, z: Unity 世界坐标
        rotation_y: 旋转角度（度）
    
    Returns:
        命令字典
    
    Example:
        >>> place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=45.0)
    """
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
    """
    通过名字创建物体。优先从预制体加载，如果没有则创建 Unity 基本几何体，最后创建空物体。
    
    支持的 Unity 基本几何体名称（不区分大小写）：
    - Cube / Box - 立方体
    - Sphere / Ball - 球体
    - Capsule - 胶囊体
    - Cylinder - 圆柱体
    - Plane - 平面
    - Quad - 四边形
    
    Args:
        object_name: 物体名称（可以是预制体名称或基本几何体名称）
        x, y, z: Unity 世界坐标
        rotation_y: 旋转角度（度）
        scale_x, scale_y, scale_z: 缩放比例（默认 1.0）
    
    Returns:
        命令字典
    
    Example:
        >>> create_object_by_name("Cube", x=0.0, y=0.0, z=2.0)
        >>> create_object_by_name("Sphere", x=1.0, y=0.0, z=2.0, scale_x=2.0)
        >>> create_object_by_name("MyCustomObject", x=2.0, y=0.0, z=2.0)
    """
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
    """
    在 Unity 场景中放置一个人形。
    
    Args:
        human_name: 人形预制体名称（必须在 Resources/Prefabs/ 目录下）
        x, y, z: Unity 世界坐标
        rotation_y: 旋转角度（度）
    
    Returns:
        命令字典
    
    Example:
        >>> place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0)
    """
    return _add_command(
        action="PlaceHuman",
        humanName=human_name,
        x=x,
        y=y,
        z=z,
        rotationY=rotation_y
    )


# ========== 便捷函数：环境控制 ==========

def set_time_of_day(hour: float) -> Dict[str, Any]:
    """
    设置场景时间（控制主光源角度）。
    
    Args:
        hour: 时间（0-24）
    
    Example:
        >>> set_time_of_day(18.0)  # 傍晚
    """
    return _add_command(action="SetTimeOfDay", hour=hour)


def set_fog_enabled(enabled: bool) -> Dict[str, Any]:
    """启用或禁用雾效"""
    return _add_command(action="SetFogEnabled", enabled=enabled)


def set_fog_density(density: float) -> Dict[str, Any]:
    """设置雾密度（推荐范围 0-0.1）"""
    return _add_command(action="SetFogDensity", density=density)


def set_light_intensity(intensity: float) -> Dict[str, Any]:
    """设置光照强度（推荐范围 0-2）"""
    return _add_command(action="SetLightIntensity", scale=intensity)


def set_light_color(r: float, g: float, b: float) -> Dict[str, Any]:
    """设置光照颜色（RGB 0-1）"""
    return _add_command(action="SetLightColor", r=r, g=g, b=b)


def set_ambient_color(r: float, g: float, b: float) -> Dict[str, Any]:
    """设置环境光颜色（RGB 0-1）"""
    return _add_command(action="SetAmbientColor", r=r, g=g, b=b)


def set_gravity(gravity_y: float) -> Dict[str, Any]:
    """设置重力（默认 -9.81）"""
    return _add_command(action="SetGravity", gravityY=gravity_y)


def set_time_scale(scale: float) -> Dict[str, Any]:
    """设置时间缩放（0=暂停，1=正常，最大2）"""
    return _add_command(action="SetTimeScale", scale=scale)


# ========== 便捷函数：动画控制 ==========

def set_human_animation_controller(controller_path: str) -> Dict[str, Any]:
    """
    设置人形动画控制器。
    
    Args:
        controller_path: 控制器路径（在 Resources/ 目录下）
    
    Example:
        >>> set_human_animation_controller("AnimatorControllers/HumanM@Walk01_Forward")
    """
    return _add_command(
        action="SetHumanAnimationController",
        controllerPathInResources=controller_path
    )


def play_human_animation(
    controller_path: str,
    state_name: str,
    cross_fade_time: float = 0.25
) -> Dict[str, Any]:
    """
    播放人形动画。
    
    Args:
        controller_path: 控制器路径（在 Resources/ 目录下）
        state_name: 动画状态名称
        cross_fade_time: 过渡时间（秒）
    
    Example:
        >>> play_human_animation(
        ...     "AnimatorControllers/HumanM@Walk01_Forward",
        ...     "HumanM@Walk01_Forward",
        ...     cross_fade_time=0.25
        ... )
    """
    return _add_command(
        action="PlayHumanAnimationFromResources",
        controllerPathInResources=controller_path,
        stateName=state_name,
        crossFadeTime=cross_fade_time
    )


# ========== 通用函数 ==========

def add_command(action: str, **kwargs) -> Dict[str, Any]:
    """
    添加一条自定义 Unity 命令。
    
    Args:
        action: Unity 动作名称
        **kwargs: 动作参数
    
    Example:
        >>> add_command("PlaceObject", objectName="Table1", x=1.0, y=0.0, z=2.0)
    """
    return _add_command(action=action, **kwargs)


def clear_commands():
    """清空所有命令"""
    _save_commands([])


def get_commands() -> List[Dict[str, Any]]:
    """获取所有命令列表"""
    return _load_commands()


def get_unexecuted_commands() -> List[Dict[str, Any]]:
    """获取所有未执行的命令"""
    commands = _load_commands()
    return [cmd for cmd in commands if not cmd.get("executed", False)]


# ========== 示例和测试 ==========

if __name__ == "__main__":
    # 设置输出编码为 UTF-8（Windows 兼容）
    import sys
    import io
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        except AttributeError:
            pass
    
    # 示例：生成多个物体
    print("=" * 60)
    print("Unity Object Generation Example")
    print("=" * 60)
    print("\n[IMPORTANT] Please:")
    print("   1. Open Unity and run the scene (click Play button)")
    print("   2. Make sure AgentNetworkBridge component is in the scene")
    print("   3. Then run this script")
    print("\n" + "-" * 60 + "\n")
    
    # 方式1：从预制体加载（需要预制体存在）
    print("Creating objects...")
    place_object("Chair1", x=0.0, y=0.0, z=2.0, rotation_y=0.0)
    
    # 方式2：通过名字创建（无需预制体，会自动创建 Unity 基本几何体）
    create_object_by_name("Cube", x=2.0, y=0.0, z=2.0, rotation_y=45.0)
    create_object_by_name("Sphere", x=4.0, y=0.0, z=2.0, scale_x=2.0, scale_y=2.0, scale_z=2.0)
    create_object_by_name("Capsule", x=6.0, y=0.0, z=2.0)
    create_object_by_name("Cylinder", x=8.0, y=0.0, z=2.0)
    
    # 放置一个人形
    place_human("HumanM_Idle01", x=1.0, y=0.0, z=3.0)
    
    # 设置环境
    set_time_of_day(18.0)  # 傍晚
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

