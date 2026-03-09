# Kevin Iglesias Human Animations - Folder and File Mapping

Project path: `Assets/Kevin Iglesias/Human Animations/`.
This document lists what each folder is for and how it maps to EnvironmentAgent usage (Resources).

## 1. Overview: Folder Purpose

| Folder | Purpose | How to use in EnvironmentAgent |
|---|---|---|
| **Models** | Humanoid skeleton models (FBX) | Use to build human prefabs, then place under `Resources/Prefabs/` |
| **Animations** | Animation clips (.fbx) | Referenced by controllers; usually no manual drag needed |
| **Unity Demo Scenes / AnimatorControllers** | Animator controllers (.controller) | Copy into `Resources/AnimatorControllers/`, then call `SetHumanAnimationController(null, "AnimatorControllers/xxx")` |
| **Scripts** | SpineProxy and related scripts | Attach SpineProxy to B-spineProxy for upper/lower body blending |
| **Materials** | Materials | Used for human meshes |
| **Textures** | Textures | Used for human meshes |

## 2. Human Models (for prefabs)

Path: `Assets/Kevin Iglesias/Human Animations/Models/`

| File | Notes |
|---|---|
| **HumanM_Model.fbx** | Male humanoid model (rigged) |
| **HumanF_Model.fbx** | Female humanoid model (rigged) |
| **Avatar Masks/** | Upper-body/arm masks for layered animation |

**EnvironmentAgent mapping:**
Use `HumanM_Model` or `HumanF_Model` in a scene, add an Animator + Controller, then make a prefab and place it under `Resources/Prefabs/`.
Example prefab name: `HumanM_Idle01`. In code: `PlaceHuman("HumanM_Idle01", position, rotation)`.

## 3. Animator Controllers (.controller) to copy into Resources

Path: `Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/AnimatorControllers/`

### Male (HumanM@...)

| Controller name | Action |
|---|---|
| HumanM@Idles | Idle (multiple idle states) |
| HumanM@Walk01_Forward / _Backward / _Left / _Right / _ForwardLeft / _ForwardRight / _BackwardLeft / _BackwardRight | 8-direction walk |
| HumanM@Run01_Forward / _Backward / _Left / _Right / ... | 8-direction run |
| HumanM@Sprint01_Forward / _Left / _Right / ... | Sprint |
| HumanM@Turn01_Left [RM] / HumanM@Turn01_Right [RM] | In-place turn (root motion) |
| HumanM@Jump01 [RM] | Jump (root motion) |
| HumanM@Fall01 | Fall |
| HumanM@Talking | Talk |
| HumanM@MaskedWalking | Walk + upper-body actions (wave, question, etc.) |

### Female (HumanF@...)

| Controller name | Action |
|---|---|
| HumanF@Walk01_Forward / _ForwardLeft / _ForwardRight / _Left / _Right / ... | Multi-direction walk |
| HumanF@Jump01 [RM] | Jump (root motion) |
| Other actions follow the same pattern as the male controllers (F prefix) |

**EnvironmentAgent mapping:**
Copy the needed `.controller` files into `Assets/Resources/AnimatorControllers/`, then call:
`SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward")`

Paths are relative to `Resources` and should not include file extensions.

## 4. Animation Clips (.fbx)

Path: `Assets/Kevin Iglesias/Human Animations/Animations/`

### Male
- `Movement/Walk/` - HumanM@Walk01_Forward.fbx and others; `RootMotion/` contains [RM] variants
- `Movement/Run/` - run clips; `RootMotion/` contains [RM] variants
- `Movement/Sprint/` - sprint clips; `RootMotion/` contains [RM] variants
- `Movement/Turn/` - HumanM@Turn01_Left.fbx / Right; [RM] variants
- `Movement/Jump/` - HumanM@Jump01.fbx, Begin, Land, and [RM] versions

### Female
- Structure is similar: `Animations/Female/` with Idles, Movement/Walk, Run, Sprint, Turn, Jump, Social, etc.

These `.fbx` clips are referenced by the controllers listed above. Usually you only need to copy the controllers into `Resources`.

## 5. Scripts

Path: `Assets/Kevin Iglesias/Human Animations/Scripts/`

| File | Use |
|---|---|
| **SpineProxy.cs** | Attach to **B-spineProxy** on the humanoid rig for layered upper/lower body animation (e.g., MaskedWalking) |

**EnvironmentAgent mapping:**
If using controllers with separate upper-body actions (e.g., HumanM@MaskedWalking), find B-spineProxy, attach SpineProxy, and assign B-spine as Original Spine.

## 6. Summary mapping to EnvironmentAgent

1. **Human prefabs**
   - Source: `Models/HumanM_Model.fbx` or `HumanF_Model.fbx` (add Animator + Controller, then prefab)
   - Target: `Resources/Prefabs/` (e.g., `HumanM_Idle01.prefab`)
   - Code: `PlaceHuman("HumanM_Idle01", position, rotation)`

2. **Animator controllers**
   - Source: `Unity Demo Scenes/Human Basic Motions/AnimatorControllers/*.controller`
   - Target: `Resources/AnimatorControllers/`
   - Code: `SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward")`

3. **SpineProxy**
   - Source: `Scripts/SpineProxy.cs`
   - Use: Attach to B-spineProxy for layered upper/lower body animation
