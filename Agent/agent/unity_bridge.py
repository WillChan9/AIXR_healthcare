"""
Unity 指令桥：Agent 通过 FastAPI /enqueue 向 Unity 发送环境控制指令。 (EN: Unity command bridge: Agent sends environment-control commands to Unity via FastAPI /enqueue.)
# EN: Unity command bridge: the Agent sends environment control commands to Unity via FastAPI /enqueue.
需先启动 main.py 的 uvicorn 服务（默认 http://127.0.0.1:8000）。 (EN: Start main.py uvicorn service first, default http://127.0.0.1:8000.)
# EN: Start the uvicorn service in main.py first (default http://127.0.0.1:8000).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    requests = None

# 从环境变量读取，默认本机 8000  # EN: Read from environment variable; default is local port 8000.
UNITY_BRIDGE_URL = os.environ.get("UNITY_BRIDGE_URL", "http://127.0.0.1:8000")


def enqueue_command(cmd: Dict[str, Any], base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    将一条 Unity 指令入队，供 Unity AgentNetworkBridge 轮询执行。 (EN: Enqueue one Unity command for AgentNetworkBridge polling.)
    # EN: Enqueue one Unity command for polling execution by Unity AgentNetworkBridge.
    cmd 需包含 "action" 字段，其余字段与 main.py UnityCommand 一致，例如： (EN: cmd must include "action"; other fields follow main.py UnityCommand, for example:)
    # EN: cmd must include "action"; other fields should match main.py UnityCommand, for example:
      {"action": "PlaceObject", "objectName": "Chair1", "x": 0, "y": 0, "z": 2, "rotationY": 0}
      {"action": "PlaceHuman", "humanName": "Models/HumanM_Model", "x": 0, "y": 0, "z": 3, "rotationY": 0}
      {"action": "SetTimeOfDay", "hour": 18}
      {"action": "PlayHumanAnimationFromResources", "controllerPathInResources": "AnimatorControllers/HumanM@Walk01_Forward", "stateName": "HumanM@Walk01_Forward"}
    返回 {"queued": 1, "queue_size": n} 或 {"error": "..."}。 (EN: Returns {"queued": 1, "queue_size": n} or {"error": "..."}.)
    # EN: Returns {"queued": 1, "queue_size": n} or {"error": "..."}.
    """
    url = (base_url or UNITY_BRIDGE_URL).rstrip("/") + "/enqueue"
    if requests is None:
        return {"error": "requests not installed; pip install requests"}
    try:
        # 只传非 None 的键，避免 Pydantic 校验多余字段  # EN: Only send non-None keys to avoid extra-field validation issues in Pydantic.
        payload = {k: v for k, v in cmd.items() if v is not None}
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e), "queued": 0}


def enqueue_place_object(object_name: str, x: float = 0, y: float = 0, z: float = 2, rotation_y: float = 0) -> Dict[str, Any]:
    """便捷：放置物体（Resources/Prefabs 下）。"""  # EN: Convenience helper: place an object (under Resources/Prefabs).
    return enqueue_command({
        "action": "PlaceObject",
        "objectName": object_name,
        "x": x, "y": y, "z": z,
        "rotationY": rotation_y,
    })


def enqueue_place_human(human_name: str, x: float = 0, y: float = 0, z: float = 3, rotation_y: float = 0) -> Dict[str, Any]:
    """便捷：放置人形（Resources/Prefabs 下，如 Models/HumanM_Model）。"""  # EN: Convenience helper: place a human model (under Resources/Prefabs, e.g., Models/HumanM_Model).
    return enqueue_command({
        "action": "PlaceHuman",
        "humanName": human_name,
        "x": x, "y": y, "z": z,
        "rotationY": rotation_y,
    })


def enqueue_set_time_of_day(hour: float) -> Dict[str, Any]:
    """便捷：设置时间（0~24）。"""  # EN: Convenience helper: set time (0-24).
    return enqueue_command({"action": "SetTimeOfDay", "hour": hour})


def enqueue_play_human_animation(controller_path: str, state_name: str, cross_fade_time: float = 0.25f) -> Dict[str, Any]:
    """便捷：播放人形动画（先确保 Controller 再播 state）。"""  # EN: Convenience helper: play human animation (ensure controller first, then play state).
    return enqueue_command({
        "action": "PlayHumanAnimationFromResources",
        "controllerPathInResources": controller_path,
        "stateName": state_name,
        "crossFadeTime": cross_fade_time,
    })
