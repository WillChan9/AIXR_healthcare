# EnvironmentAgent Resources Setup

Like Chair and Table, human prefabs and animator controllers are loaded by path from **Resources**.
Follow the steps below to place files in the correct folders.

## 1. Directory Structure (you maintain this)

```
Assets/Resources/
  Prefabs/                 # all placeable prefabs (chairs, tables, humans)
    HumanM_Idle01.prefab    # human prefab (create yourself)
  AnimatorControllers/      # Animator Controllers (.controller) for humans
```

- **Prefabs**: same as Chair/Table. When calling `PlaceObject("Chair1", ...)` or `PlaceHuman("HumanM_Idle01", ...)`, assets are loaded from `Resources/Prefabs/` by name.
- **AnimatorControllers**: used to switch human actions. When calling `SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward")`, the controller is loaded from `Resources/AnimatorControllers/`.

## 2. Human Prefabs: how to create and where to put them

### Option A: Use Kevin Iglesias FBX models
1. In Project, find:
   `Assets/Kevin Iglesias/Human Animations/Models/HumanM_Model.fbx` (or `HumanF_Model.fbx`).
2. Drag the FBX into **Hierarchy** (creates a rigged model).
3. Select the root object, add an **Animator** component in Inspector.
4. In the Animator component, assign a `.controller` (from Kevin Iglesias controllers), for example `HumanM@Walk01_Forward`.
5. In Project, open `Assets/Resources/Prefabs` and drag the human root object from Hierarchy into the folder to create a prefab (rename to something like `HumanM_Idle01`).
6. Optionally remove the temporary object from Hierarchy.

### Option B: Use an existing human in the scene
1. Select the human object in Hierarchy and confirm it has an **Animator** and controller.
2. Open `Assets/Resources/Prefabs` in Project.
3. Drag the human object into the folder to create a prefab.
4. Rename the prefab to a simple name (e.g., `HumanM_Idle01`) and use that name in code.

Once created, humans work the same as Chair/Table: they are loaded by name from `Resources/Prefabs/`.
If you use `PlaceHuman`, the placed human becomes the default target for animation calls.

## 3. Animator Controllers: how to copy into Resources

1. Find the controller you need in:
   `Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/AnimatorControllers/`
2. Copy the `.controller` file into `Assets/Resources/AnimatorControllers/`.
3. Use the **Resources-relative path** (no extension) in code:
   `AnimatorControllers/HumanM@Walk01_Forward`

**Important:** The controller must exist under `Resources/AnimatorControllers/` for `Resources.Load` to find it.

## 4. How to use in code (same as Chair/Table)

- **Place human** (from Resources prefab):
  - Name must match the prefab name under `Resources/Prefabs/`.

- **Switch animation** (from Resources controller):
  - Path is relative to `Resources`, e.g. `AnimatorControllers/xxx`.

- **Default Human Model**:
  - If you use `PlaceHuman`, it becomes the default target automatically.
  - If you placed a human manually in the scene, set `Default Human Model` in Inspector once.

Summary: **Human = prefab name in Prefabs; Animation = controller path in AnimatorControllers**.
