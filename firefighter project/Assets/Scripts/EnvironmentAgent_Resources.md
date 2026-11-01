# EnvironmentAgent: Resources Setup

`EnvironmentAgent` loads every prefab and animator controller it spawns from Unity
**Resources** folders, so the agent can refer to assets by name alone. This page explains
how assets are looked up and how to add new characters and animations.

## How assets are looked up

| Asset | Used by | Lookup |
|---|---|---|
| Prefabs | `PlaceObject`, `PlaceHuman`, `AttachPlayerAvatar` | By file name, case-insensitive, among **all** prefabs in any `Resources` folder (subfolders included). `AttachPlayerAvatar` also accepts a full Resources path. |
| Prefabs | `CreateObjectByName` | Only `Resources/Prefabs/<objectName>`; otherwise falls back to a primitive shape. |
| Animator controllers | `PlayHumanAnimationFromResources`, `SetHumanAnimationController`, `WalkCharacterTo` | By Resources path (e.g. `AnimatorControllers/HumanM@Idles`) first, then by controller name alone, case-insensitive. |

Names must be unique: if two prefabs (or two controllers) share a name, only the first one
found is used. The index is built the first time an asset is requested.

## Folder layout

```
Assets/Resources/
  Prefabs/                      General prefabs (e.g. Chair1, Table1) and the
                                Kevin Iglesias dummies used for the player avatar
  AnimatorControllers/          Kevin Iglesias controllers (HumanM@*, HumanF@*)
  Medical_Pack/
    Prefabs/                    ithappy medical furniture, equipment and props (e.g. Bed_01)
    Characters/
      Ready_Characters/         Complete characters (e.g. Doctor_01, Nurse_02, Patient_01)
      Prefabs/                  Separate character parts (e.g. Doctor_01_Body, Doctor_01_Coat)
```

`PlaceHuman` should be given a complete character name such as `Doctor_01`. When
`PlaceObject` receives a name that starts with a known role (`Doctor_`, `Nurse_`, `EMT_`,
`Patient_`, `Surgeon_`, `infectiologist_`), it strips any part suffix (`_Body`, `_Coat`,
`_Hat`, ...) and places the complete character instead.

## Adding a character

Characters need an **Animator** with a humanoid rig. When placed, `EnvironmentAgent`:

- snaps the character to the floor,
- removes extra Animators from clothing parts and rebinds their meshes to the main skeleton,
- applies `AnimatorControllers/HumanM@Idles` so it starts in an idle pose,
- adds a `CapsuleCollider` if there is none, so touch detection can find it.

To add a new character:

1. Drag the model or prefab into the Hierarchy and confirm it has an **Animator** with a
   humanoid **Avatar**.
2. Drag the root object into a folder under `Assets/Resources/` to create a prefab.
3. Give the prefab a short, unique name (e.g. `Doctor_07`) and use that name in commands.

## Adding an animator controller

1. Find the controller you need, e.g. in
   `Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/AnimatorControllers/`.
2. Copy the `.controller` file into `Assets/Resources/AnimatorControllers/`.
3. Refer to it by its Resources path without the extension:
   `AnimatorControllers/HumanM@Walk01_Forward`.
4. Use the name of the state to play as `stateName`. For the Kevin Iglesias controllers
   this is the same as the controller name (e.g. `HumanM@Walk01_Forward`).

Example command:

```json
{
  "action": "PlayHumanAnimationFromResources",
  "targetModel": "Doctor_01",
  "controllerPath": "AnimatorControllers/HumanM@Talking",
  "stateName": "HumanM@Talking",
  "crossFadeTime": 0.3
}
```

## Default animation target

Commands without a `targetModel` (and the `SetHumanAnimator*` and
`SetHumanAnimationController` actions, which have no target field) animate the
**default human**:

- Each `PlaceHuman` makes the newly placed character the default human.
- Otherwise, the `Default Human Model` field on `EnvironmentAgent` is used if assigned.
- If that is empty too, the best humanoid Animator in the scene is chosen automatically.
