# EnvironmentAgent 从 Resources 加载资源 — 操作说明

和 Chair、Table 一样，人形和动画控制器都从 **Resources** 里按路径加载。按下面步骤把文件放到正确位置即可。

---

## 一、目录结构（你要维护的）

```
Assets/
  Resources/
    Prefabs/           ← 放所有“可放置”的预制体（椅子、桌子、人形）
      Chair1.prefab
      Table1.prefab
      HumanM_Idle01.prefab   ← 人形预制体（自己建，见下）
    AnimatorControllers/     ← 放人形用的 Animator Controller（.controller）
      HumanM@Walk01_Forward.controller
      HumanM@Idle01.controller
      ...
```

- **Prefabs**：和现在 Chair/Table 一样，人形也放这里，代码里用 `PlaceObject("Chair1", ...)` / `PlaceHuman("HumanM_Idle01", ...)` 时，会从 `Resources/Prefabs/` 下按名字加载。
- **AnimatorControllers**：给人形换动作时用，代码里用 `SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward")` 时，会从 `Resources/AnimatorControllers/` 加载。

---

## 二、人形预制体：如何拖放、放到哪

### 1. 在 Project 里建好 Resources 下的文件夹（没有的话）

- 在 **Project** 里点选 **Assets/Resources**。
- 右键 → **Create** → **Folder**，建一个叫 **Prefabs**（若已有就用现有的）。
- 再在 **Resources** 下建一个 **AnimatorControllers**（若已有就用现有的）。

### 2. 做人形预制体并拖进 Resources/Prefabs

任选一种方式：

**方式 A：用 Kevin Iglesias 的 FBX 做人形预制体**

1. 在 **Project** 里找到  
   `Assets/Kevin Iglesias/Human Animations/Models/HumanM_Model.fbx`（或 HumanF_Model.fbx）。
2. 把该 **FBX** 拖到 **Hierarchy**（生成一个带骨骼的模型）。
3. 选中刚生成的根物体，在 **Inspector** 里 **Add Component** → 搜索 **Animator**，加上。
4. 在 **Animator** 组件的 **Controller** 槽里，拖入一个 .controller（可从  
   `Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/AnimatorControllers/` 里选，例如 `HumanM@Walk01_Forward`）。
5. 在 **Project** 里打开 **Assets/Resources/Prefabs**，把 **Hierarchy** 里这个人形根物体**拖进 Prefabs 文件夹**，生成预制体（名字可改为如 `HumanM_Idle01`）。
6. 删除 Hierarchy 里刚才用来做预制体的那个物体（可选）。

**方式 B：用场景里已有的人形做人形预制体**

1. 在 **Hierarchy** 里选中你已经在用的人形（例如 `HumanM@Idle01`），确保它带 **Animator** 且已指定 Controller。
2. 在 **Project** 里打开 **Assets/Resources/Prefabs**。
3. 把 **Hierarchy** 里这个人形**拖进 Prefabs 文件夹**，生成预制体。
4. 把生成的预制体改名为简单英文（如 `HumanM_Idle01`），代码里用这个名字：  
   `PlaceHuman("HumanM_Idle01", position, rotation)`。

这样人形就和 Chair/Table 一样，**从 Resources 调用**：只认 **Resources/Prefabs/** 下的预制体名字，不需要在 Inspector 里拖 Default Human Model（用 `PlaceHuman` 放置后会自动设为当前人形）。

---

## 三、Animator Controller：如何拖放到 Resources

1. 在 **Project** 里找到 Kevin Iglesias 的 Controller，例如：  
   `Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/AnimatorControllers/`。
2. 选中你要用的 .controller 文件（如 **HumanM@Walk01_Forward**、**HumanM@Idle01** 等）。
3. **复制**（Ctrl+C）或 **复制** 后 **粘贴**（Ctrl+V）到 **Assets/Resources/AnimatorControllers/** 文件夹里。  
   （若没有 **AnimatorControllers** 文件夹，在 **Resources** 下新建一个，再把 .controller 拖进去。）
4. 代码里用 **Resources 下的路径**（不含扩展名）：  
   `SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward")`。

注意：**必须**在 **Resources** 下的 **AnimatorControllers** 里有一份，代码才能 `Resources.Load` 到。

---

## 四、代码里怎么用（和 Chair/Table 一致）

- **放置人形**（从 Resources 调预制体）：  
  `PlaceHuman("HumanM_Idle01", new Vector3(0, 0, 3), 0f);`  
  名字要和 **Resources/Prefabs/** 下预制体名字一致。
- **换动作**（从 Resources 调 Controller）：  
  `SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward");`  
  路径是相对 **Resources** 的，例如 `AnimatorControllers/xxx`。
- **Default Human Model**：若用了 `PlaceHuman`，放置后会自动设为当前人形，传 `null` 的动画接口都会作用在它身上；若场景里是手动放的人形，再在 Inspector 里拖一次 Default Human Model 即可。

总结：**人形 = Prefabs 里预制体名；动画 = AnimatorControllers 里 .controller 的 Resources 路径**。按上面拖放好后，用法就和 Chair、Table 一致，全部从 Resources 调用。
