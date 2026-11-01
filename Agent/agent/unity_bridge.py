"""
Unity command bridge: the Agent sends environment control commands to Unity via FastAPI /enqueue.
Start the uvicorn service in main.py first (default http://127.0.0.1:8000).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    requests = None

# Read from environment variable; default is local port 8000.
UNITY_BRIDGE_URL = os.environ.get("UNITY_BRIDGE_URL", "http://127.0.0.1:8000")


def enqueue_command(cmd: Dict[str, Any], base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Enqueue one Unity command for polling execution by Unity AgentNetworkBridge.
    cmd must include "action"; other fields should match main.py UnityCommand, for example:
      {"action": "PlaceObject", "objectName": "Chair1", "x": 0, "y": 0, "z": 2, "rotationY": 0}
      {"action": "PlaceHuman", "humanName": "Models/HumanM_Model", "x": 0, "y": 0, "z": 3, "rotationY": 0}
      {"action": "SetTimeOfDay", "hour": 18}
      {"action": "PlayHumanAnimationFromResources", "controllerPathInResources": "AnimatorControllers/HumanM@Walk01_Forward", "stateName": "HumanM@Walk01_Forward"}
    Returns {"queued": 1, "queue_size": n} or {"error": "..."}.
    """
    url = (base_url or UNITY_BRIDGE_URL).rstrip("/") + "/enqueue"
    if requests is None:
        return {"error": "requests not installed; pip install requests"}
    try:
        # Only send non-None keys to avoid extra-field validation issues in Pydantic.
        payload = {k: v for k, v in cmd.items() if v is not None}
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e), "queued": 0}


def enqueue_place_object(object_name: str, x: float = 0, y: float = 0, z: float = 2, rotation_y: float = 0) -> Dict[str, Any]:
    """Convenience helper: place an object (under Resources/Prefabs)."""
    return enqueue_command({
        "action": "PlaceObject",
        "objectName": object_name,
        "x": x, "y": y, "z": z,
        "rotationY": rotation_y,
    })


def enqueue_place_human(human_name: str, x: float = 0, y: float = 0, z: float = 3, rotation_y: float = 0) -> Dict[str, Any]:
    """Convenience helper: place a human model (under Resources/Prefabs, e.g., Models/HumanM_Model)."""
    return enqueue_command({
        "action": "PlaceHuman",
        "humanName": human_name,
        "x": x, "y": y, "z": z,
        "rotationY": rotation_y,
    })


def enqueue_set_time_of_day(hour: float) -> Dict[str, Any]:
    """Convenience helper: set time (0-24)."""
    return enqueue_command({"action": "SetTimeOfDay", "hour": hour})


def enqueue_play_human_animation(controller_path: str, state_name: str, cross_fade_time: float = 0.25) -> Dict[str, Any]:
    """Convenience helper: play human animation (ensure controller first, then play state)."""
    return enqueue_command({
        "action": "PlayHumanAnimationFromResources",
        "controllerPathInResources": controller_path,
        "stateName": state_name,
        "crossFadeTime": cross_fade_time,
    })
