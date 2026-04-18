using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

/// <summary>
/// Agent control interface: object placement + environment parameters (light, fog, time of day, gravity, etc.).
/// External AI or game logic controls the environment by calling this script's public methods.
/// Humanoid animation control reference: Kevin Iglesias Human Animations (AnimatorController + SpineProxy upper/lower body blend).
/// </summary>
[RequireComponent(typeof(InteractionSetup))]
[RequireComponent(typeof(InteractionAnimationController))]
[RequireComponent(typeof(InteractionEventReporter))]
public class EnvironmentAgent : MonoBehaviour
{
    // ── Scene-state tracking ──────────────────────────────────────────────────
    // Maps prefab name → spawned GameObject so WriteSceneState() can query live transforms/bounds.
    private readonly Dictionary<string, GameObject> _trackedObjects = new Dictionary<string, GameObject>();
    // Maps character name → spawned character GameObject for multi-character support.
    private readonly Dictionary<string, GameObject> _namedCharacters = new Dictionary<string, GameObject>();
    [Header("Optional: assign for Time-of-Day control")]
    [SerializeField] Light mainDirectionalLight;

    [Header("Optional: auto-add XR interaction components to spawned objects")]
    [SerializeField] InteractionSetup interactionSetup;

    [Header("Optional: default human model for animation (if not passed in methods)")]
    [Tooltip("Leave empty to auto-find the first object with an Animator in the scene at Start (prefer names containing 'Human').")]
    [SerializeField] GameObject defaultHumanModel;

    [Header("Player avatar (optional)")]
    [Tooltip("Attach PlayerAvatarController here to enable player body animations via targetModel='player'.")]
    [SerializeField] PlayerAvatarController playerAvatarController;

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

        if (interactionSetup == null)
            interactionSetup = GetComponent<InteractionSetup>();
        if (interactionSetup == null)
            interactionSetup = FindFirstObjectByType<InteractionSetup>();

        // Ensure InteractionEventReporter is present — required for Phase 2 touch logging
        if (GetComponent<InteractionEventReporter>() == null)
        {
            gameObject.AddComponent<InteractionEventReporter>();
            Debug.Log("[EnvironmentAgent] Auto-added InteractionEventReporter.");
        }

        AutoBindDefaultHumanIfNeeded();

        // Auto-add PlayerAvatarController so the player body is always present without Inspector setup
        if (playerAvatarController == null)
        {
            playerAvatarController = GetComponent<PlayerAvatarController>()
                                  ?? gameObject.AddComponent<PlayerAvatarController>();
            Debug.Log("[EnvironmentAgent] Auto-added PlayerAvatarController.");
        }
    }

    // ========== Object placement (same as Chair/Table; all loaded from Resources/Prefabs/) ==========
    /// <summary>Called by Agent: place a prefab at the given position. Prefab must be under Resources/Prefabs/.</summary>
    public void PlaceObject(string objectName, Vector3 position, float rotationY)
    {
        GameObject prefab = LoadPrefabByName(objectName);

        if (prefab != null)
        {
            GameObject spawned = Instantiate(prefab, position, Quaternion.Euler(0, rotationY, 0));
            SnapToFloor(spawned);
            _trackedObjects[objectName] = spawned;
            WriteSceneState();
            if (interactionSetup != null) interactionSetup.MakeInteractable(spawned);
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
                Debug.LogWarning($"AI Agent CreateObjectByName: '{objectName}' is not a known prefab or primitive — skipping.");
                return;
            }
        }

        if (spawned != null)
        {
            var sx = scaleX > 0 ? scaleX : 1f;
            var sy = scaleY > 0 ? scaleY : 1f;
            var sz = scaleZ > 0 ? scaleZ : 1f;
            spawned.transform.localScale = new Vector3(sx, sy, sz);
            // Skip floor-snapping for structural elements (walls, ceilings).
            // A non-zero y or a tall scale means the caller set an intentional position.
            bool isStructural = position.y > 0.05f || sy > 1.5f;
            if (!isStructural)
                SnapToFloor(spawned);
            _trackedObjects[objectName] = spawned;
            WriteSceneState();
            if (interactionSetup != null) interactionSetup.MakeInteractable(spawned);
        }
    }

    /// <summary>Called by Agent: place a human prefab from Resources/Prefabs/ (same usage as Chair/Table). After placement it becomes the "current human"; subsequent PlayHumanAnimation(null, ...) etc. will target it. humanName is the prefab name, e.g. "HumanM_Idle01".</summary>
    public void PlaceHuman(string humanName, Vector3 position, float rotationY)
    {
        GameObject prefab = LoadHumanPrefabByName(humanName);

        if (prefab != null)
        {
            GameObject spawned = Instantiate(prefab, position, Quaternion.Euler(0, rotationY, 0));
            SnapToFloor(spawned);
            defaultHumanModel = spawned;
            // Ensure only the main Animator drives the humanoid rig (remove extra Animators on clothing, etc.)
            Animator mainAnim = FindPrimaryAnimator(spawned);
            if (mainAnim != null)
            {
                HardCleanupHumanRig(spawned, mainAnim);
                // Force idle pose: load the Idle controller so the character stands still
                // instead of playing whatever animation the prefab ships with.
                RuntimeAnimatorController idleCtrl = LoadAnimatorControllerByName("AnimatorControllers/HumanM@Idles");
                if (idleCtrl != null)
                {
                    mainAnim.runtimeAnimatorController = idleCtrl;
                    mainAnim.Update(0f);
                    mainAnim.CrossFade("HumanM@Idles", 0f);
                }
                else
                {
                    // No idle controller available — disable the animator to prevent random animations
                    mainAnim.runtimeAnimatorController = null;
                }
            }
            // Ensure a collider exists so Physics.OverlapSphere (PlayerTouchHighlighter) can detect the character.
            if (spawned.GetComponentInChildren<Collider>() == null)
            {
                CapsuleCollider cap = spawned.AddComponent<CapsuleCollider>();
                cap.height = 1.8f;
                cap.radius = 0.3f;
                cap.center = new Vector3(0f, 0.9f, 0f);
                cap.isTrigger = false;
                Debug.Log($"[EnvironmentAgent] Added CapsuleCollider to '{humanName}' for touch detection.");
            }

            _trackedObjects[humanName] = spawned;
            _namedCharacters[humanName] = spawned;
            WriteSceneState();
            Debug.Log($"AI Agent placed human: {humanName} at {position} (set as default human, idle pose)");
        }
        else
        {
            Debug.LogError($"AI Agent Error: Human prefab '{humanName}' not found in Resources. Place human prefabs under Assets/Resources/...");
        }
    }

    /// <summary>Called by Agent: find a previously spawned object by name and destroy it.</summary>
    public void DeleteObject(string objectName)
    {
        GameObject target = FindSpawnedObject(objectName);
        if (target != null)
        {
            Debug.Log($"AI Agent deleted: {target.name}");
            _trackedObjects.Remove(objectName);
            Destroy(target);
            WriteSceneState();
        }
        else
        {
            Debug.LogWarning($"AI Agent DeleteObject: '{objectName}' not found in scene.");
        }
    }

    /// <summary>Called by Agent: find a previously spawned object by name and move/rotate it.</summary>
    public void MoveObject(string objectName, Vector3 position, float rotationY)
    {
        GameObject target = FindSpawnedObject(objectName);
        if (target != null)
        {
            // Check if object has a Rigidbody for physics-based movement
            Rigidbody rb = target.GetComponent<Rigidbody>();
            Quaternion newRotation = Quaternion.Euler(0, rotationY, 0);
            
            if (rb != null && !rb.isKinematic)
            {
                // Use physics-based movement for dynamic rigidbodies
                rb.MovePosition(position);
                rb.MoveRotation(newRotation);
                Debug.Log($"AI Agent moved (physics): {objectName} to {position}");
            }
            else
            {
                // Use transform directly for kinematic/static objects
                target.transform.position = position;
                target.transform.rotation = newRotation;
                Debug.Log($"AI Agent moved (transform): {objectName} to {position}");
            }
            WriteSceneState();
        }
        else
        {
            Debug.LogWarning($"AI Agent MoveObject: '{objectName}' not found in scene.");
        }
    }

    // ── Scene state export ────────────────────────────────────────────────────
    /// <summary>
    /// Writes scene_state.json next to unity_commands.json.
    /// Each entry has: objectName, x/y/z (world position), rotationY, and renderer bounds.
    /// The Python agent reads this file instead of reconstructing state from the command log.
    /// </summary>
    private void WriteSceneState()
    {
        // Write to Agent/api_files/ so the Python agent can read it directly
        string repoRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        string dir = Path.Combine(repoRoot, "Agent", "api_files");
        string path = Path.Combine(dir, "scene_state.json");

        var sb = new StringBuilder();
        sb.AppendLine("[");
        bool first = true;

        foreach (var kv in _trackedObjects)
        {
            string prefabName = kv.Key;
            GameObject go = kv.Value;
            if (go == null) continue;  // destroyed but not yet removed

            Vector3 pos = go.transform.position;
            float rotY = go.transform.eulerAngles.y;

            // Compute world-space bounds by encapsulating all renderers
            Bounds bounds = new Bounds(pos, Vector3.zero);
            bool hasBounds = false;
            foreach (var r in go.GetComponentsInChildren<Renderer>(true))
            {
                if (!hasBounds) { bounds = r.bounds; hasBounds = true; }
                else bounds.Encapsulate(r.bounds);
            }

            if (!first) sb.AppendLine(",");
            first = false;

            sb.AppendLine("  {");
            sb.AppendLine($"    \"objectName\": \"{EscapeJson(prefabName)}\",");
            sb.AppendLine($"    \"x\": {pos.x.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
            sb.AppendLine($"    \"y\": {pos.y.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
            sb.AppendLine($"    \"z\": {pos.z.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
            sb.AppendLine($"    \"rotationY\": {rotY.ToString("F1", System.Globalization.CultureInfo.InvariantCulture)},");
            if (hasBounds)
            {
                sb.AppendLine($"    \"boundsMinX\": {bounds.min.x.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.AppendLine($"    \"boundsMinY\": {bounds.min.y.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.AppendLine($"    \"boundsMinZ\": {bounds.min.z.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.AppendLine($"    \"boundsMaxX\": {bounds.max.x.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.AppendLine($"    \"boundsMaxY\": {bounds.max.y.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.AppendLine($"    \"boundsMaxZ\": {bounds.max.z.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.AppendLine($"    \"boundsSizeX\": {bounds.size.x.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.AppendLine($"    \"boundsSizeY\": {bounds.size.y.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)},");
                sb.Append    ($"    \"boundsSizeZ\": {bounds.size.z.ToString("F3", System.Globalization.CultureInfo.InvariantCulture)}");
            }
            else
            {
                // No renderers — emit zero bounds so Python can still read the record
                sb.Append    ("    \"boundsMinX\": 0, \"boundsMinY\": 0, \"boundsMinZ\": 0,\n" +
                              "    \"boundsMaxX\": 0, \"boundsMaxY\": 0, \"boundsMaxZ\": 0,\n" +
                              "    \"boundsSizeX\": 0, \"boundsSizeY\": 0, \"boundsSizeZ\": 0");
            }
            sb.AppendLine();
            sb.Append("  }");
        }

        sb.AppendLine();
        sb.Append("]");

        try
        {
            Directory.CreateDirectory(dir);
            File.WriteAllText(path, sb.ToString(), Encoding.UTF8);
        }
        catch (System.Exception ex)
        {
            Debug.LogWarning($"[EnvironmentAgent] WriteSceneState failed: {ex.Message}");
        }
    }

    private static string EscapeJson(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    private static GameObject FindSpawnedObject(string objectName)
    {
        if (string.IsNullOrEmpty(objectName)) return null;

        // Exact match
        GameObject found = GameObject.Find(objectName);
        if (found != null) return found;

        // Unity appends "(Clone)" on Instantiate
        found = GameObject.Find(objectName + "(Clone)");
        if (found != null) return found;

        // Prefix search across all active objects
        string lower = objectName.ToLowerInvariant();
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go.name.ToLowerInvariant().StartsWith(lower))
                return go;
        }
        return null;
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

    // ========== Phase control ==========

    public void SetPhase(int phase)
    {
        Debug.Log($"[EnvironmentAgent] SetPhase({phase})");
        if (phase >= 2)
            ActivatePhase2();
    }

    private void ActivatePhase2()
    {
        // 0. Ensure PlayerAvatarController exists on AI_Manager
        if (playerAvatarController == null)
            playerAvatarController = gameObject.GetComponent<PlayerAvatarController>()
                                  ?? gameObject.AddComponent<PlayerAvatarController>();

        // 1. Tag the XR Origin / Camera Rig as "Player"
        GameObject rig = FindXRRig();
        if (rig != null && !rig.CompareTag("Player"))
        {
            rig.tag = "Player";
            Debug.Log($"[EnvironmentAgent] Tagged '{rig.name}' as Player.");
        }

        // 2. Ensure PlayerTouchHighlighter exists and is enabled
        PlayerTouchHighlighter pth = FindFirstObjectByType<PlayerTouchHighlighter>(FindObjectsInactive.Include);
        if (pth == null)
        {
            pth = gameObject.AddComponent<PlayerTouchHighlighter>();
            Debug.Log("[EnvironmentAgent] Added PlayerTouchHighlighter.");
        }

        if (rig != null && pth.playerTransform == null)
            pth.playerTransform = rig.transform;

        pth.enabled = true;

        // Give PlayerAvatarController the now-tagged XR rig so it can lock avatar position to it
        if (playerAvatarController != null && rig != null && playerAvatarController.xrRigRoot == null)
            playerAvatarController.xrRigRoot = rig.transform;

        Debug.Log("[EnvironmentAgent] Phase 2 active — PlayerTouchHighlighter enabled.");
    }

    private static GameObject FindXRRig()
    {
        // Delegate to PlayerAvatarController's shared helper (partial-name search)
        Transform t = PlayerAvatarController.FindXRRigRoot();
        return t != null ? t.gameObject : null;
    }

    // ========== Interaction feedback commands (Phase 2) ==========

    /// <summary>Temporarily highlight an object with an outline / emission color.
    /// duration &lt;= 0 means highlight until explicitly cleared.</summary>
    public void HighlightObject(string objectName, float r, float g, float b, float duration)
    {
        GameObject target = FindSpawnedObject(objectName);
        if (target == null) { Debug.LogWarning($"HighlightObject: '{objectName}' not found."); return; }
        HighlightObject(target, r, g, b, duration);
    }

    /// <summary>Highlight a GameObject directly (no name lookup required).</summary>
    public void HighlightObject(GameObject target, float r, float g, float b, float duration)
    {
        if (target == null) return;
        StartCoroutine(HighlightCoroutine(target, new Color(r, g, b), duration));
    }

    private System.Collections.IEnumerator HighlightCoroutine(GameObject target, Color color, float duration)
    {
        var renderers = target.GetComponentsInChildren<Renderer>();

        // Store material instances up-front so the restore loop uses the exact
        // same instances we modified (calling .material again can return a
        // different instance if another coroutine ran between apply and restore).
        var mats         = new Material[renderers.Length];
        var origBase     = new Color[renderers.Length];
        var origEmission = new Color[renderers.Length];
        var hadEmission  = new bool[renderers.Length];

        for (int i = 0; i < renderers.Length; i++)
        {
            mats[i] = renderers[i].material; // instantiates if needed; same ref hereafter

            if (mats[i].HasProperty("_BaseColor"))
            {
                origBase[i] = mats[i].GetColor("_BaseColor");
                mats[i].SetColor("_BaseColor", Color.Lerp(origBase[i], color, 0.6f));
            }
            else if (mats[i].HasProperty("_Color"))
            {
                origBase[i] = mats[i].GetColor("_Color");
                mats[i].SetColor("_Color", Color.Lerp(origBase[i], color, 0.6f));
            }

            hadEmission[i]  = mats[i].IsKeywordEnabled("_EMISSION");
            origEmission[i] = mats[i].HasProperty("_EmissionColor")
                ? mats[i].GetColor("_EmissionColor")
                : Color.black;

            if (mats[i].HasProperty("_EmissionColor"))
            {
                mats[i].EnableKeyword("_EMISSION");
                mats[i].SetColor("_EmissionColor", color * 0.8f);
            }
        }

        if (duration > 0f)
        {
            yield return new WaitForSeconds(duration);
            for (int i = 0; i < renderers.Length; i++)
            {
                if (renderers[i] == null || mats[i] == null) continue;

                if (mats[i].HasProperty("_BaseColor"))
                    mats[i].SetColor("_BaseColor", origBase[i]);
                else if (mats[i].HasProperty("_Color"))
                    mats[i].SetColor("_Color", origBase[i]);

                if (mats[i].HasProperty("_EmissionColor"))
                {
                    mats[i].SetColor("_EmissionColor", origEmission[i]);
                    if (!hadEmission[i]) mats[i].DisableKeyword("_EMISSION");
                }
            }
        }
    }

    /// <summary>Show a speech bubble above a named character for <paramref name="duration"/> seconds.</summary>
    public void ShowSpeechBubble(string characterName, string text, float duration = 5f)
    {
        var charGo = GetCharacterByName(characterName);
        if (charGo == null)
        {
            Debug.LogWarning($"[EnvironmentAgent] ShowSpeechBubble: character '{characterName}' not found.");
            return;
        }
        var bubble = charGo.GetComponent<SpeechBubble>() ?? charGo.AddComponent<SpeechBubble>();
        bubble.Show(text, duration);
        Debug.Log($"[EnvironmentAgent] Speech bubble → {characterName}: \"{text}\"");
    }

    /// <summary>Clear highlight from an object.</summary>
    public void ClearHighlight(string objectName)
    {
        GameObject target = FindSpawnedObject(objectName);
        if (target == null) return;
        var renderers = target.GetComponentsInChildren<Renderer>();
        foreach (var r in renderers)
            r.material.SetColor("_EmissionColor", Color.black);
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
    GameObject GetHumanTarget(GameObject human)
    {
        if (human != null) return human;
        AutoBindDefaultHumanIfNeeded();
        return defaultHumanModel;
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

    static Animator FindPrimaryAnimator(GameObject root)
    {
        if (root == null) return null;
        var animators = root.GetComponentsInChildren<Animator>(true);
        if (animators == null || animators.Length == 0) return null;
        if (animators.Length == 1) return animators[0];

        // Prefer an Animator that sits on the main humanoid bone hierarchy.
        var humanoidAnimator = FindAnimatorFromHumanoidBones(root);
        if (humanoidAnimator != null) return humanoidAnimator;

        // Prefer the Animator that drives the "Body" mesh (if present).
        var bodyRenderer = FindBodySkinnedMesh(root);
        if (bodyRenderer != null)
        {
            var bodyAnimator = bodyRenderer.GetComponentInParent<Animator>(true);
            if (bodyAnimator != null) return bodyAnimator;
        }

        Animator best = null;
        int bestScore = int.MinValue;
        foreach (var anim in animators)
        {
            if (anim == null) continue;
            int score = 0;

            // Prefer animators closer to the root.
            score -= GetDepth(anim.transform);

            // Prefer animators that have an Avatar assigned.
            if (anim.avatar != null) score += 100;
            if (anim.avatar != null && anim.avatar.isHuman) score += 100;

            // Prefer animators that drive more skinned meshes.
            int meshCount = anim.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length;
            score += meshCount * 10;

            // Prefer animators with more child bones.
            int boneCount = anim.transform.GetComponentsInChildren<Transform>(true).Length;
            score += boneCount / 10;

            if (score > bestScore)
            {
                bestScore = score;
                best = anim;
            }
        }
        return best;
    }

    static Animator FindAnimatorFromHumanoidBones(GameObject root)
    {
        var bones = root.GetComponentsInChildren<Transform>(true);
        string[] keyBones = { "Hips", "Pelvis", "Spine", "Root", "B-root" };
        Animator best = null;
        int bestScore = int.MinValue;

        foreach (var t in bones)
        {
            if (t == null) continue;
            bool isKeyBone = false;
            for (int i = 0; i < keyBones.Length; i++)
            {
                if (t.name.IndexOf(keyBones[i], System.StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    isKeyBone = true;
                    break;
                }
            }
            if (!isKeyBone) continue;

            var anim = t.GetComponentInParent<Animator>(true);
            if (anim == null) continue;

            int score = 0;
            score -= GetDepth(anim.transform);
            if (anim.avatar != null) score += 100;
            if (anim.avatar != null && anim.avatar.isHuman) score += 100;
            score += anim.transform.GetComponentsInChildren<Transform>(true).Length / 10;

            if (score > bestScore)
            {
                bestScore = score;
                best = anim;
            }
        }
        return best;
    }

    static SkinnedMeshRenderer FindBodySkinnedMesh(GameObject root)
    {
        var renderers = root.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        foreach (var r in renderers)
        {
            if (r == null) continue;
            var name = r.name;
            if (name.IndexOf("body", System.StringComparison.OrdinalIgnoreCase) >= 0)
                return r;
        }
        return null;
    }

    static void RebindSkinnedMeshesToAnimator(GameObject root, Animator anim)
    {
        if (root == null || anim == null) return;
        var boneMap = new System.Collections.Generic.Dictionary<string, Transform>(System.StringComparer.OrdinalIgnoreCase);
        foreach (var t in anim.GetComponentsInChildren<Transform>(true))
        {
            if (!boneMap.ContainsKey(t.name))
                boneMap[t.name] = t;
        }

        var renderers = root.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        foreach (var r in renderers)
        {
            if (r == null) continue;

            // If already bound to the animator hierarchy, skip.
            if (r.rootBone != null && r.rootBone.IsChildOf(anim.transform))
                continue;

            var newBones = new Transform[r.bones.Length];
            bool anyMissing = false;
            for (int i = 0; i < r.bones.Length; i++)
            {
                var b = r.bones[i];
                if (b != null && boneMap.TryGetValue(b.name, out var mapped))
                    newBones[i] = mapped;
                else
                    anyMissing = true;
            }

            if (anyMissing)
                continue;

            r.bones = newBones;

            if (r.rootBone != null && boneMap.TryGetValue(r.rootBone.name, out var mappedRoot))
                r.rootBone = mappedRoot;
            else if (boneMap.TryGetValue("Hips", out var hips))
                r.rootBone = hips;
            else if (boneMap.TryGetValue("Root", out var rootBone))
                r.rootBone = rootBone;
        }
    }

    static void HardCleanupHumanRig(GameObject root, Animator anim)
    {
        if (root == null || anim == null) return;
        RemoveChildAnimators(root, anim);
        RebindSkinnedMeshesToAnimator(root, anim);

        // Force all skinned meshes to use the same Animator GameObject for update.
        var renderers = root.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        foreach (var r in renderers)
        {
            if (r == null) continue;
            r.updateWhenOffscreen = true;
            r.forceMatrixRecalculationPerRender = true;
        }

        Debug.Log($"[HumanRig] Hard cleanup applied to {root.name}, using Animator {anim.name}");
    }

    static int GetDepth(Transform t)
    {
        int depth = 0;
        while (t != null && t.parent != null)
        {
            depth++;
            t = t.parent;
        }
        return depth;
    }

    static void RemoveChildAnimators(GameObject root, Animator keep)
    {
        if (root == null || keep == null) return;
        var animators = root.GetComponentsInChildren<Animator>(true);
        foreach (var anim in animators)
        {
            if (anim == keep) continue;
            Destroy(anim);
        }
    }

    void AutoBindDefaultHumanIfNeeded()
    {
        if (defaultHumanModel != null && GetHumanAnimator(defaultHumanModel) != null)
            return;

        // Choose the best Animator in the scene (avoid accessories like hats/shoes).
        Animator[] all = FindObjectsByType<Animator>(FindObjectsSortMode.None);
        Animator chosen = ChooseBestSceneAnimator(all);
        if (chosen != null)
        {
            defaultHumanModel = chosen.gameObject;
            Debug.Log($"EnvironmentAgent: auto-bound human = {defaultHumanModel.name}");
        }
    }

    // ========== Multi-character support ==========

    /// <summary>
    /// Find a named character by the name it was placed with.
    /// Falls back to a scene search so pre-placed characters work too.
    /// </summary>
    public GameObject GetCharacterByName(string name)
    {
        if (string.IsNullOrEmpty(name)) return null;
        if (_namedCharacters.TryGetValue(name, out var go) && go != null) return go;
        return FindSpawnedObject(name);
    }

    /// <summary>Move a named character to a new position.</summary>
    public void MoveCharacter(string name, Vector3 position, float rotationY)
    {
        GameObject target = GetCharacterByName(name);
        if (target == null)
        {
            Debug.LogWarning($"[EnvironmentAgent] MoveCharacter: '{name}' not found.");
            return;
        }
        target.transform.position = position;
        target.transform.rotation = Quaternion.Euler(0, rotationY, 0);
        WriteSceneState();
        Debug.Log($"[EnvironmentAgent] MoveCharacter: '{name}' → {position}");
    }

    /// <summary>
    /// Attach (or replace) the player body avatar.
    /// prefabName: Resources path or plain prefab name (same lookup as PlaceObject).
    /// The spawned avatar is registered under "player" so animation commands with
    /// targetModel="player" automatically target it.
    /// </summary>
    public void AttachPlayerAvatar(string prefabName)
    {
        // Auto-add controller if not assigned
        if (playerAvatarController == null)
        {
            playerAvatarController = gameObject.GetComponent<PlayerAvatarController>()
                                  ?? gameObject.AddComponent<PlayerAvatarController>();
            Debug.Log("[EnvironmentAgent] Auto-added PlayerAvatarController.");
        }

        GameObject prefab = LoadPrefabByName(prefabName);
        if (prefab == null)
        {
            // Try direct Resources path
            prefab = Resources.Load<GameObject>(prefabName);
        }
        if (prefab == null)
        {
            Debug.LogError($"[EnvironmentAgent] AttachPlayerAvatar: prefab '{prefabName}' not found in Resources.");
            return;
        }

        GameObject avatarRoot = playerAvatarController.AttachAvatar(prefab);
        if (avatarRoot != null)
        {
            // Register as "player" so GetCharacterByName("player") works
            _namedCharacters["player"] = avatarRoot;
            _trackedObjects["PlayerAvatar"] = avatarRoot;
            WriteSceneState();
            Debug.Log($"[EnvironmentAgent] Player avatar '{prefabName}' attached and registered as 'player'.");
        }
    }

    /// <summary>
    /// Play an animation on a named character.  If characterName is null/empty,
    /// falls back to defaultHumanModel (same behaviour as PlayHumanAnimationFromResources(null,...)).
    /// </summary>
    public void PlayHumanAnimationFromResourcesNamed(string characterName, string controllerPath, string stateName, float crossFadeTime = 0.25f)
    {
        GameObject target = string.IsNullOrEmpty(characterName)
            ? GetHumanTarget(null)
            : GetCharacterByName(characterName);

        if (target == null)
        {
            Debug.LogWarning($"[EnvironmentAgent] PlayHumanAnimationFromResourcesNamed: character '{characterName}' not found.");
            return;
        }
        PlayHumanAnimationFromResources(target, controllerPath, stateName, crossFadeTime);
    }

    static Animator ChooseBestSceneAnimator(Animator[] animators)
    {
        if (animators == null || animators.Length == 0) return null;
        Animator best = null;
        int bestScore = int.MinValue;

        foreach (var a in animators)
        {
            if (a == null) continue;
            int score = 0;

            string name = a.gameObject.name;
            if (name.IndexOf("human", System.StringComparison.OrdinalIgnoreCase) >= 0) score += 50;
            if (name.IndexOf("doctor", System.StringComparison.OrdinalIgnoreCase) >= 0) score += 50;

            // Penalize accessories.
            if (name.IndexOf("hat", System.StringComparison.OrdinalIgnoreCase) >= 0) score -= 200;
            if (name.IndexOf("shoe", System.StringComparison.OrdinalIgnoreCase) >= 0) score -= 200;
            if (name.IndexOf("pants", System.StringComparison.OrdinalIgnoreCase) >= 0) score -= 150;
            if (name.IndexOf("shirt", System.StringComparison.OrdinalIgnoreCase) >= 0) score -= 150;

            if (a.avatar != null) score += 100;
            if (a.avatar != null && a.avatar.isHuman) score += 100;

            // Prefer animators that drive more skinned meshes (full body beats accessory).
            int meshCount = a.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length;
            score += meshCount * 20;

            // Prefer animators with deeper bone hierarchies.
            int boneCount = a.transform.GetComponentsInChildren<Transform>(true).Length;
            score += boneCount / 10;

            if (score > bestScore)
            {
                bestScore = score;
                best = a;
            }
        }
        return best;
    }
    /// <summary>Called by Agent: switch the human's Animator Controller. controllerPath is path under Resources, e.g. "AnimatorControllers/HumanM@Walk01_Forward" (place Kevin Iglesias .controller under Resources/AnimatorControllers/).</summary>
    public void SetHumanAnimationController(GameObject human, string controllerPathInResources)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) { Debug.LogWarning("EnvironmentAgent: no human model assigned."); return; }

        Animator anim = FindPrimaryAnimator(target);
        if (anim == null) { Debug.LogWarning("EnvironmentAgent: human model has no Animator."); return; }
        HardCleanupHumanRig(target, anim);

        RuntimeAnimatorController ctrl = LoadAnimatorControllerByName(controllerPathInResources);
        if (ctrl == null) { Debug.LogError($"EnvironmentAgent: AnimatorController not found at Resources/{controllerPathInResources}"); return; }

        anim.runtimeAnimatorController = ctrl;
        Debug.Log($"Agent: human AnimatorController set to {controllerPathInResources}");
    }

    /// <summary>Called by Agent: play the given animation state (state name inside current Controller), e.g. "HumanM@Walk01_Forward". crossFadeTime is blend duration.</summary>
    public void PlayHumanAnimation(GameObject human, string stateName, float crossFadeTime = 0.25f)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) { Debug.LogWarning("EnvironmentAgent: no human model assigned."); return; }

        Animator anim = FindPrimaryAnimator(target);
        if (anim == null) { Debug.LogWarning("EnvironmentAgent: human model has no Animator."); return; }
        HardCleanupHumanRig(target, anim);

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
        GameObject target = GetHumanTarget(human);
        if (target == null) { Debug.LogWarning("EnvironmentAgent: no human model assigned."); return; }

        Animator anim = FindPrimaryAnimator(target);
        if (anim == null) { Debug.LogWarning("EnvironmentAgent: human model has no Animator."); return; }
        HardCleanupHumanRig(target, anim);

        // Always apply the specified controller so we're never playing states against the wrong one.
        if (!string.IsNullOrEmpty(controllerPathInResources))
        {
            RuntimeAnimatorController ctrl = LoadAnimatorControllerByName(controllerPathInResources);
            if (ctrl == null)
            {
                Debug.LogError($"EnvironmentAgent: AnimatorController not found at Resources/{controllerPathInResources}");
                return;
            }
            anim.runtimeAnimatorController = ctrl;
            // Let the controller initialise for one frame before crossing-fading into its state.
            anim.Update(0f);
        }

        anim.CrossFade(stateName, crossFadeTime);
        Debug.Log($"Agent: human playing state '{stateName}' (controller: {controllerPathInResources})");
    }

    [ContextMenu("Debug Human Rig (Default)")]
    void DebugHumanRigDefault()
    {
        DebugHumanRig(null);
    }

    /// <summary>Log animators and skinned mesh bindings for the current/default human.</summary>
    public void DebugHumanRig(GameObject human)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null)
        {
            Debug.LogWarning("EnvironmentAgent: no human model assigned.");
            return;
        }

        Debug.Log($"[HumanRig] Target = {target.name}");

        var animators = target.GetComponentsInChildren<Animator>(true);
        Debug.Log($"[HumanRig] Animators: {animators.Length}");
        foreach (var a in animators)
        {
            if (a == null) continue;
            string avatar = a.avatar != null ? a.avatar.name : "None";
            string isHuman = (a.avatar != null && a.avatar.isHuman) ? "Human" : "Generic/None";
            Debug.Log($"[HumanRig] Animator: {a.name} | Avatar: {avatar} ({isHuman}) | Path: {GetHierarchyPath(a.transform)}");
        }

        var renderers = target.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        Debug.Log($"[HumanRig] SkinnedMeshRenderers: {renderers.Length}");
        foreach (var r in renderers)
        {
            if (r == null) continue;
            string rootBone = r.rootBone != null ? r.rootBone.name : "None";
            string rootPath = r.rootBone != null ? GetHierarchyPath(r.rootBone) : "None";
            Debug.Log($"[HumanRig] Mesh: {r.name} | RootBone: {rootBone} | RootPath: {rootPath} | Bones: {r.bones.Length}");
            for (int i = 0; i < Mathf.Min(5, r.bones.Length); i++)
            {
                var b = r.bones[i];
                Debug.Log($"[HumanRig]   Bone[{i}]: {(b != null ? b.name : "None")}");
            }
        }
    }

    static string GetHierarchyPath(Transform t)
    {
        if (t == null) return "None";
        var path = t.name;
        var current = t.parent;
        while (current != null)
        {
            path = current.name + "/" + path;
            current = current.parent;
        }
        return path;
    }

    /// <summary>Set an Animator Float parameter (e.g. Speed).</summary>
    public void SetHumanAnimatorFloat(GameObject human, string paramName, float value)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetFloat(paramName, value);
    }

    /// <summary>Set an Animator Bool parameter.</summary>
    public void SetHumanAnimatorBool(GameObject human, string paramName, bool value)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetBool(paramName, value);
    }

    /// <summary>Trigger an Animator Trigger parameter.</summary>
    public void SetHumanAnimatorTrigger(GameObject human, string paramName)
    {
        GameObject target = GetHumanTarget(human);
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

    private static readonly System.Collections.Generic.Dictionary<string, GameObject> PrefabCache =
        new System.Collections.Generic.Dictionary<string, GameObject>();

    private static readonly System.Collections.Generic.Dictionary<string, RuntimeAnimatorController> AnimatorCache =
        new System.Collections.Generic.Dictionary<string, RuntimeAnimatorController>();

    private static bool _prefabCacheLoaded = false;
    private static bool _animatorCacheLoaded = false;

    private void EnsurePrefabCache()
    {
        if (_prefabCacheLoaded) return;
        PrefabCache.Clear();
        var all = Resources.LoadAll<GameObject>("");
        foreach (var go in all)
        {
            if (go == null) continue;
            var key = go.name.ToLowerInvariant();
            if (!PrefabCache.ContainsKey(key))
                PrefabCache[key] = go;
        }
        Debug.Log($"EnvironmentAgent: Prefab cache loaded ({PrefabCache.Count} prefabs)");
        _prefabCacheLoaded = true;
    }

    private void EnsureAnimatorCache()
    {
        if (_animatorCacheLoaded) return;
        AnimatorCache.Clear();
        var all = Resources.LoadAll<RuntimeAnimatorController>("");
        foreach (var ctrl in all)
        {
            if (ctrl == null) continue;
            var key = ctrl.name.ToLowerInvariant();
            if (!AnimatorCache.ContainsKey(key))
                AnimatorCache[key] = ctrl;
        }
        Debug.Log($"EnvironmentAgent: Animator cache loaded ({AnimatorCache.Count} controllers)");
        _animatorCacheLoaded = true;
    }

    private GameObject LoadPrefabByName(string name)
    {
        if (string.IsNullOrEmpty(name)) return null;
        EnsurePrefabCache();
        PrefabCache.TryGetValue(name.ToLowerInvariant(), out var prefab);
        return prefab;
    }

    private GameObject LoadHumanPrefabByName(string name)
    {
        // Same lookup as other prefabs (humans are also prefabs).
        return LoadPrefabByName(name);
    }

    private RuntimeAnimatorController LoadAnimatorControllerByName(string nameOrPath)
    {
        if (string.IsNullOrEmpty(nameOrPath)) return null;
        // Try direct path first (Resources.Load supports relative paths).
        var direct = Resources.Load<RuntimeAnimatorController>(nameOrPath);
        if (direct != null) return direct;

        EnsureAnimatorCache();
        AnimatorCache.TryGetValue(nameOrPath.ToLowerInvariant(), out var ctrl);
        return ctrl;
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
