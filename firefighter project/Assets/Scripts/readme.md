# Quest 3 AI Environment Builder

This project is a Unity-based simulation environment designed to bridge AI agents with Mixed Reality (MR).
It allows an external system or internal logic to procedurally build environments by placing 3D assets and
triggering actions based on descriptions.

## Prerequisites

Before starting, ensure you have the following installed:
- Unity Hub and Unity 6 (recommended: 2022.3 LTS or newer)
- Meta Quest Link App (for PCVR testing)
- Cursor IDE (recommended for AI-assisted development)
- Android Build Support (installed via Unity Hub)

## Environment Setup

### 1. Clone and Open
1. Clone this repository to your local machine.
2. Open Unity Hub, click **Add**, and select the project folder.

### 2. Configure XR and Platform
1. Go to `File > Build Settings`.
2. Select **Android** and click **Switch Platform**.
3. Navigate to `Project Settings > XR Plug-in Management`.
4. Enable **Oculus (Meta XR)** for both Desktop and Android tabs.

### 3. Handle Shader Issues ("Pink" Asset Fix)
If models appear pink, convert to URP:
1. Go to `Window > Rendering > Render Pipeline Converter`.
2. Select **Built-in to URP** and check **Material Upgrade**.
3. Click **Initialize Converters** and then **Convert Assets**.

## Project Structure

- `Assets/Resources/Prefabs`: 3D models that the AI can spawn
- `Assets/Scripts`: core C# logic
  - `EnvironmentAgent.cs`: main handler for spawning and placing objects
- `Assets/Scenes`: main simulation scene

## Running the Simulation Demo

### Step 1: Prepare the Scene
1. Open `SampleScene` from `Assets/Scenes`.
2. Ensure the **AI_Manager** GameObject exists in the Hierarchy and has `EnvironmentAgent` attached.

### Step 2: Enter Play Mode
1. Click **Play** in the Unity Editor.
2. If testing on Meta Quest 3, connect via Link/Air Link first.

### Step 3: Trigger AI Placement
1. Select **AI_Manager** in the Hierarchy.
2. In the Inspector, right-click the **Environment Agent** component header.
3. Select **Test Spawn Chair** or **Test Spawn Table**.
4. Observe objects appearing in the Game View.

## Let the Agent Control Environment Parameters

`EnvironmentAgent` provides a unified interface for external AI or game logic to control the scene.
After attaching the script to a GameObject (e.g., **AI_Manager**), you can call:

| Function | Method | Example |
|---|---|---|
| Place object | `PlaceObject(objectName, position, rotationY)` | `PlaceObject("Chair1", new Vector3(0,0,2), 0)` |
| Light intensity | `SetLightIntensity(float)` | `SetLightIntensity(0.5f)` |
| Light color | `SetLightColor(r, g, b)` | `SetLightColor(1f, 0.9f, 0.8f)` |
| Time of day | `SetTimeOfDay(hour)` | `SetTimeOfDay(18f)` |
| Fog toggle | `SetFogEnabled(bool)` | `SetFogEnabled(true)` |
| Fog density | `SetFogDensity(float)` | `SetFogDensity(0.02f)` |
| Fog color | `SetFogColor(r, g, b)` | `SetFogColor(0.7f, 0.7f, 0.8f)` |
| Ambient light | `SetAmbientColor(r, g, b)` | `SetAmbientColor(0.2f, 0.2f, 0.3f)` |
| Gravity | `SetGravity(gravityY)` | `SetGravity(-9.81f)` |
| Time scale | `SetTimeScale(scale)` | `SetTimeScale(0f)` |

**Called from Unity scripts:**

```csharp
var agent = FindFirstObjectByType<EnvironmentAgent>();
if (agent != null)
{
    agent.SetTimeOfDay(20f);      // night
    agent.SetFogEnabled(true);
    agent.SetFogDensity(0.015f);
    agent.PlaceObject("Chair1", new Vector3(1, 0, 3), 45);
}
```

**Inspector quick test:** select a GameObject with `EnvironmentAgent`, right-click the component header,
and choose **Env: Night (18h)**, **Env: Day (12h)**, **Env: Fog On/Off** for quick checks.

## Python FastAPI Agent Communication

### Unity side (this project)

1. Add two components to `AI_Manager` (or any scene object):
   - `EnvironmentAgent`
   - `AgentNetworkBridge`
2. In the `AgentNetworkBridge` Inspector:
   - Set `Server Base Url` to your FastAPI address, e.g.:
     - Local: `http://127.0.0.1:8000`
     - Quest on PC: `http://<your LAN IP>:8000` (e.g. `http://192.168.1.10:8000`)
   - Drag `EnvironmentAgent` into the field (or leave empty and it will auto-find one in the scene).
3. When the scene is running, Unity polls:
   - `GET {serverBaseUrl}/command`
4. The returned JSON is deserialized into a command object and mapped to `EnvironmentAgent` methods.

### Command JSON protocol (FastAPI response format)

Unity expects **flat JSON fields**, for example:

```json
{ "action": "SetTimeOfDay", "hour": 18 }
```

or:

```json
{ "action": "SetFogEnabled", "enabled": true }
```

or:

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

Supported actions and fields:

| action | fields | effect |
|---|---|---|
| `SetTimeOfDay` | `hour` | calls `SetTimeOfDay(hour)` |
| `SetFogEnabled` | `enabled` | calls `SetFogEnabled(enabled)` |
| `SetFogDensity` | `density` | calls `SetFogDensity(density)` |
| `SetLightIntensity` | `scale` | calls `SetLightIntensity(scale)` |
| `SetLightColor` | `r, g, b` | calls `SetLightColor(r, g, b)` |
| `SetAmbientColor` | `r, g, b` | calls `SetAmbientColor(r, g, b)` |
| `SetGravity` | `gravityY` | calls `SetGravity(gravityY)` |
| `SetTimeScale` | `scale` | calls `SetTimeScale(scale)` |
| `PlaceObject` | `objectName, x, y, z, rotationY` | places a prefab in the scene |

### Python FastAPI example (separate repo)

In your Python project (separate repo/folder), create `main.py`:

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
    """Call your own Python agent logic here to decide the next command.
    Return None/null if no command is available.
    """
    # Example: return a simple fixed time/fog command
    return AgentCommand(action="SetTimeOfDay", hour=18.0)
```

Run:

```bash
pip install fastapi uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000
```

Verify that you can open `http://127.0.0.1:8000/command` in a browser,
then set the same address in Unity.

## Extending the AI Logic

To add more complex models:
1. Drag your new 3D model into the scene, configure its scale and colliders, and save it as a Prefab in `Assets/Resources/Prefabs`.
2. Update `EnvironmentAgent.cs` to include new action triggers or movement logic.
