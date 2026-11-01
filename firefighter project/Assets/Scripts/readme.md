# Unity Scripts

Runtime and editor C# scripts that let the Python LLM agent (`Agent/` at the repository
root) build and drive a VR healthcare training scene. Python and Unity communicate only
through JSON and PNG files, so no network setup is needed.

```
Python agent ──► Assets/Scripts/unity_commands.json ──► AgentNetworkBridge ──► EnvironmentAgent
     ▲                                                                              │
     ├──── Agent/api_files/scene_state.json ◄───────────────────────────────────────┤
     ├──── Agent/api_files/scene_screenshot_{xz,xy,yz,iso}.png ◄── ScreenshotLoopSender
     └──── Assets/Scripts/interaction_events.json ◄── InteractionEventReporter / PlayerTouchHighlighter
```

## Scripts

### Runtime (`Assets/Scripts/`)

| Script | Role |
|---|---|
| `AgentNetworkBridge` | Polls `unity_commands.json` (or an HTTP endpoint), runs each new command on `EnvironmentAgent`, and marks it as executed. |
| `EnvironmentAgent` | The scene-control API: spawns, moves and deletes objects and characters, controls lighting and fog, plays animations, highlights objects, shows speech bubbles, and writes `scene_state.json` after every change. |
| `InteractionSetup` | Adds a collider, a kinematic Rigidbody and an `XRGrabInteractable` to spawned objects so the player can hover over and grab them. |
| `InteractionEventReporter` | Records XR hover / grab / release / activate events (and touch events) to `interaction_events.json`. |
| `InteractionAnimationController` | Plays an immediate character animation and highlight when the player interacts with an object, without waiting for Python. |
| `PlayerTouchHighlighter` | Phase 2 proximity detection: highlights objects and characters the player walks into and reports them as `touch` events. |
| `PlayerAvatarController` | Gives the player a visible body that follows the XR rig and can be animated by the agent. |
| `SpeechBubble` | World-space text bubble above a character's head, built entirely in code. |
| `ScreenshotLoopSender` | Saves top-down, front, side and 45-degree screenshots of the room every few seconds for the LLM. |
| `AutoScreenshotBootstrap` | Ensures a `ScreenshotLoopSender` exists in every scene (runtime and builds). |

### Editor (`Assets/Editor/`)

| Script | Role |
|---|---|
| `AgentNetworkBridgeBootstrap` | Adds an `AgentNetworkBridge` to the `EnvironmentAgent` GameObject when entering Play Mode, if the scene has none. |
| `ScreenshotLoopEditorBootstrap` | Adds a `ScreenshotLoopSender` when entering Play Mode, if the scene has none. |
| `PlayerAvatarResourceLinker` | Copies the Kevin Iglesias dummy prefabs into `Assets/Resources/Prefabs/` so the Male / Female avatar presets can be loaded at runtime. |

## Scene setup

`Assets/Scenes/SampleScene.unity` is already set up. For a new scene:

1. Create an empty GameObject (the sample scene calls it **AI_Manager**) and add
   `EnvironmentAgent`. This automatically adds `InteractionSetup`,
   `InteractionAnimationController` and `InteractionEventReporter`; a
   `PlayerAvatarController` is added at Start.
2. Optionally add `AgentNetworkBridge` to the same GameObject. In the Editor it is added
   automatically on entering Play Mode if missing.
3. Make sure the scene has an XR rig (e.g. XR Origin) and a camera tagged **MainCamera**.
4. Enter Play Mode, then start the Python agent (see the repository `README.md`).

`ScreenshotLoopSender` and `PlayerTouchHighlighter` are created automatically and need no
setup. `PlayerTouchHighlighter` stays disabled until the agent sends `SetPhase` with
`phase` 2.

## Files exchanged with Python

| File | Written by | Read by | Contents |
|---|---|---|---|
| `Assets/Scripts/unity_commands.json` | Python (`Agent/main.py`) | `AgentNetworkBridge` | JSON array of commands. Unity sets `executed` / `done` to `true` after running each one and writes the file back. |
| `Agent/api_files/scene_state.json` | `EnvironmentAgent` | Python | One entry per tracked object: `objectName`, `x`, `y`, `z`, `rotationY`, and world-space renderer bounds (`boundsMinX/Y/Z`, `boundsMaxX/Y/Z`, `boundsSizeX/Y/Z`). |
| `Agent/api_files/scene_screenshot_{xz,xy,yz,iso}.png` | `ScreenshotLoopSender` | Python | Top-down, front, side and perspective views. The location can be changed with the `UNITY_SCREENSHOT_PATH` environment variable or the component's `overrideBasePath`. |
| `Assets/Scripts/interaction_events.json` | `InteractionEventReporter` | Python | JSON array of player events: `id`, `timestamp`, `type`, `objectName`, `objectPosition`, `playerPosition`, `playerLookDir`, `processed`. |

## Command protocol

Each command is a flat JSON object with an `action` and the fields that action uses.
`unity_commands.json` holds an array of these objects; commands whose `executed` flag is
`false` (or missing) are run in order. `id` is optional and only used in log messages.

```json
[
  { "id": "cmd_1", "action": "SetTimeOfDay", "hour": 18 },
  { "id": "cmd_2", "action": "PlaceObject", "objectName": "Bed_01", "x": 1.0, "y": 0.0, "z": 2.0, "rotationY": 90 },
  { "id": "cmd_3", "action": "PlaceHuman", "humanName": "Doctor_01", "x": -1.0, "y": 0.0, "z": 3.0, "rotationY": 180 },
  { "id": "cmd_4", "action": "ShowSpeechBubble", "objectName": "Doctor_01", "message": "Welcome!", "duration": 4 }
]
```

Fields that are omitted take their default value (`0`, `false` or empty), so for example
an omitted `rotationY` means 0 degrees. Positions are in world meters; rotations are yaw in
degrees. Colors use channels from 0 to 1.

### Environment

| `action` | Fields | Effect |
|---|---|---|
| `SetTimeOfDay` | `hour` | Rotates the main directional light to simulate the sun at that hour (0 to 24). |
| `SetLightIntensity` | `scale` | Sets the main directional light intensity (clamped to 0 to 2). |
| `SetLightColor` | `r`, `g`, `b` | Sets the main directional light color. |
| `SetAmbientColor` | `r`, `g`, `b` | Sets the ambient light color. |
| `SetFogEnabled` | `enabled` | Turns fog on or off. |
| `SetFogDensity` | `density` | Sets fog density (clamped to 0 to 0.2). |
| `SetGravity` | `gravityY` | Sets gravity to (0, `gravityY`, 0). Unity's default is -9.81. |
| `SetTimeScale` | `scale` | Sets the time scale (clamped to 0 to 2; 0 pauses, 1 is normal). |

### Objects

| `action` | Fields | Effect |
|---|---|---|
| `PlaceObject` | `objectName`, `x`, `y`, `z`, `rotationY` | Spawns a prefab (looked up by name in any `Resources` folder), drops it onto the surface below and makes it grabbable. Repeated names are tracked as `Name__inst_2`, `Name__inst_3`, ... If the name looks like a character (e.g. `Doctor_01` or `Nurse_02_Body`), it is handled like `PlaceHuman` instead. |
| `CreateObjectByName` | `objectName`, `x`, `y`, `z`, `rotationY`, `scaleX`, `scaleY`, `scaleZ` | Creates a prefab from `Resources/Prefabs/` or a primitive (`Cube`/`Box`, `Sphere`/`Ball`, `Capsule`, `Cylinder`, `Plane`, `Quad`) with the given scale (values of 0 or less mean 1). Used to build the floor, walls and ceiling. Objects above y = 0.05 or taller than 1.5 are not floor-snapped. |
| `MoveObject` | `objectName`, `x`, `y`, `z`, `rotationY` | Teleports an existing object. |
| `DeleteObject` | `objectName` | Destroys an existing object. |
| `ClearScene` | none | Destroys all spawned objects and characters. Floor Cubes, cameras, lights, XR rigs and objects tagged `Player` are kept. |
| `HighlightObject` | `objectName`, `r`, `g`, `b`, `duration` | Tints the object and makes it glow. After `duration` seconds the original colors are restored; 0 or less keeps the highlight. |
| `ClearHighlight` | `objectName` | Turns off the object's emission glow. |

Objects are found by exact name first, then with Unity's `(Clone)` suffix, then by
case-insensitive name prefix.

### Characters and animation

| `action` | Fields | Effect |
|---|---|---|
| `PlaceHuman` | `humanName` (or `objectName`), `x`, `y`, `z`, `rotationY` | Spawns a character prefab on the floor in an idle pose and registers it under its name. The most recently placed character becomes the default animation target. |
| `MoveCharacter` | `objectName` (or `humanName`), `x`, `y`, `z`, `rotationY` | Teleports a character. |
| `WalkCharacterTo` | `objectName` (or `humanName`), `x`, `y`, `z`, `rotationY`, `duration`, `controllerPath`, `idleControllerPath` | Plays a walk animation while moving the character to the target over `duration` seconds (default 2), then switches to idle. Controllers default to `AnimatorControllers/HumanM@Walk01_Forward` and `AnimatorControllers/HumanM@Idles`. |
| `PlayHumanAnimationFromResources` | `controllerPath`, `stateName`, `crossFadeTime`, `targetModel` | Loads an AnimatorController and crossfades into `stateName`. This is the most reliable way to play an animation. |
| `PlayHumanAnimation` | `stateName`, `crossFadeTime`, `targetModel` | Crossfades into a state of the character's current controller. |
| `SetHumanAnimationController` | `controllerPath` | Replaces the default character's AnimatorController. |
| `SetHumanAnimatorFloat` | `paramName`, `value` | Sets a float parameter on the default character's Animator. |
| `SetHumanAnimatorBool` | `paramName`, `boolValue` | Sets a bool parameter on the default character's Animator. |
| `SetHumanAnimatorTrigger` | `paramName` | Fires a trigger on the default character's Animator. |
| `ShowSpeechBubble` | `objectName` (or `humanName`), `message`, `duration` | Shows a speech bubble above a character for `duration` seconds (default 5). |
| `AttachPlayerAvatar` | `objectName` (or `humanName`) | Replaces the player's visible body with the given prefab and registers it as the character `player`. |

Notes:

- `targetModel` is a character name (as placed, e.g. `Doctor_01`, or `player` for the
  player avatar). When it is empty, the default character is animated.
- `crossFadeTime` defaults to 0.25 seconds when it is 0 or omitted.
- `controllerPath` is a path inside a `Resources` folder without the file extension, e.g.
  `AnimatorControllers/HumanF@Talking`. For the Kevin Iglesias controllers the state name
  matches the controller name (`HumanF@Talking`). The older field name
  `controllerPathInResources` is still accepted.

### Other

| `action` | Fields | Effect |
|---|---|---|
| `SetPhase` | `phase` | `2` (or higher) starts Phase 2: the XR rig is tagged `Player` and touch detection is enabled. |
| `DebugLog` | `message` | Writes `message` to the Unity console. |

## HTTP mode (optional)

`AgentNetworkBridge` can poll a server instead of reading a file. Untick
**Use File Commands** and set **Server Base Url** (e.g. `http://127.0.0.1:8000`, or the
PC's LAN address when running on a Quest). Unity then calls `GET {serverBaseUrl}/command`
every poll interval and expects a single command object in the format above, or `null`
when there is nothing to do. In this mode commands are not marked as executed, so the
server must return each command only once.

## Adding new assets

Any prefab placed under a `Resources` folder can be spawned by its file name; the lookup
is case-insensitive. See `EnvironmentAgent_Resources.md` for how to add characters and
animator controllers.

If imported models appear pink, convert their materials to URP with
**Window > Rendering > Render Pipeline Converter > Built-in to URP > Material Upgrade**.

## Inspector test tools

With the `EnvironmentAgent` component selected in Play Mode, right-click its header to
access:

- **Debug Human Rig (Default)**: logs the Animators and skinned-mesh bindings of the
  default character.
- **ithappy: Spawn Furniture / Prop / Character (test)**: spawns the test prefabs assigned
  in the Inspector.
- **ithappy Anim: Idle / Walk Forward / Run Forward / Attack Punch**: plays a test
  animation on the spawned test character.
