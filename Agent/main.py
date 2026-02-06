from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Deque, List, Optional, Union

from fastapi import FastAPI
from pydantic import BaseModel, Field


# -----------------------------
# Unity <-> Python 协议模型（与 Unity 端 AgentNetworkBridge.cs 对齐：扁平字段）
# -----------------------------
class UnityCommand(BaseModel):
    action: str = Field(..., description="Unity side action name, e.g. SetTimeOfDay / PlaceObject")

    # Environment params
    hour: Optional[float] = None
    enabled: Optional[bool] = None
    density: Optional[float] = None
    r: Optional[float] = None
    g: Optional[float] = None
    b: Optional[float] = None
    gravityY: Optional[float] = None
    scale: Optional[float] = None

    # Object placement
    objectName: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    rotationY: Optional[float] = None


app = FastAPI(title="Firefighter Agent Bridge", version="0.1.0")


_queue: Deque[UnityCommand] = deque()
_lock = Lock()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/command", response_model=Optional[UnityCommand])
def get_command():
    """Unity 端轮询接口：返回并弹出下一条指令。若无指令，返回 null。"""
    with _lock:
        if not _queue:
            return None
        return _queue.popleft()


@app.post("/enqueue")
def enqueue(cmd: Union[UnityCommand, List[UnityCommand]]):
    """入队接口：你可以从 agent 逻辑里调用，或用 Postman/curl 手动推指令。"""
    with _lock:
        if isinstance(cmd, list):
            for c in cmd:
                _queue.append(c)
            return {"queued": len(cmd), "queue_size": len(_queue)}
        _queue.append(cmd)
        return {"queued": 1, "queue_size": len(_queue)}


@app.post("/clear")
def clear_queue():
    with _lock:
        n = len(_queue)
        _queue.clear()
        return {"cleared": n, "queue_size": 0}


# 运行方式（在 S:\Firefighter_project\Agent\firefighter 目录下）：
#   pip install -r requirements.txt
#   uvicorn main:app --host 0.0.0.0 --port 8000

