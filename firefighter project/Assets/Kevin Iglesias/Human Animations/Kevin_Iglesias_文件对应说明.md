# Kevin Iglesias Human Animations — 文件夹与文件对应说明

本资源包在项目中的路径：`Assets/Kevin Iglesias/Human Animations/`。下面列出各子文件夹用途，以及和 EnvironmentAgent（Resources）的对应关系。

---

## 一、总览：各文件夹用途

| 文件夹 | 用途 | EnvironmentAgent 里怎么用 |
|--------|------|---------------------------|
| **Models** | 人形骨骼模型（FBX） | 用来做人形预制体 → 放到 `Resources/Prefabs/` |
| **Animations** | 动画片段（.fbx） | 已被 Controller 引用，一般不用手动拖 |
| **Unity Demo Scenes / AnimatorControllers** | 动画控制器（.controller） | 复制到 `Resources/AnimatorControllers/` 后，用 `SetHumanAnimationController(null, "AnimatorControllers/xxx")` |
| **Scripts** | SpineProxy 等脚本 | 给人形 B-spineProxy 挂 SpineProxy.cs（上下半身混合时用） |
| **Materials** | 材质 | 给人形贴图用 |
| **Textures** | 贴图 | 同上 |

---

## 二、人形模型（做预制体用）

路径：`Assets/Kevin Iglesias/Human Animations/Models/`

| 文件 | 说明 |
|------|------|
| **HumanM_Model.fbx** | 男性人形模型（骨骼+蒙皮） |
| **HumanF_Model.fbx** | 女性人形模型（骨骼+蒙皮） |
| **Avatar Masks/** | 上半身/手臂等遮罩，做分层动画时用 |

**对应到 EnvironmentAgent**：用 `HumanM_Model` 或 `HumanF_Model` 在场景里做人形，挂 Animator + 指定一个 Controller，再拖成预制体放到 `Resources/Prefabs/`，名字如 `HumanM_Idle01`，代码里用 `PlaceHuman("HumanM_Idle01", position, rotation)`。

---

## 三、动画控制器（.controller）— 要复制到 Resources 的

路径：`Assets/Kevin Iglesias/Human Animations/Unity Demo Scenes/Human Basic Motions/AnimatorControllers/`

### 男性（HumanM@...）

| .controller 文件名 | 动作 |
|-------------------|------|
| HumanM@Idles | 待机（多个 Idle 切换） |
| HumanM@Walk01_Forward / _Backward / _Left / _Right / _ForwardLeft / _ForwardRight / _BackwardLeft / _BackwardRight | 走路 8 方向 |
| HumanM@Run01_Forward / _Backward / _Left / _Right / ... | 跑步 8 方向 |
| HumanM@Sprint01_Forward / _Left / _Right / ... | 冲刺 |
| HumanM@Turn01_Left [RM] / HumanM@Turn01_Right [RM] | 原地转向（Root Motion） |
| HumanM@Jump01 [RM] | 跳跃（Root Motion） |
| HumanM@Fall01 | 倒地 |
| HumanM@Talking | 说话 |
| HumanM@MaskedWalking | 走路 + 上半身多种动作（挥手、疑问等） |

### 女性（HumanF@...）

| .controller 文件名 | 动作 |
|-------------------|------|
| HumanF@Walk01_Forward / _ForwardLeft / _ForwardRight / _Left / _Right / ... | 走路多方向 |
| HumanF@Jump01 [RM] | 跳跃（Root Motion） |
| （其他与 Male 类似，按 F 前缀） |

**对应到 EnvironmentAgent**：把需要的 .controller **复制**到 `Assets/Resources/AnimatorControllers/`，代码里用：  
`SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward")`  
（路径不要写扩展名，且相对 Resources 根目录。）

---

## 四、动画片段（.fbx）— 被 Controller 引用，通常不用单独拖

路径：`Assets/Kevin Iglesias/Human Animations/Animations/`

### Male（男性）

- **Idles/** — HumanM@Idle01.fbx, HumanM@Idle02.fbx, ...
- **Movement/Walk/** — HumanM@Walk01_Forward.fbx 等；子文件夹 **RootMotion/** 里是带 [RM] 的走路
- **Movement/Run/** — 跑步；**RootMotion/** 里是带 [RM] 的跑步
- **Movement/Sprint/** — 冲刺；**RootMotion/** 同上
- **Movement/Turn/** — HumanM@Turn01_Left.fbx / Right，带 [RM] 的转向
- **Movement/Jump/** — HumanM@Jump01.fbx、Begin、Land、以及 [RM] 版本
- **Social/Conversation/** — HumanM@Talk01.fbx

### Female（女性）

- 结构类似：`Animations/Female/` 下 Idles、Movement/Walk、Run、Sprint、Turn、Jump、Social 等。

这些 .fbx 已经被上面列出的 .controller 引用，你只要把 .controller 复制到 Resources 即可，无需单独复制 .fbx。

---

## 五、脚本

路径：`Assets/Kevin Iglesias/Human Animations/Scripts/`

| 文件 | 用途 |
|------|------|
| **SpineProxy.cs** | 挂在人形的 **B-spineProxy** 骨骼上，配合 Avatar Mask 做上下半身分层动画（如 MaskedWalking）时用 |

**对应到 EnvironmentAgent**：若使用带“上半身单独动作”的 Controller（如 HumanM@MaskedWalking），需在人形骨骼里找到 B-spineProxy，挂 SpineProxy，并把 B-spine 赋给 Original Spine。

---

## 六、和 EnvironmentAgent 的对应小结

1. **人形预制体**  
   - 来源：`Models/HumanM_Model.fbx` 或 `HumanF_Model.fbx`（在场景里加 Animator + Controller 后做成 Prefab）。  
   - 目标：放到 `Resources/Prefabs/`，例如 `HumanM_Idle01.prefab`。  
   - 代码：`PlaceHuman("HumanM_Idle01", position, rotation)`。

2. **动画控制器**  
   - 来源：`Unity Demo Scenes/Human Basic Motions/AnimatorControllers/*.controller`。  
   - 目标：复制到 `Resources/AnimatorControllers/`。  
   - 代码：`SetHumanAnimationController(null, "AnimatorControllers/HumanM@Walk01_Forward")` 等。

3. **SpineProxy**  
   - 来源：`Scripts/SpineProxy.cs`。  
   - 使用：挂到人形骨骼的 B-spineProxy 上（做上下半身混合时）。

按上表在 Kevin Iglesias 文件夹下找对应文件即可。
