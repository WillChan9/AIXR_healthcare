using UnityEngine;

/// <summary>
/// Agent control interface: object placement + environment parameters (light, fog, time of day, gravity, etc.).
/// External AI or game logic controls the environment by calling this script's public methods.
/// Humanoid animation control reference: Kevin Iglesias Human Animations (AnimatorController + SpineProxy upper/lower body blend).
/// </summary>
public class EnvironmentAgent : MonoBehaviour
{
    [Header("Optional: assign for Time-of-Day control")]
    [SerializeField] Light mainDirectionalLight;

    [Header("Optional: default human model for animation (if not passed in methods)")]
    [Tooltip("Leave empty to auto-find the first object with an Animator in the scene at Start (prefer names containing 'Human').")]
    [SerializeField] GameObject defaultHumanModel;

    [Header("ithappy Medical Pack: test prefab references (assign in Inspector)")]
    [SerializeField] GameObject ithappyFurniturePrefab;   // e.g. Medical_Pack/Prefabs/Furniture/Table_01
    [SerializeField] GameObject ithappyPropPrefab;        // e.g. Medical_Pack/Prefabs/Props/Syringe_02
    [SerializeField] GameObject ithappyCharacterPrefab;   // e.g. Medical_Pack/Characters/Prefabs/Doctor_01_Sneakers

    [Header("ithappy Medical Pack: character animation test")]
    [Tooltip("Assign AnimatorController such as Medical_Pack/Animations/Animation_Controllers/All_Animations.controller")]
    [SerializeField] RuntimeAnimatorController ithappyCharacterController;

    // Runtime: last spawned ithappy character instance, used for playing test animations
    GameObject ithappyCharacterInstance;

    void Start()
    {
        if (mainDirectionalLight == null)
            mainDirectionalLight = FindFirstObjectByType<Light>();

        // When no human is assigned: prefer an Animator whose name contains "Human" to avoid binding XR/Camera etc.
        if (defaultHumanModel == null)
        {
            Animator[] all = FindObjectsByType<Animator>(FindObjectsSortMode.None);
            Animator chosen = null;
            foreach (Animator a in all)
            {
                if (a.gameObject.name.IndexOf("Human", System.StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    chosen = a;
                    break;
                }
            }
            if (chosen == null && all.Length > 0)
                chosen = all[0];
            if (chosen != null)
            {
                defaultHumanModel = chosen.gameObject;
                Debug.Log($"EnvironmentAgent: auto-bound human = {defaultHumanModel.name}");
            }
        }
    }

    // ========== Object placement (same as Chair/Table; all loaded from Resources/Prefabs/) ==========
    /// <summary>Called by Agent: place a prefab at the given position. Prefab must be under Resources/Prefabs/.</summary>
    public void PlaceObject(string objectName, Vector3 position, float rotationY)
    {
        string path = "Prefabs/" + objectName;
        GameObject prefab = Resources.Load<GameObject>(path);

        if (prefab != null)
        {
            GameObject spawned = Instantiate(prefab, position, Quaternion.Euler(0, rotationY, 0));
            SnapToFloor(spawned);
            Debug.Log($"AI Agent placed: {objectName} at {position}");
        }
        else
        {
            Debug.LogError($"AI Agent Error: Prefab '{objectName}' not found in Resources/Prefabs/");
        }
    }

    /// <summary>
    /// Called by Agent: create by name. Prefer prefab in Resources/Prefabs/, else primitive, else empty GameObject.
    /// scaleX/Y/Z: if <= 0, keep default 1.
    /// </summary>
    public void CreateObjectByName(string objectName, Vector3 position, float rotationY, float scaleX, float scaleY, float scaleZ)
    {
        GameObject spawned = null;
        string path = "Prefabs/" + objectName;
        GameObject prefab = Resources.Load<GameObject>(path);

        if (prefab != null)
        {
            spawned = Instantiate(prefab, position, Quaternion.Euler(0, rotationY, 0));
            Debug.Log($"AI Agent created prefab: {objectName} at {position}");
        }
        else
        {
            if (TryCreatePrimitive(objectName, out spawned))
            {
                spawned.transform.position = position;
                spawned.transform.rotation = Quaternion.Euler(0, rotationY, 0);
                Debug.Log($"AI Agent created primitive: {objectName} at {position}");
            }
            else
            {
                spawned = new GameObject(objectName);
                spawned.transform.position = position;
                spawned.transform.rotation = Quaternion.Euler(0, rotationY, 0);
                Debug.Log($"AI Agent created empty object: {objectName} at {position}");
            }
        }

        if (spawned != null)
        {
            var sx = scaleX > 0 ? scaleX : 1f;
            var sy = scaleY > 0 ? scaleY : 1f;
            var sz = scaleZ > 0 ? scaleZ : 1f;
            spawned.transform.localScale = new Vector3(sx, sy, sz);
            SnapToFloor(spawned);
        }
    }

    /// <summary>Called by Agent: place a human prefab from Resources/Prefabs/ (same usage as Chair/Table). After placement it becomes the "current human"; subsequent PlayHumanAnimation(null, ...) etc. will target it. humanName is the prefab name, e.g. "HumanM_Idle01".</summary>
    public void PlaceHuman(string humanName, Vector3 position, float rotationY)
    {
        string path = "Prefabs/" + humanName;
        GameObject prefab = Resources.Load<GameObject>(path);

        if (prefab != null)
        {
            GameObject spawned = Instantiate(prefab, position, Quaternion.Euler(0, rotationY, 0));
            SnapToFloor(spawned);
            defaultHumanModel = spawned;
            Debug.Log($"AI Agent placed human: {humanName} at {position} (set as default human)");
        }
        else
        {
            Debug.LogError($"AI Agent Error: Human prefab '{humanName}' not found in Resources/Prefabs/. Place human prefabs under Assets/Resources/Prefabs/.");
        }
    }

    // ========== Environment parameter control (for Agent calls) ==========

    /// <summary>Set main directional light intensity; recommended range 0~2.</summary>
    public void SetLightIntensity(float intensity)
    {
        if (mainDirectionalLight == null) return;
        mainDirectionalLight.intensity = Mathf.Clamp(intensity, 0f, 2f);
        Debug.Log($"Agent: Light intensity = {mainDirectionalLight.intensity}");
    }

    /// <summary>Set main light color (R,G,B 0~1).</summary>
    public void SetLightColor(float r, float g, float b)
    {
        if (mainDirectionalLight == null) return;
        mainDirectionalLight.color = new Color(
            Mathf.Clamp01(r), Mathf.Clamp01(g), Mathf.Clamp01(b));
    }

    /// <summary>Set “time of day”(0~24); rotates main directional light to simulate sun elevation.</summary>
    public void SetTimeOfDay(float hour)
    {
        if (mainDirectionalLight == null) return;
        float t = Mathf.Repeat(hour / 24f, 1f);
        float angle = Mathf.Lerp(-90f, 270f, t);
        mainDirectionalLight.transform.rotation = Quaternion.Euler(angle, 30f, 0f);
        Debug.Log($"Agent: Time of day = {hour:F1}h");
    }

    /// <summary>Enable or disable fog.</summary>
    public void SetFogEnabled(bool enabled)
    {
        RenderSettings.fog = enabled;
    }

    /// <summary>Set fog density; recommended range 0~0.1.</summary>
    public void SetFogDensity(float density)
    {
        RenderSettings.fogDensity = Mathf.Clamp(density, 0f, 0.2f);
    }

    /// <summary>Set fog color (R,G,B 0~1).</summary>
    public void SetFogColor(float r, float g, float b)
    {
        RenderSettings.fogColor = new Color(
            Mathf.Clamp01(r), Mathf.Clamp01(g), Mathf.Clamp01(b));
    }

    /// <summary>Set ambient light color (R,G,B 0~1).</summary>
    public void SetAmbientColor(float r, float g, float b)
    {
        RenderSettings.ambientLight = new Color(
            Mathf.Clamp01(r), Mathf.Clamp01(g), Mathf.Clamp01(b));
    }

    /// <summary>Set gravity Y component (default -9.81).</summary>
    public void SetGravity(float gravityY)
    {
        Physics.gravity = new Vector3(0, gravityY, 0);
    }

    /// <summary>Set time scale; 0=paused, 1=normal.</summary>
    public void SetTimeScale(float scale)
    {
        Time.timeScale = Mathf.Clamp(scale, 0f, 2f);
    }

    // ========== Humanoid animation control (reference: Kevin Iglesias Human Animations) ==========
    // Usage: human must have an Animator; for upper/lower body blend, attach SpineProxy on B-spineProxy.

    /// <summary>Get the human GameObject used for animation (prefer the passed-in one, else Inspector-assigned defaultHumanModel).</summary>
    static GameObject GetHumanTarget(GameObject human, GameObject defaultHuman)
    {
        if (human != null) return human;
        return defaultHuman;
    }

    /// <summary>Find Animator on the human itself, its children, or parent (Kevin Iglesias human sometimes has Animator on child or parent).</summary>
    static Animator GetHumanAnimator(GameObject target)
    {
        if (target == null) return null;
        Animator a = target.GetComponent<Animator>();
        if (a != null) return a;
        a = target.GetComponentInChildren<Animator>(true);
        if (a != null) return a;
        return target.GetComponentInParent<Animator>();
    }
    /// <summary>Called by Agent: switch the human's Animator Controller. controllerPath is path under Resources, e.g. "AnimatorControllers/HumanM@Walk01_Forward" (place Kevin Iglesias .controller under Resources/AnimatorControllers/).</summary>
    public void SetHumanAnimationController(GameObject human, string controllerPathInResources)
    {
        GameObject target = GetHumanTarget(human, defaultHumanModel);
        if (target == null) { Debug.LogWarning("EnvironmentAgent: no human model assigned."); return; }

        Animator anim = GetHumanAnimator(target);
        if (anim == null) { Debug.LogWarning("EnvironmentAgent: human model has no Animator."); return; }

        RuntimeAnimatorController ctrl = Resources.Load<RuntimeAnimatorController>(controllerPathInResources);
        if (ctrl == null) { Debug.LogError($"EnvironmentAgent: AnimatorController not found at Resources/{controllerPathInResources}"); return; }

        anim.runtimeAnimatorController = ctrl;
        Debug.Log($"Agent: human AnimatorController set to {controllerPathInResources}");
    }

    /// <summary>Called by Agent: play the given animation state (state name inside current Controller), e.g. "HumanM@Walk01_Forward". crossFadeTime is blend duration.</summary>
    public void PlayHumanAnimation(GameObject human, string stateName, float crossFadeTime = 0.25f)
    {
        GameObject target = GetHumanTarget(human, defaultHumanModel);
        if (target == null) { Debug.LogWarning("EnvironmentAgent: no human model assigned."); return; }

        Animator anim = GetHumanAnimator(target);
        if (anim == null) { Debug.LogWarning("EnvironmentAgent: human model has no Animator."); return; }

        if (anim.runtimeAnimatorController == null)
        {
            Debug.LogWarning("EnvironmentAgent: Animator does not have an AnimatorController. Call SetHumanAnimationController(...) or use PlayHumanAnimationFromResources(...) first.");
            return;
        }

        anim.CrossFade(stateName, crossFadeTime);
        Debug.Log($"Agent: human playing state '{stateName}'");
    }

    /// <summary>
    /// Called by Agent: load/ensure AnimatorController from Resources then play the given state.
    /// Use this to avoid "Animator does not have an AnimatorController" errors.
    /// controllerPathInResources e.g. "AnimatorControllers/HumanM@Walk01_Forward"
    /// stateName e.g. "HumanM@Walk01_Forward"
    /// </summary>
    public void PlayHumanAnimationFromResources(GameObject human, string controllerPathInResources, string stateName, float crossFadeTime = 0.25f)
    {
        GameObject target = GetHumanTarget(human, defaultHumanModel);
        if (target == null) { Debug.LogWarning("EnvironmentAgent: no human model assigned."); return; }

        Animator anim = GetHumanAnimator(target);
        if (anim == null) { Debug.LogWarning("EnvironmentAgent: human model has no Animator."); return; }

        if (anim.runtimeAnimatorController == null)
        {
            RuntimeAnimatorController ctrl = Resources.Load<RuntimeAnimatorController>(controllerPathInResources);
            if (ctrl == null)
            {
                Debug.LogError($"EnvironmentAgent: AnimatorController not found at Resources/{controllerPathInResources}");
                return;
            }
            anim.runtimeAnimatorController = ctrl;
        }

        anim.CrossFade(stateName, crossFadeTime);
        Debug.Log($"Agent: human playing state '{stateName}' (controller ensured: {controllerPathInResources})");
    }

    /// <summary>Set an Animator Float parameter (e.g. Speed).</summary>
    public void SetHumanAnimatorFloat(GameObject human, string paramName, float value)
    {
        GameObject target = GetHumanTarget(human, defaultHumanModel);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetFloat(paramName, value);
    }

    /// <summary>Set an Animator Bool parameter.</summary>
    public void SetHumanAnimatorBool(GameObject human, string paramName, bool value)
    {
        GameObject target = GetHumanTarget(human, defaultHumanModel);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetBool(paramName, value);
    }

    /// <summary>Trigger an Animator Trigger parameter.</summary>
    public void SetHumanAnimatorTrigger(GameObject human, string paramName)
    {
        GameObject target = GetHumanTarget(human, defaultHumanModel);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetTrigger(paramName);
    }

    private void SnapToFloor(GameObject obj)
    {
        RaycastHit hit;
        if (Physics.Raycast(obj.transform.position + Vector3.up, Vector3.down, out hit, 10f))
        {
            obj.transform.position = hit.point;
        }
    }

    private bool TryCreatePrimitive(string objectName, out GameObject spawned)
    {
        spawned = null;
        if (string.IsNullOrEmpty(objectName)) return false;
        string name = objectName.ToLowerInvariant();

        if (name == "cube" || name == "box") spawned = GameObject.CreatePrimitive(PrimitiveType.Cube);
        else if (name == "sphere" || name == "ball") spawned = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        else if (name == "capsule") spawned = GameObject.CreatePrimitive(PrimitiveType.Capsule);
        else if (name == "cylinder") spawned = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
        else if (name == "plane") spawned = GameObject.CreatePrimitive(PrimitiveType.Plane);
        else if (name == "quad") spawned = GameObject.CreatePrimitive(PrimitiveType.Quad);

        return spawned != null;
    }

    // --- Inspector test buttons (ithappy Medical Pack) ---
    [ContextMenu("ithappy: Spawn Furniture (test)")]
    void TestSpawnIthappyFurniture()
    {
        if (ithappyFurniturePrefab == null)
        {
            Debug.LogWarning("EnvironmentAgent: ithappyFurniturePrefab not assigned in Inspector. Assign a prefab from Medical_Pack/Prefabs/Furniture.");
            return;
        }
        GameObject spawned = Instantiate(ithappyFurniturePrefab, new Vector3(0, 0, 2), Quaternion.identity);
        SnapToFloor(spawned);
        Debug.Log($"EnvironmentAgent: Spawn ithappy furniture '{ithappyFurniturePrefab.name}'");
    }

    [ContextMenu("ithappy: Spawn Prop (test)")]
    void TestSpawnIthappyProp()
    {
        if (ithappyPropPrefab == null)
        {
            Debug.LogWarning("EnvironmentAgent: ithappyPropPrefab not assigned in Inspector. Assign a prefab from Medical_Pack/Prefabs/Props.");
            return;
        }
        GameObject spawned = Instantiate(ithappyPropPrefab, new Vector3(1.5f, 0, 2), Quaternion.identity);
        SnapToFloor(spawned);
        Debug.Log($"EnvironmentAgent: Spawn ithappy prop '{ithappyPropPrefab.name}'");
    }

    [ContextMenu("ithappy: Spawn Character (test)")]
    void TestSpawnIthappyCharacter()
    {
        if (ithappyCharacterPrefab == null)
        {
            Debug.LogWarning("EnvironmentAgent: ithappyCharacterPrefab not assigned in Inspector. Assign a prefab from Medical_Pack/Characters/Prefabs.");
            return;
        }
        ithappyCharacterInstance = Instantiate(ithappyCharacterPrefab, new Vector3(-1.5f, 0, 2), Quaternion.identity);
        SnapToFloor(ithappyCharacterInstance);
        Debug.Log($"EnvironmentAgent: Spawn ithappy character '{ithappyCharacterPrefab.name}' (set as ithappyCharacterInstance)");
    }

    // --- ithappy character animation test ---

    void PlayIthappyAnimation(string stateName, float crossFadeTime = 0.25f)
    {
        if (ithappyCharacterInstance == null)
        {
            Debug.LogWarning("EnvironmentAgent: ithappyCharacterInstance is null. Run context menu 'ithappy: Spawn Character (test)' first to spawn the character.");
            return;
        }

        Animator anim = GetHumanAnimator(ithappyCharacterInstance);
        if (anim == null)
        {
            Debug.LogWarning("EnvironmentAgent: no Animator component found on ithappy character.");
            return;
        }

        if (anim.runtimeAnimatorController == null)
        {
            if (ithappyCharacterController == null)
            {
                Debug.LogError("EnvironmentAgent: ithappyCharacterController not assigned in Inspector. Assign Medical_Pack/Animations/Animation_Controllers/All_Animations.controller.");
                return;
            }
            anim.runtimeAnimatorController = ithappyCharacterController;
        }

        anim.CrossFade(stateName, crossFadeTime);
        Debug.Log($"EnvironmentAgent: ithappy character playing state '{stateName}'");
    }

    [ContextMenu("ithappy Anim: Idle (Skeleton_Idle_Breathing_002)")]
    void TestIthappyIdle() => PlayIthappyAnimation("Skeleton_Idle_Breathing_002", 0.2f);

    [ContextMenu("ithappy Anim: Walk Forward (Skeleton_Walk_Forward_002)")]
    void TestIthappyWalk() => PlayIthappyAnimation("Skeleton_Walk_Forward_002", 0.2f);

    [ContextMenu("ithappy Anim: Run Forward (Skeleton_Run_Forward_002)")]
    void TestIthappyRun() => PlayIthappyAnimation("Skeleton_Run_Forward_002", 0.15f);

    [ContextMenu("ithappy Anim: Attack Punch (Skeleton_Attack_Punch_002)")]
    void TestIthappyAttack() => PlayIthappyAnimation("Skeleton_Attack_Punch_002", 0.1f);
}
