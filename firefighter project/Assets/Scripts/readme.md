# Quest 3 AI Environment Builder

This project is a Unity-based simulation environment designed to bridge AI agents with Mixed Reality (MR). It allows an external system or internal logic to procedurally build environments by placing 3D assets and triggering actions based on descriptions.

## 🛠 Prerequisites

Before starting, ensure you have the following installed:
* **Unity Hub & Unity 6** (Recommended: 2022.3 LTS or newer).
* **Meta Quest Link App** (For PCVR testing).
* **Cursor IDE** (Highly recommended for AI-assisted development).
* **Android Build Support** (Installed via Unity Hub).

## 🚀 Environment Setup

### 1. Clone & Open
1. Clone this repository to your local machine.
2. Open **Unity Hub**, click **Add**, and select the project folder.

### 2. Configure XR & Platform
1. Go to `File > Build Settings`.
2. Select **Android** and click **Switch Platform**.
3. Navigate to `Project Settings > XR Plug-in Management`.
4. Enable **Oculus** (Meta XR) for both the Desktop and Android tabs.

### 3. Handle Shader Issues (The "Pink" Asset Fix)
If models appear pink, they need to be converted to the Universal Render Pipeline (URP):
1. Go to `Window > Rendering > Render Pipeline Converter`.
2. Select **Built-in to URP** and check **Material Upgrade**.
3. Click **Initialize Converters** and then **Convert Assets**.

## 🏗 Project Structure

* `/Assets/Resources/Prefabs`: Contains all 3D models (Chairs, Tables, etc.) that the AI can spawn.
* `/Assets/Scripts`: Contains the core C# logic.
    * `EnvironmentAgent.cs`: The main handler for spawning and placing objects.
* `/Assets/Scenes`: The main simulation scene.

## 🕹 Running the Simulation Demo

Follow these steps to test the environment placement logic:

### Step 1: Prepare the Scene
1. Open the `SampleScene` from the `Assets/Scenes` folder.
2. Ensure the **AI_Manager** GameObject exists in the Hierarchy and has the `EnvironmentAgent` script attached.

### Step 2: Enter Play Mode
1. Click the **Play** button at the top of the Unity Editor.
2. If testing on a **Meta Quest 3**, ensure your headset is connected via Link/Air Link first.

### Step 3: Trigger AI Placement
1. Select the **AI_Manager** in the Hierarchy.
2. In the **Inspector** window, right-click the **Environment Agent** component header.
3. Select **"Test Spawn Chair"** or **"Test Spawn Table"**.
4. Observe the objects appearing in the Game View at the designated coordinates.

## 🤖 让 Agent 控制环境参数

`EnvironmentAgent` 提供统一接口，供外部 AI 或游戏逻辑控制场景环境。在场景中挂载该脚本的物体（如 **AI_Manager**）上获取 `EnvironmentAgent` 组件后，可调用以下方法：

| 功能 | 方法 | 示例 |
|------|------|------|
| 放置物体 | `PlaceObject(objectName, position, rotationY)` | `PlaceObject("Chair1", new Vector3(0,0,2), 0)` |
| 光照强度 | `SetLightIntensity(float)` | `SetLightIntensity(0.5f)` 调暗 |
| 光照颜色 | `SetLightColor(r, g, b)` | `SetLightColor(1f, 0.9f, 0.8f)` |
| 时间（太阳） | `SetTimeOfDay(hour)` | `SetTimeOfDay(18f)` 傍晚 |
| 雾效开关 | `SetFogEnabled(bool)` | `SetFogEnabled(true)` |
| 雾浓度 | `SetFogDensity(float)` | `SetFogDensity(0.02f)` |
| 雾颜色 | `SetFogColor(r, g, b)` | `SetFogColor(0.7f, 0.7f, 0.8f)` |
| 环境光 | `SetAmbientColor(r, g, b)` | `SetAmbientColor(0.2f, 0.2f, 0.3f)` |
| 重力 | `SetGravity(gravityY)` | `SetGravity(-9.81f)` |
| 时间缩放 | `SetTimeScale(scale)` | `SetTimeScale(0f)` 暂停 |

**在 Unity 内由其他脚本调用：**

```csharp
var agent = FindFirstObjectByType<EnvironmentAgent>();
if (agent != null)
{
    agent.SetTimeOfDay(20f);      // 夜晚
    agent.SetFogEnabled(true);
    agent.SetFogDensity(0.015f);
    agent.PlaceObject("Chair1", new Vector3(1, 0, 3), 45);
}
```

**Inspector 快速测试：** 选中挂有 `EnvironmentAgent` 的物体，在组件标题上右键，可选择「Env: Night (18h)」「Env: Day (12h)」「Env: Fog On/Off」等快速验证效果。

---

## 🌐 和 Python FastAPI Agent 通信

### Unity 端（本项目）

1. 在 `AI_Manager`（或任意场景物体）上添加两个组件：
   - `EnvironmentAgent`
   - `AgentNetworkBridge`
2. 在 `AgentNetworkBridge` Inspector 中：
   - `Server Base Url` 填写你的 Python FastAPI 地址，例如：
     - 在本机调试：`http://127.0.0.1:8000`
     - Quest 真机连接 PC：`http://<你电脑的局域网 IP>:8000`（例如 `http://192.168.1.10:8000`）
   - 将 `EnvironmentAgent` 字段拖拽引用到同一物体上的组件实例（或留空，脚本会自动查找场景中的第一个 `EnvironmentAgent`）。
3. 运行场景后，Unity 会每隔一段时间（`pollIntervalSeconds`）请求：
   - `GET {serverBaseUrl}/command`
4. Python 返回的 JSON 会被反序列化为一个指令对象，并映射到 `EnvironmentAgent` 的方法上。

### 指令 JSON 协议（FastAPI 需返回的格式）

Unity 端期望的 JSON 为**扁平字段**，例如：

```json
{ "action": "SetTimeOfDay", "hour": 18 }
```

或：

```json
{ "action": "SetFogEnabled", "enabled": true }
```

或：

```json
{
  "action": "PlaceObject",
  "objectName": "Chair1",
  "x": 1.0,
  "y": 0.0,
  "z": 2.0,
  "rotationY": 45.0
}
```

目前支持的 `action` 与字段：

| action | 使用字段 | 效果 |
|--------|----------|------|
| `SetTimeOfDay` | `hour` | 调用 `SetTimeOfDay(hour)` |
| `SetFogEnabled` | `enabled` | 调用 `SetFogEnabled(enabled)` |
| `SetFogDensity` | `density` | 调用 `SetFogDensity(density)` |
| `SetLightIntensity` | `scale` | 调用 `SetLightIntensity(scale)` |
| `SetLightColor` | `r, g, b` | 调用 `SetLightColor(r,g,b)` |
| `SetAmbientColor` | `r, g, b` | 调用 `SetAmbientColor(r,g,b)` |
| `SetGravity` | `gravityY` | 调用 `SetGravity(gravityY)` |
| `SetTimeScale` | `scale` | 调用 `SetTimeScale(scale)` |
| `PlaceObject` | `objectName, x, y, z, rotationY` | 在场景中放置预制体 |

### Python FastAPI 示例（单独 repo 使用）

在你的 Python 项目（**独立 repo / 独立文件夹**）中，创建例如 `main.py`：

```python
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional


class AgentCommand(BaseModel):
    action: str

    hour: Optional[float] = None
    enabled: Optional[bool] = None
    density: Optional[float] = None

    r: Optional[float] = None
    g: Optional[float] = None
    b: Optional[float] = None

    gravityY: Optional[float] = None
    scale: Optional[float] = None

    objectName: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    rotationY: Optional[float] = None


app = FastAPI()


@app.get("/command", response_model=Optional[AgentCommand])
def get_command():
    \"\"\"这里调用你自己的 Python agent 逻辑，决定下一条指令。
    返回 None / null 表示当前没有指令。
    \"\"\"
    # 示例：简单返回一个固定的时间和雾效指令
    return AgentCommand(action="SetTimeOfDay", hour=18.0)
```

运行：

```bash
pip install fastapi uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000
```

确认浏览器能打开 `http://127.0.0.1:8000/command`，再在 Unity 中设置相同地址即可。

---

## 🤖 Extending the AI Logic
To add more complex models:
1. Drag your new 3D model into the scene, configure its scale and colliders, and save it as a Prefab in `Assets/Resources/Prefabs`.
2. Update the `EnvironmentAgent.cs` script via **Cursor** to include new action triggers or movement logic.