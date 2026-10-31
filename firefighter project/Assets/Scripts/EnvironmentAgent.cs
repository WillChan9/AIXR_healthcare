using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

/// <summary>
/// Central scene-control API used by the LLM agent. Every command received by
/// <see cref="AgentNetworkBridge"/> ends up as a call to one of the public methods here.
///
/// Responsibilities:
/// <list type="bullet">
/// <item><description>Spawning, moving and deleting objects and characters loaded by
/// name from any <c>Resources</c> folder (with primitive-shape fallbacks).</description></item>
/// <item><description>Environment parameters: directional light, time of day, fog,
/// ambient light, gravity and time scale.</description></item>
/// <item><description>Humanoid animation (Kevin Iglesias Human Animations controllers and
/// ithappy Medical Pack characters), including smooth walking between positions.</description></item>
/// <item><description>Interaction feedback: object highlighting, speech bubbles, the player
/// avatar and phase switching (Phase 2 enables touch detection).</description></item>
/// <item><description>Exporting the live scene layout to <c>Agent/api_files/scene_state.json</c>
/// after every change so the Python agent can reason about current positions and bounds.</description></item>
/// </list>
///
/// Setup: add this component to a scene GameObject (AI_Manager in the sample scene).
/// <see cref="InteractionSetup"/>, <see cref="InteractionAnimationController"/> and
/// <see cref="InteractionEventReporter"/> are added automatically through
/// <c>RequireComponent</c>, and a <see cref="PlayerAvatarController"/> is added in
/// <c>Start</c> if missing. All Inspector references are optional.
/// </summary>
[RequireComponent(typeof(InteractionSetup))]
[RequireComponent(typeof(InteractionAnimationController))]
[RequireComponent(typeof(InteractionEventReporter))]
public class EnvironmentAgent : MonoBehaviour
{
    // ---- Scene-state tracking ----

    // Unique key -> spawned GameObject, so WriteSceneState() can query live transforms and bounds.
    private readonly Dictionary<string, GameObject> _trackedObjects = new Dictionary<string, GameObject>();
    // Counter for generating unique keys when the same objectName is created several times (e.g. Cube walls).
    private int _spawnCounter = 0;
    // Character name -> spawned character GameObject, for addressing multiple characters by name.
    private readonly Dictionary<string, GameObject> _namedCharacters = new Dictionary<string, GameObject>();

    // ---- Inspector ----

    [Header("Optional: directional light for light / time-of-day control (auto-found if empty)")]
    [SerializeField] Light mainDirectionalLight;

    [Header("Optional: adds XR interaction components to spawned objects (auto-found if empty)")]
    [SerializeField] InteractionSetup interactionSetup;

    [Header("Optional: default human model for animation commands without a target")]
    [Tooltip("Leave empty to auto-select the best humanoid Animator in the scene (names containing 'Human' or 'Doctor' are preferred). Each PlaceHuman call replaces this with the newly placed character.")]
    [SerializeField] GameObject defaultHumanModel;

    [Header("Optional: player avatar")]
    [Tooltip("PlayerAvatarController that drives the player's visible body. Added automatically at Start if empty.")]
    [SerializeField] PlayerAvatarController playerAvatarController;

    [Header("ithappy Medical Pack: test prefabs for the context-menu spawn buttons")]
    [SerializeField] GameObject ithappyFurniturePrefab;   // e.g. Medical_Pack/Prefabs/Furniture/Table_01
    [SerializeField] GameObject ithappyPropPrefab;        // e.g. Medical_Pack/Prefabs/Props/Syringe_02
    [SerializeField] GameObject ithappyCharacterPrefab;   // e.g. Medical_Pack/Characters/Prefabs/Doctor_01_Sneakers

    [Header("ithappy Medical Pack: controller for the context-menu animation tests")]
    [Tooltip("AnimatorController to use, e.g. Medical_Pack/Animations/Animation_Controllers/All_Animations.controller")]
    [SerializeField] RuntimeAnimatorController ithappyCharacterController;

    // Last character spawned by the "ithappy: Spawn Character (test)" context menu.
    GameObject ithappyCharacterInstance;

    // ---- Lifecycle ----

    /// <summary>
    /// Resolves optional references (light, InteractionSetup, default human) and makes
    /// sure the InteractionEventReporter and PlayerAvatarController components exist.
    /// </summary>
    void Start()
    {
        if (mainDirectionalLight == null)
            mainDirectionalLight = FindFirstObjectByType<Light>();

        if (interactionSetup == null)
            interactionSetup = GetComponent<InteractionSetup>();
        if (interactionSetup == null)
            interactionSetup = FindFirstObjectByType<InteractionSetup>();

        // Ensure InteractionEventReporter is present (required for Phase 2 touch logging).
        if (GetComponent<InteractionEventReporter>() == null)
        {
            gameObject.AddComponent<InteractionEventReporter>();
            Debug.Log("[EnvironmentAgent] Auto-added InteractionEventReporter.");
        }

        AutoBindDefaultHumanIfNeeded();

        // Auto-add PlayerAvatarController so the player body is present without Inspector setup.
        if (playerAvatarController == null)
        {
            playerAvatarController = GetComponent<PlayerAvatarController>()
                                  ?? gameObject.AddComponent<PlayerAvatarController>();
            Debug.Log("[EnvironmentAgent] Auto-added PlayerAvatarController.");
        }
    }

    // ---- Object placement ----

    /// <summary>
    /// Tracks a spawned object under <paramref name="key"/>. If the key is already in
    /// use by a different GameObject, the old one is destroyed first so it does not
    /// become an untracked orphan.
    /// </summary>
    private void TrackObject(string key, GameObject spawned)
    {
        if (_trackedObjects.TryGetValue(key, out var old) && old != null && old != spawned)
        {
            Debug.Log($"[Track] Replacing existing '{key}' — destroying old instance.");
            Destroy(old);
        }
        _trackedObjects[key] = spawned;
    }

    /// <summary>
    /// Agent action <c>PlaceObject</c>: instantiates a prefab, snaps it to the surface
    /// below and makes it XR-interactable.
    /// </summary>
    /// <remarks>
    /// The prefab is looked up by name (case-insensitive) among all prefabs in any
    /// <c>Resources</c> folder. If the same name has already been placed, the new
    /// instance is named and tracked as <c>{objectName}__inst_N</c> so multiple
    /// instances coexist (e.g. four PlaceObject Bed_01 commands spawn four beds).
    /// </remarks>
    /// <param name="objectName">Prefab name, e.g. "Bed_01".</param>
    /// <param name="position">World position; Y is replaced by the floor-snap result when a surface is found.</param>
    /// <param name="rotationY">Yaw in degrees.</param>
    public void PlaceObject(string objectName, Vector3 position, float rotationY)
    {
        GameObject prefab = LoadPrefabByName(objectName);

        if (prefab != null)
        {
            GameObject spawned = Instantiate(prefab, position, Quaternion.Euler(0, rotationY, 0));
            SnapToFloor(spawned);
            string trackKey = _uniqueTrackKey(objectName);
            spawned.name = trackKey;
            TrackObject(trackKey, spawned);
            WriteSceneState();
            if (interactionSetup != null) interactionSetup.MakeInteractable(spawned);
            Debug.Log($"AI Agent placed: {trackKey} at {position}");
        }
        else
        {
            Debug.LogError($"AI Agent Error: Prefab '{objectName}' not found in Resources/Prefabs/");
        }
    }

    /// <summary>
    /// Returns a tracking key that does not collide with an existing one: the first
    /// call returns <paramref name="baseName"/> unchanged, later calls return
    /// <c>baseName__inst_2</c>, <c>baseName__inst_3</c>, and so on.
    /// </summary>
    private string _uniqueTrackKey(string baseName)
    {
        if (!_trackedObjects.ContainsKey(baseName)) return baseName;
        int n = 2;
        while (_trackedObjects.ContainsKey($"{baseName}__inst_{n}")) n++;
        return $"{baseName}__inst_{n}";
    }

    /// <summary>
    /// Agent action <c>CreateObjectByName</c>: creates an object with an explicit scale,
    /// typically used to build room structure (floor, walls, ceiling) from Cubes.
    /// </summary>
    /// <remarks>
    /// Looks for a prefab at <c>Resources/Prefabs/{objectName}</c> first, then falls back
    /// to a Unity primitive (cube/box, sphere/ball, capsule, cylinder, plane, quad).
    /// Unknown names are skipped with a warning. Objects placed above y = 0.05 or with
    /// scaleY &gt; 1.5 are treated as structural and are not floor-snapped. Repeated
    /// names are tracked as <c>{objectName}__struct_N</c>.
    /// </remarks>
    /// <param name="objectName">Prefab or primitive name.</param>
    /// <param name="position">World position.</param>
    /// <param name="rotationY">Yaw in degrees.</param>
    /// <param name="scaleX">X scale; values &lt;= 0 mean 1.</param>
    /// <param name="scaleY">Y scale; values &lt;= 0 mean 1.</param>
    /// <param name="scaleZ">Z scale; values &lt;= 0 mean 1.</param>
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

            // Use a unique key so multiple Cubes (walls, floor, ceiling) each get their own entry.
            string trackKey = objectName;
            if (_trackedObjects.ContainsKey(trackKey))
            {
                trackKey = $"{objectName}__struct_{_spawnCounter++}";
            }
            spawned.name = trackKey;  // Match the hierarchy name to the tracking key so name lookups work
            TrackObject(trackKey, spawned);
            WriteSceneState();
            if (interactionSetup != null) interactionSetup.MakeInteractable(spawned);
        }
    }

    /// <summary>
    /// Agent action <c>PlaceHuman</c> (also used by <c>PlaceObject</c> for character
    /// prefab names): spawns a character prefab standing in an idle pose.
    /// </summary>
    /// <remarks>
    /// The prefab is looked up by name like <see cref="PlaceObject"/>. The character is
    /// snapped to the real floor (never onto furniture), extra Animators are removed,
    /// the <c>AnimatorControllers/HumanM@Idles</c> controller is applied, and a
    /// CapsuleCollider is added if the prefab has none (needed for touch detection).
    /// The character is registered by name for <see cref="GetCharacterByName"/> and
    /// becomes the default target for animation calls that pass no explicit target.
    /// </remarks>
    /// <param name="humanName">Character prefab name, e.g. "Doctor_01".</param>
    /// <param name="position">World position (X/Z used; Y comes from the floor).</param>
    /// <param name="rotationY">Yaw in degrees.</param>
    public void PlaceHuman(string humanName, Vector3 position, float rotationY)
    {
        GameObject prefab = LoadHumanPrefabByName(humanName);

        if (prefab != null)
        {
            GameObject spawned = Instantiate(prefab, position, Quaternion.Euler(0, rotationY, 0));
            SnapHumanToFloor(spawned);
            defaultHumanModel = spawned;
            // Ensure only the main Animator drives the humanoid rig (remove extra Animators on clothing, etc.).
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
                    // No idle controller available: clear the controller so the prefab's default animation does not play.
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

            string trackKey = _uniqueTrackKey(humanName);
            spawned.name = trackKey;
            TrackObject(trackKey, spawned);
            _namedCharacters[trackKey] = spawned;
            WriteSceneState();
            Debug.Log($"AI Agent placed human: {trackKey} at {position} (set as default human, idle pose)");
        }
        else
        {
            Debug.LogError($"AI Agent Error: Human prefab '{humanName}' not found in Resources. Place human prefabs under Assets/Resources/...");
        }
    }

    /// <summary>
    /// Agent action <c>DeleteObject</c>: finds an object by name (see
    /// <see cref="FindSpawnedObject"/>) and destroys it.
    /// </summary>
    /// <param name="objectName">Name or name prefix of the object to delete.</param>
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

    /// <summary>
    /// Agent action <c>ClearScene</c>: destroys all spawned objects and characters,
    /// resetting the scene to an empty room.
    /// </summary>
    /// <remarks>
    /// After clearing the tracking dictionaries, a full scan of root GameObjects is also
    /// performed so untracked objects are removed as well. Scene infrastructure is kept:
    /// cameras, lights, audio listeners, XR rigs, objects tagged "Player", this agent's
    /// own GameObject and a fixed list of named objects. Floor objects (flat Cubes near
    /// y = 0) are preserved so characters placed afterwards do not fall through.
    /// </remarks>
    public void ClearScene()
    {
        int count = 0;

        // 1. Destroy everything in the tracking dictionaries, but keep floor objects.
        var keepObjects = new System.Collections.Generic.Dictionary<string, GameObject>();
        foreach (var kvp in _trackedObjects)
        {
            if (kvp.Value != null)
            {
                if (IsFloorObject(kvp.Value))
                {
                    keepObjects[kvp.Key] = kvp.Value;
                    continue;
                }
                Destroy(kvp.Value);
                count++;
            }
        }
        _trackedObjects.Clear();
        // Re-add preserved floor objects.
        foreach (var kvp in keepObjects)
            _trackedObjects[kvp.Key] = kvp.Value;

        foreach (var kvp in _namedCharacters)
        {
            if (kvp.Value != null) { Destroy(kvp.Value); count++; }
        }
        _namedCharacters.Clear();

        // 2. Full scene scan: destroy any remaining root objects that are not scene
        //    infrastructure (cameras, lights, EventSystem, XR rigs, Canvas, etc.).
        var preserve = new System.Collections.Generic.HashSet<string>(System.StringComparer.OrdinalIgnoreCase)
        {
            "Main Camera", "Directional Light", "EventSystem",
            "XR Interaction Manager", "Canvas", "MixedRealityPlayspace",
            "ScreenshotSender", "AIAgent",
        };

        var toDestroy = new System.Collections.Generic.List<GameObject>();
        foreach (var go in FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go == null) continue;
            // Only consider root-level objects; destroying a root also destroys its children.
            if (go.transform.parent != null) continue;
            // Skip the EnvironmentAgent's own GameObject.
            if (go == this.gameObject) continue;
            // Skip preserved infrastructure objects.
            string cleanName = go.name.Replace("(Clone)", "").Trim();
            if (preserve.Contains(cleanName)) continue;
            // Skip objects with cameras, lights or an AudioListener (infrastructure).
            if (go.GetComponent<Camera>() != null) continue;
            if (go.GetComponent<Light>() != null) continue;
            if (go.GetComponent<AudioListener>() != null) continue;
            // Skip XR rigs (detected by common naming conventions).
            if (go.name.Contains("XR") || go.name.Contains("xr") ||
                go.name.Contains("OVR") || go.name.Contains("Rig")) continue;
            // Skip the player rig / avatar (tagged "Player").
            if (go.CompareTag("Player")) continue;
            // Skip floor objects.
            if (IsFloorObject(go)) continue;

            toDestroy.Add(go);
        }

        foreach (var go in toDestroy)
        {
            if (go != null) { Destroy(go); count++; }
        }

        defaultHumanModel = null;
        ithappyCharacterInstance = null;
        _spawnCounter = 0;

        WriteSceneState();
        Debug.Log($"AI Agent ClearScene: destroyed {count} objects, kept {keepObjects.Count} floor(s).");
    }

    /// <summary>
    /// Returns true if <paramref name="go"/> looks like a floor: a GameObject whose name
    /// contains "Cube", positioned at y &lt;= 0.15 with a flat scale
    /// (Y &lt;= 0.5, X and Z &gt;= 1).
    /// </summary>
    private static bool IsFloorObject(GameObject go)
    {
        if (go == null) return false;
        // The floor is a Cube primitive with flat proportions near y = 0.
        bool isCube = go.name.Contains("Cube");
        if (!isCube) return false;
        Vector3 pos = go.transform.position;
        Vector3 scale = go.transform.localScale;
        bool nearGround = pos.y <= 0.15f;
        bool isFlat = scale.y <= 0.5f && scale.x >= 1f && scale.z >= 1f;
        return nearGround && isFlat;
    }

    /// <summary>
    /// Agent action <c>MoveObject</c>: finds an object by name and teleports it to a new
    /// position and yaw (no floor snapping).
    /// </summary>
    /// <param name="objectName">Name or name prefix of the object to move.</param>
    /// <param name="position">New world position.</param>
    /// <param name="rotationY">New yaw in degrees.</param>
    public void MoveObject(string objectName, Vector3 position, float rotationY)
    {
        GameObject target = FindSpawnedObject(objectName);
        if (target != null)
        {
            // Dynamic rigidbodies are moved through physics; everything else via the transform.
            Rigidbody rb = target.GetComponent<Rigidbody>();
            Quaternion newRotation = Quaternion.Euler(0, rotationY, 0);

            if (rb != null && !rb.isKinematic)
            {
                rb.MovePosition(position);
                rb.MoveRotation(newRotation);
                Debug.Log($"AI Agent moved (physics): {objectName} to {position}");
            }
            else
            {
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

    // ---- Scene state export ----

    /// <summary>
    /// Writes <c>{repoRoot}/Agent/api_files/scene_state.json</c>, where repoRoot is the
    /// folder that contains the Unity project folder.
    /// </summary>
    /// <remarks>
    /// The file is a JSON array with one entry per tracked object: <c>objectName</c>,
    /// world position <c>x</c>/<c>y</c>/<c>z</c>, <c>rotationY</c>, and the world-space
    /// renderer bounds (<c>boundsMin*</c>, <c>boundsMax*</c>, <c>boundsSize*</c>; all zero
    /// if the object has no renderers). The Python agent reads this file instead of
    /// reconstructing state from the command log. Called after every scene change.
    /// </remarks>
    private void WriteSceneState()
    {
        // Write to Agent/api_files/ so the Python agent can read it directly.
        string repoRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        string dir = Path.Combine(repoRoot, "Agent", "api_files");
        string path = Path.Combine(dir, "scene_state.json");

        var sb = new StringBuilder();
        sb.AppendLine("[");
        bool first = true;

        foreach (var kv in _trackedObjects)
        {
            // Strip the __struct_N suffix so scene_state.json reports the original name.
            // (The __inst_N suffix from PlaceObject/PlaceHuman is kept.)
            string prefabName = kv.Key;
            int structIdx = prefabName.IndexOf("__struct_");
            if (structIdx >= 0) prefabName = prefabName.Substring(0, structIdx);
            GameObject go = kv.Value;
            if (go == null) continue;  // destroyed but not yet removed from the dictionary

            Vector3 pos = go.transform.position;
            float rotY = go.transform.eulerAngles.y;

            // Compute world-space bounds by encapsulating all renderers.
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
                // No renderers: emit zero bounds so Python can still read the record.
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

    /// <summary>Escapes backslashes and double quotes for embedding in a JSON string.</summary>
    private static string EscapeJson(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    /// <summary>
    /// Finds an active GameObject by name: exact match first, then with Unity's
    /// "(Clone)" suffix, then the first object whose name starts with
    /// <paramref name="objectName"/> (case-insensitive). Searches the whole scene,
    /// not only objects spawned by the agent.
    /// </summary>
    /// <returns>The matching GameObject, or null if none was found.</returns>
    private static GameObject FindSpawnedObject(string objectName)
    {
        if (string.IsNullOrEmpty(objectName)) return null;

        // Exact match.
        GameObject found = GameObject.Find(objectName);
        if (found != null) return found;

        // Unity appends "(Clone)" on Instantiate.
        found = GameObject.Find(objectName + "(Clone)");
        if (found != null) return found;

        // Prefix search across all active objects.
        string lower = objectName.ToLowerInvariant();
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go.name.ToLowerInvariant().StartsWith(lower))
                return go;
        }
        return null;
    }

    // ---- Environment parameters ----

    /// <summary>Agent action <c>SetLightIntensity</c>: sets the main directional light intensity, clamped to [0, 2].</summary>
    public void SetLightIntensity(float intensity)
    {
        if (mainDirectionalLight == null) return;
        mainDirectionalLight.intensity = Mathf.Clamp(intensity, 0f, 2f);
        Debug.Log($"Agent: Light intensity = {mainDirectionalLight.intensity}");
    }

    /// <summary>Agent action <c>SetLightColor</c>: sets the main light color; each channel is clamped to [0, 1].</summary>
    public void SetLightColor(float r, float g, float b)
    {
        if (mainDirectionalLight == null) return;
        mainDirectionalLight.color = new Color(
            Mathf.Clamp01(r), Mathf.Clamp01(g), Mathf.Clamp01(b));
    }

    /// <summary>
    /// Agent action <c>SetTimeOfDay</c>: rotates the main directional light to simulate
    /// the sun's elevation for an hour in [0, 24) (values wrap around).
    /// </summary>
    public void SetTimeOfDay(float hour)
    {
        if (mainDirectionalLight == null) return;
        float t = Mathf.Repeat(hour / 24f, 1f);
        float angle = Mathf.Lerp(-90f, 270f, t);
        mainDirectionalLight.transform.rotation = Quaternion.Euler(angle, 30f, 0f);
        Debug.Log($"Agent: Time of day = {hour:F1}h");
    }

    /// <summary>Agent action <c>SetFogEnabled</c>: enables or disables scene fog.</summary>
    public void SetFogEnabled(bool enabled)
    {
        RenderSettings.fog = enabled;
    }

    /// <summary>Agent action <c>SetFogDensity</c>: sets the fog density, clamped to [0, 0.2] (0 to 0.1 recommended).</summary>
    public void SetFogDensity(float density)
    {
        RenderSettings.fogDensity = Mathf.Clamp(density, 0f, 0.2f);
    }

    // ---- Phase control ----

    /// <summary>
    /// Agent action <c>SetPhase</c>: switches the experiment phase. Phase 1 is scene
    /// building; any value &gt;= 2 activates Phase 2 (player interaction), which enables
    /// touch detection through <see cref="PlayerTouchHighlighter"/>. Lower values only log.
    /// </summary>
    public void SetPhase(int phase)
    {
        Debug.Log($"[EnvironmentAgent] SetPhase({phase})");
        if (phase >= 2)
            ActivatePhase2();
    }

    /// <summary>
    /// Tags the XR rig as "Player", ensures a <see cref="PlayerTouchHighlighter"/>
    /// exists and is enabled, and hands the rig transform to the touch highlighter and
    /// the player avatar controller.
    /// </summary>
    private void ActivatePhase2()
    {
        // 0. Ensure PlayerAvatarController exists on this GameObject.
        if (playerAvatarController == null)
            playerAvatarController = gameObject.GetComponent<PlayerAvatarController>()
                                  ?? gameObject.AddComponent<PlayerAvatarController>();

        // 1. Tag the XR Origin / Camera Rig as "Player".
        GameObject rig = FindXRRig();
        if (rig != null && !rig.CompareTag("Player"))
        {
            rig.tag = "Player";
            Debug.Log($"[EnvironmentAgent] Tagged '{rig.name}' as Player.");
        }

        // 2. Ensure PlayerTouchHighlighter exists and is enabled.
        PlayerTouchHighlighter pth = FindFirstObjectByType<PlayerTouchHighlighter>(FindObjectsInactive.Include);
        if (pth == null)
        {
            pth = gameObject.AddComponent<PlayerTouchHighlighter>();
            Debug.Log("[EnvironmentAgent] Added PlayerTouchHighlighter.");
        }

        if (rig != null && pth.playerTransform == null)
            pth.playerTransform = rig.transform;

        pth.enabled = true;

        // 3. Give PlayerAvatarController the now-tagged XR rig so it can keep the avatar on the rig's floor level.
        if (playerAvatarController != null && rig != null && playerAvatarController.xrRigRoot == null)
            playerAvatarController.xrRigRoot = rig.transform;

        Debug.Log("[EnvironmentAgent] Phase 2 active — PlayerTouchHighlighter enabled.");
    }

    /// <summary>Finds the XR rig root GameObject via <see cref="PlayerAvatarController.FindXRRigRoot"/>.</summary>
    private static GameObject FindXRRig()
    {
        // Delegate to PlayerAvatarController's shared helper (tag, then partial-name search).
        Transform t = PlayerAvatarController.FindXRRigRoot();
        return t != null ? t.gameObject : null;
    }

    // ---- Interaction feedback (Phase 2) ----

    /// <summary>
    /// Agent action <c>HighlightObject</c>: finds an object by name and tints it with
    /// an emissive glow of the given color.
    /// </summary>
    /// <param name="objectName">Name or name prefix of the object.</param>
    /// <param name="r">Red, 0 to 1.</param>
    /// <param name="g">Green, 0 to 1.</param>
    /// <param name="b">Blue, 0 to 1.</param>
    /// <param name="duration">Seconds before the original colors are restored; &lt;= 0 keeps the highlight.</param>
    public void HighlightObject(string objectName, float r, float g, float b, float duration)
    {
        GameObject target = FindSpawnedObject(objectName);
        if (target == null) { Debug.LogWarning($"HighlightObject: '{objectName}' not found."); return; }
        HighlightObject(target, r, g, b, duration);
    }

    /// <summary>Highlights a GameObject directly (no name lookup). Same behavior as the name-based overload.</summary>
    public void HighlightObject(GameObject target, float r, float g, float b, float duration)
    {
        if (target == null) return;
        StartCoroutine(HighlightCoroutine(target, new Color(r, g, b), duration));
    }

    /// <summary>
    /// Tints every renderer's base color 85% toward <paramref name="color"/> and sets an
    /// HDR emission (2.5x the color) so it glows under bloom; after
    /// <paramref name="duration"/> seconds (if &gt; 0) the original values are restored.
    /// Supports both URP (<c>_BaseColor</c>) and built-in (<c>_Color</c>) shaders.
    /// </summary>
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

            // Tint the base color strongly toward the highlight color. Weaker tints
            // (e.g. 0.6) are hard to see on light materials; 0.85 is clearly visible
            // without fully replacing the original color.
            if (mats[i].HasProperty("_BaseColor"))
            {
                origBase[i] = mats[i].GetColor("_BaseColor");
                mats[i].SetColor("_BaseColor", Color.Lerp(origBase[i], color, 0.85f));
            }
            else if (mats[i].HasProperty("_Color"))
            {
                origBase[i] = mats[i].GetColor("_Color");
                mats[i].SetColor("_Color", Color.Lerp(origBase[i], color, 0.85f));
            }

            hadEmission[i]  = mats[i].IsKeywordEnabled("_EMISSION");
            origEmission[i] = mats[i].HasProperty("_EmissionColor")
                ? mats[i].GetColor("_EmissionColor")
                : Color.black;

            // Boost emission to HDR levels so the object actually glows: the bloom
            // threshold is around 1.0, so values below that produce no glow.
            if (mats[i].HasProperty("_EmissionColor"))
            {
                mats[i].EnableKeyword("_EMISSION");
                mats[i].SetColor("_EmissionColor", color * 2.5f);
                mats[i].globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
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

    /// <summary>
    /// Agent action <c>ShowSpeechBubble</c>: shows a world-space speech bubble above a
    /// character. A <see cref="SpeechBubble"/> component is added to the character on
    /// first use.
    /// </summary>
    /// <param name="characterName">Character name (see <see cref="GetCharacterByName"/>).</param>
    /// <param name="text">Text to display.</param>
    /// <param name="duration">Seconds the bubble stays visible.</param>
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

    /// <summary>
    /// Agent action <c>ClearHighlight</c>: sets the emission color of every renderer on
    /// the named object to black. Note that this does not restore a tinted base color.
    /// </summary>
    public void ClearHighlight(string objectName)
    {
        GameObject target = FindSpawnedObject(objectName);
        if (target == null) return;
        var renderers = target.GetComponentsInChildren<Renderer>();
        foreach (var r in renderers)
            r.material.SetColor("_EmissionColor", Color.black);
    }

    /// <summary>Sets the fog color; each channel is clamped to [0, 1]. Not exposed as an agent action.</summary>
    public void SetFogColor(float r, float g, float b)
    {
        RenderSettings.fogColor = new Color(
            Mathf.Clamp01(r), Mathf.Clamp01(g), Mathf.Clamp01(b));
    }

    /// <summary>Agent action <c>SetAmbientColor</c>: sets the ambient light color; each channel is clamped to [0, 1].</summary>
    public void SetAmbientColor(float r, float g, float b)
    {
        RenderSettings.ambientLight = new Color(
            Mathf.Clamp01(r), Mathf.Clamp01(g), Mathf.Clamp01(b));
    }

    /// <summary>Agent action <c>SetGravity</c>: sets gravity to (0, gravityY, 0). Unity's default is -9.81.</summary>
    public void SetGravity(float gravityY)
    {
        Physics.gravity = new Vector3(0, gravityY, 0);
    }

    /// <summary>Agent action <c>SetTimeScale</c>: sets <c>Time.timeScale</c>, clamped to [0, 2] (0 = paused, 1 = normal).</summary>
    public void SetTimeScale(float scale)
    {
        Time.timeScale = Mathf.Clamp(scale, 0f, 2f);
    }

    // ---- Humanoid animation: rig helpers ----
    // Characters must have an Animator. Kevin Iglesias rigs that use upper/lower body
    // blending also need the SpineProxy component on the B-spineProxy bone.

    /// <summary>
    /// Returns the character to animate: <paramref name="human"/> if given, otherwise
    /// <see cref="defaultHumanModel"/> (auto-selected from the scene if unset).
    /// </summary>
    GameObject GetHumanTarget(GameObject human)
    {
        if (human != null) return human;
        AutoBindDefaultHumanIfNeeded();
        return defaultHumanModel;
    }

    /// <summary>
    /// Returns the first Animator found on the object itself, then its children, then its
    /// parents (Kevin Iglesias characters sometimes keep the Animator on a child or parent).
    /// </summary>
    static Animator GetHumanAnimator(GameObject target)
    {
        if (target == null) return null;
        Animator a = target.GetComponent<Animator>();
        if (a != null) return a;
        a = target.GetComponentInChildren<Animator>(true);
        if (a != null) return a;
        return target.GetComponentInParent<Animator>();
    }

    /// <summary>
    /// Picks the Animator that drives the main body of a character prefab that may
    /// contain several (e.g. separate clothing parts with their own Animators).
    /// Preference order: the Animator above the humanoid key bones, then the one above
    /// the "Body" skinned mesh, then the best score (human Avatar, more skinned meshes,
    /// more bones, closer to the root).
    /// </summary>
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

    /// <summary>
    /// Finds the best-scoring Animator that is a parent of a key humanoid bone
    /// (Hips, Pelvis, Spine, Root, B-root). Returns null if there is none.
    /// </summary>
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

    /// <summary>Returns the first SkinnedMeshRenderer whose name contains "body" (case-insensitive), or null.</summary>
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

    /// <summary>
    /// Re-targets skinned meshes that are not yet bound to <paramref name="anim"/>'s
    /// skeleton onto bones with the same names under it, so one Animator drives all
    /// parts. Meshes with any bone that has no match are left unchanged.
    /// </summary>
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

    /// <summary>
    /// Prepares a multi-part character for animation: destroys every Animator except
    /// <paramref name="anim"/>, rebinds all skinned meshes to its skeleton, and forces
    /// the meshes to update even when off-screen.
    /// </summary>
    static void HardCleanupHumanRig(GameObject root, Animator anim)
    {
        if (root == null || anim == null) return;
        RemoveChildAnimators(root, anim);
        RebindSkinnedMeshesToAnimator(root, anim);

        // Keep every skinned mesh updating so no part freezes or culls while animating.
        var renderers = root.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        foreach (var r in renderers)
        {
            if (r == null) continue;
            r.updateWhenOffscreen = true;
            r.forceMatrixRecalculationPerRender = true;
        }

        Debug.Log($"[HumanRig] Hard cleanup applied to {root.name}, using Animator {anim.name}");
    }

    /// <summary>Returns the number of ancestors of <paramref name="t"/> (0 for a root object).</summary>
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

    /// <summary>Destroys every Animator under <paramref name="root"/> except <paramref name="keep"/>.</summary>
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

    /// <summary>
    /// If <see cref="defaultHumanModel"/> is unset or has no Animator, selects the
    /// best-scoring humanoid Animator in the scene (see <see cref="ChooseBestSceneAnimator"/>).
    /// </summary>
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

    // ---- Multi-character support ----

    /// <summary>
    /// Finds a character by the name it was registered under (its tracking key from
    /// <see cref="PlaceHuman"/>, or "player" after <see cref="AttachPlayerAvatar"/>).
    /// Falls back to <see cref="FindSpawnedObject"/> so pre-placed characters work too.
    /// </summary>
    /// <returns>The character's root GameObject, or null if not found.</returns>
    public GameObject GetCharacterByName(string name)
    {
        if (string.IsNullOrEmpty(name)) return null;
        if (_namedCharacters.TryGetValue(name, out var go) && go != null) return go;
        return FindSpawnedObject(name);
    }

    // Active move/walk coroutine per character, so a new MoveCharacter or WalkCharacterTo
    // call cancels and replaces any motion still in progress on the same target.
    private readonly Dictionary<GameObject, Coroutine> _activeCharMoves = new Dictionary<GameObject, Coroutine>();

    /// <summary>
    /// Agent action <c>MoveCharacter</c>: moves a named character without playing a walk
    /// animation (no floor snapping).
    /// </summary>
    /// <param name="name">Character name (see <see cref="GetCharacterByName"/>).</param>
    /// <param name="position">Target world position.</param>
    /// <param name="rotationY">Target yaw in degrees.</param>
    /// <param name="duration">0 (default) teleports instantly; &gt; 0 interpolates smoothly over that many seconds.
    /// The agent action always uses the instant teleport.</param>
    public void MoveCharacter(string name, Vector3 position, float rotationY, float duration = 0f)
    {
        GameObject target = GetCharacterByName(name);
        if (target == null)
        {
            Debug.LogWarning($"[EnvironmentAgent] MoveCharacter: '{name}' not found.");
            return;
        }

        // Cancel any in-progress move on this character so the new call wins.
        if (_activeCharMoves.TryGetValue(target, out var prev) && prev != null)
        {
            StopCoroutine(prev);
            _activeCharMoves.Remove(target);
        }

        if (duration <= 0f)
        {
            target.transform.position = position;
            target.transform.rotation = Quaternion.Euler(0, rotationY, 0);
            WriteSceneState();
            Debug.Log($"[EnvironmentAgent] MoveCharacter: '{name}' → {position} (instant)");
            return;
        }

        var co = StartCoroutine(MoveCharacterSmoothCoroutine(target, name, position, rotationY, duration));
        _activeCharMoves[target] = co;
        Debug.Log($"[EnvironmentAgent] MoveCharacter: '{name}' → {position} over {duration}s");
    }

    /// <summary>Smoothly interpolates position and yaw (smooth-step easing) over <paramref name="duration"/> seconds.</summary>
    private System.Collections.IEnumerator MoveCharacterSmoothCoroutine(
        GameObject target, string name, Vector3 endPos, float endRotY, float duration)
    {
        Vector3 startPos = target.transform.position;
        Quaternion startRot = target.transform.rotation;
        Quaternion endRot = Quaternion.Euler(0, endRotY, 0);
        float t = 0f;
        while (t < duration)
        {
            if (target == null) yield break;
            t += Time.deltaTime;
            float k = Mathf.SmoothStep(0f, 1f, Mathf.Clamp01(t / duration));
            target.transform.position = Vector3.Lerp(startPos, endPos, k);
            target.transform.rotation = Quaternion.Slerp(startRot, endRot, k);
            yield return null;
        }
        if (target != null)
        {
            target.transform.position = endPos;
            target.transform.rotation = endRot;
            WriteSceneState();
        }
        _activeCharMoves.Remove(target);
    }

    /// <summary>
    /// Agent action <c>WalkCharacterTo</c>: makes a named character walk to a target
    /// position. Plays the walk animation, smoothly interpolates position and rotation
    /// over <paramref name="duration"/> seconds, then crossfades to the idle animation
    /// on arrival.
    /// </summary>
    /// <remarks>
    /// Root motion is disabled during the walk so it does not fight the transform
    /// interpolation, and restored on arrival. The animator state played is the last
    /// path segment of each controller path (e.g. "HumanM@Walk01_Forward").
    /// </remarks>
    /// <param name="name">Character name (see <see cref="GetCharacterByName"/>).</param>
    /// <param name="position">Target world position.</param>
    /// <param name="rotationY">Final yaw in degrees.</param>
    /// <param name="duration">Walk time in seconds; &lt;= 0 means 2 seconds.</param>
    /// <param name="walkController">Resources path of the walk controller, e.g.
    /// "AnimatorControllers/HumanF@Walk01_Forward". Null/empty means
    /// "AnimatorControllers/HumanM@Walk01_Forward".</param>
    /// <param name="idleController">Resources path of the idle controller. Null/empty means
    /// "AnimatorControllers/HumanM@Idles".</param>
    public void WalkCharacterTo(string name, Vector3 position, float rotationY, float duration,
                                string walkController, string idleController)
    {
        GameObject target = GetCharacterByName(name);
        if (target == null)
        {
            Debug.LogWarning($"[EnvironmentAgent] WalkCharacterTo: '{name}' not found.");
            return;
        }

        if (string.IsNullOrEmpty(walkController))
            walkController = "AnimatorControllers/HumanM@Walk01_Forward";
        if (string.IsNullOrEmpty(idleController))
            idleController = "AnimatorControllers/HumanM@Idles";
        if (duration <= 0f) duration = 2.0f;

        // Cancel any in-progress motion on this character.
        if (_activeCharMoves.TryGetValue(target, out var prev) && prev != null)
        {
            StopCoroutine(prev);
            _activeCharMoves.Remove(target);
        }

        // Disable root motion during transform-driven motion. Walk animations
        // usually contain root motion, which would fight the transform.position
        // interpolation and make the character appear to walk in place. Save the
        // original setting so it can be restored when the walk finishes.
        var animator = target.GetComponentInChildren<Animator>();
        bool savedRootMotion = false;
        if (animator != null)
        {
            savedRootMotion = animator.applyRootMotion;
            animator.applyRootMotion = false;
        }

        // Kick off the walk animation immediately, then start the move coroutine.
        string walkState = LeafName(walkController);
        PlayHumanAnimationFromResources(target, walkController, walkState, 0.3f);

        var co = StartCoroutine(WalkCoroutine(target, name, position, rotationY, duration,
                                              idleController, animator, savedRootMotion));
        _activeCharMoves[target] = co;
        Debug.Log($"[EnvironmentAgent] WalkCharacterTo: '{name}' → {position} over {duration}s "
                  + $"(walk={walkState}, rootMotion saved={savedRootMotion})");
    }

    /// <summary>
    /// Interpolates the character to its destination, then restores root motion and
    /// crossfades to the idle controller.
    /// </summary>
    private System.Collections.IEnumerator WalkCoroutine(
        GameObject target, string name, Vector3 endPos, float endRotY, float duration,
        string idleController, Animator animator, bool restoreRootMotion)
    {
        Vector3 startPos = target.transform.position;
        Quaternion startRot = target.transform.rotation;
        Quaternion endRot = Quaternion.Euler(0, endRotY, 0);
        float t = 0f;
        while (t < duration)
        {
            if (target == null) yield break;
            t += Time.deltaTime;
            float k = Mathf.SmoothStep(0f, 1f, Mathf.Clamp01(t / duration));
            target.transform.position = Vector3.Lerp(startPos, endPos, k);
            target.transform.rotation = Quaternion.Slerp(startRot, endRot, k);
            yield return null;
        }
        if (target != null)
        {
            target.transform.position = endPos;
            target.transform.rotation = endRot;
            // Restore the original root motion setting before switching to idle
            // so the idle animation can drive the character normally.
            if (animator != null) animator.applyRootMotion = restoreRootMotion;
            // Crossfade back to idle.
            string idleState = LeafName(idleController);
            PlayHumanAnimationFromResources(target, idleController, idleState, 0.4f);
            WriteSceneState();
            Debug.Log($"[EnvironmentAgent] WalkCharacterTo: '{name}' arrived → idle ({idleState}).");
        }
        _activeCharMoves.Remove(target);
    }

    /// <summary>Returns the part of a Resources path after the last '/', e.g. "HumanM@Idles".</summary>
    private static string LeafName(string resourcesPath)
    {
        if (string.IsNullOrEmpty(resourcesPath)) return "";
        int slash = resourcesPath.LastIndexOf('/');
        return slash >= 0 ? resourcesPath.Substring(slash + 1) : resourcesPath;
    }

    /// <summary>
    /// Agent action <c>AttachPlayerAvatar</c>: attaches (or replaces) the player's
    /// visible body via <see cref="PlayerAvatarController.AttachAvatar"/>.
    /// </summary>
    /// <remarks>
    /// The spawned avatar is registered under the character name "player" so animation
    /// commands with <c>targetModel = "player"</c> target it.
    /// </remarks>
    /// <param name="prefabName">Plain prefab name (same lookup as <see cref="PlaceObject"/>)
    /// or a full Resources path.</param>
    public void AttachPlayerAvatar(string prefabName)
    {
        // Auto-add the controller if not assigned.
        if (playerAvatarController == null)
        {
            playerAvatarController = gameObject.GetComponent<PlayerAvatarController>()
                                  ?? gameObject.AddComponent<PlayerAvatarController>();
            Debug.Log("[EnvironmentAgent] Auto-added PlayerAvatarController.");
        }

        GameObject prefab = LoadPrefabByName(prefabName);
        if (prefab == null)
        {
            // Fall back to treating the name as a direct Resources path.
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
            // Register as "player" so GetCharacterByName("player") works.
            _namedCharacters["player"] = avatarRoot;
            _trackedObjects["PlayerAvatar"] = avatarRoot;
            WriteSceneState();
            Debug.Log($"[EnvironmentAgent] Player avatar '{prefabName}' attached and registered as 'player'.");
        }
    }

    /// <summary>
    /// Agent action <c>PlayHumanAnimationFromResources</c>: loads a controller and plays a
    /// state on a named character.
    /// </summary>
    /// <param name="characterName">Character name (see <see cref="GetCharacterByName"/>).
    /// Null/empty targets <see cref="defaultHumanModel"/>.</param>
    /// <param name="controllerPath">Resources path of the AnimatorController.</param>
    /// <param name="stateName">Animator state to crossfade into.</param>
    /// <param name="crossFadeTime">Blend time in seconds.</param>
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

    /// <summary>
    /// Scores scene Animators to find the most likely full-body human: names containing
    /// "human" or "doctor" and human Avatars score higher, clothing/accessory names
    /// (hat, shoe, pants, shirt) score lower, and more skinned meshes and bones help.
    /// </summary>
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

            // Penalize clothing and accessories.
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

    // ---- Humanoid animation: playback ----

    /// <summary>
    /// Agent action <c>SetHumanAnimationController</c>: replaces the character's
    /// AnimatorController without playing a specific state.
    /// </summary>
    /// <param name="human">Character to change; null targets <see cref="defaultHumanModel"/>
    /// (the agent action always passes null).</param>
    /// <param name="controllerPathInResources">Resources path or controller name, e.g.
    /// "AnimatorControllers/HumanM@Walk01_Forward".</param>
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

    /// <summary>
    /// Agent action <c>PlayHumanAnimation</c>: crossfades into a state of the character's
    /// current AnimatorController (e.g. "HumanM@Walk01_Forward"). Does nothing if the
    /// Animator has no controller; use <see cref="PlayHumanAnimationFromResources"/> then.
    /// </summary>
    /// <param name="human">Character to animate; null targets <see cref="defaultHumanModel"/>.</param>
    /// <param name="stateName">Animator state name.</param>
    /// <param name="crossFadeTime">Blend time in seconds.</param>
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
    /// Loads an AnimatorController from Resources, applies it to the character and
    /// crossfades into the given state. Preferred over <see cref="PlayHumanAnimation"/>
    /// because it never fails with "Animator does not have an AnimatorController".
    /// </summary>
    /// <param name="human">Character to animate; null targets <see cref="defaultHumanModel"/>.</param>
    /// <param name="controllerPathInResources">Resources path or controller name, e.g.
    /// "AnimatorControllers/HumanM@Walk01_Forward". Null/empty keeps the current controller.</param>
    /// <param name="stateName">Animator state name, e.g. "HumanM@Walk01_Forward".</param>
    /// <param name="crossFadeTime">Blend time in seconds.</param>
    public void PlayHumanAnimationFromResources(GameObject human, string controllerPathInResources, string stateName, float crossFadeTime = 0.25f)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) { Debug.LogWarning("EnvironmentAgent: no human model assigned."); return; }

        Animator anim = FindPrimaryAnimator(target);
        if (anim == null) { Debug.LogWarning("EnvironmentAgent: human model has no Animator."); return; }
        HardCleanupHumanRig(target, anim);

        // Always apply the specified controller so the state is never looked up in the wrong one.
        if (!string.IsNullOrEmpty(controllerPathInResources))
        {
            RuntimeAnimatorController ctrl = LoadAnimatorControllerByName(controllerPathInResources);
            if (ctrl == null)
            {
                Debug.LogError($"EnvironmentAgent: AnimatorController not found at Resources/{controllerPathInResources}");
                return;
            }
            anim.runtimeAnimatorController = ctrl;
            // Evaluate the new controller once so CrossFade can find its states immediately.
            anim.Update(0f);
        }

        anim.CrossFade(stateName, crossFadeTime);
        Debug.Log($"Agent: human playing state '{stateName}' (controller: {controllerPathInResources})");
    }

    // ---- Debugging ----

    /// <summary>Context-menu wrapper for <see cref="DebugHumanRig"/> on the default human.</summary>
    [ContextMenu("Debug Human Rig (Default)")]
    void DebugHumanRigDefault()
    {
        DebugHumanRig(null);
    }

    /// <summary>Logs the Animators and skinned-mesh bone bindings of a character (null = default human).</summary>
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

    /// <summary>Returns the slash-separated hierarchy path of <paramref name="t"/>, e.g. "Root/Hips/Spine".</summary>
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

    // ---- Humanoid animation: Animator parameters ----

    /// <summary>Agent action <c>SetHumanAnimatorFloat</c>: sets an Animator float parameter (e.g. Speed). Null targets the default human.</summary>
    public void SetHumanAnimatorFloat(GameObject human, string paramName, float value)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetFloat(paramName, value);
    }

    /// <summary>Agent action <c>SetHumanAnimatorBool</c>: sets an Animator bool parameter. Null targets the default human.</summary>
    public void SetHumanAnimatorBool(GameObject human, string paramName, bool value)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetBool(paramName, value);
    }

    /// <summary>Agent action <c>SetHumanAnimatorTrigger</c>: fires an Animator trigger parameter. Null targets the default human.</summary>
    public void SetHumanAnimatorTrigger(GameObject human, string paramName)
    {
        GameObject target = GetHumanTarget(human);
        if (target == null) return;
        Animator anim = GetHumanAnimator(target);
        if (anim != null) anim.SetTrigger(paramName);
    }

    // ---- Placement helpers ----

    /// <summary>
    /// Drops an object onto the first collider found below it (ray cast from 1 m above
    /// its position, 10 m long). The object is left unchanged if nothing is hit.
    /// </summary>
    private void SnapToFloor(GameObject obj)
    {
        RaycastHit hit;
        if (Physics.Raycast(obj.transform.position + Vector3.up, Vector3.down, out hit, 10f))
        {
            obj.transform.position = hit.point;
        }
    }

    /// <summary>
    /// Snaps a character to the actual floor, never onto chairs, desks or trolleys.
    /// Casts from high above (ignoring the agent's spawn Y) and uses the highest hit
    /// that belongs to a floor object (see <see cref="IsFloorObject"/>). Falls back to
    /// y = 0 if no floor collider is found. Use this instead of
    /// <see cref="SnapToFloor"/> when spawning characters.
    /// </summary>
    private void SnapHumanToFloor(GameObject obj)
    {
        Vector3 origin = obj.transform.position + Vector3.up * 20f;
        RaycastHit[] hits = Physics.RaycastAll(origin, Vector3.down, 25f);

        float bestFloorY = float.NaN;
        if (hits != null)
        {
            foreach (var h in hits)
            {
                if (h.collider == null) continue;
                GameObject root = h.collider.transform.root.gameObject;
                if (!IsFloorObject(root)) continue;
                if (float.IsNaN(bestFloorY) || h.point.y > bestFloorY)
                    bestFloorY = h.point.y;
            }
        }

        Vector3 p = obj.transform.position;
        if (!float.IsNaN(bestFloorY))
            obj.transform.position = new Vector3(p.x, bestFloorY, p.z);
        else
            obj.transform.position = new Vector3(p.x, 0f, p.z);
    }

    /// <summary>
    /// Creates a Unity primitive for the names cube/box, sphere/ball, capsule, cylinder,
    /// plane and quad (case-insensitive).
    /// </summary>
    /// <returns>True if a primitive was created.</returns>
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

    // ---- Resource lookup ----
    // Every prefab and AnimatorController under any Resources folder is indexed once by
    // lowercase asset name, so the agent can refer to assets by name alone. When two
    // assets share a name, the first one loaded wins.

    private static readonly System.Collections.Generic.Dictionary<string, GameObject> PrefabCache =
        new System.Collections.Generic.Dictionary<string, GameObject>();

    private static readonly System.Collections.Generic.Dictionary<string, RuntimeAnimatorController> AnimatorCache =
        new System.Collections.Generic.Dictionary<string, RuntimeAnimatorController>();

    private static bool _prefabCacheLoaded = false;
    private static bool _animatorCacheLoaded = false;

    /// <summary>Builds <see cref="PrefabCache"/> on first use by loading every GameObject in Resources.</summary>
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

    /// <summary>Builds <see cref="AnimatorCache"/> on first use by loading every RuntimeAnimatorController in Resources.</summary>
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

    /// <summary>Returns the prefab with the given name (case-insensitive) from any Resources folder, or null.</summary>
    private GameObject LoadPrefabByName(string name)
    {
        if (string.IsNullOrEmpty(name)) return null;
        EnsurePrefabCache();
        PrefabCache.TryGetValue(name.ToLowerInvariant(), out var prefab);
        return prefab;
    }

    /// <summary>Character prefab lookup; identical to <see cref="LoadPrefabByName"/>.</summary>
    private GameObject LoadHumanPrefabByName(string name)
    {
        // Same lookup as other prefabs (characters are ordinary prefabs).
        return LoadPrefabByName(name);
    }

    /// <summary>
    /// Loads an AnimatorController by Resources path (e.g. "AnimatorControllers/HumanM@Idles")
    /// or, failing that, by controller name alone (case-insensitive). Returns null if not found.
    /// </summary>
    private RuntimeAnimatorController LoadAnimatorControllerByName(string nameOrPath)
    {
        if (string.IsNullOrEmpty(nameOrPath)) return null;
        // Try the value as a Resources-relative path first.
        var direct = Resources.Load<RuntimeAnimatorController>(nameOrPath);
        if (direct != null) return direct;

        EnsureAnimatorCache();
        AnimatorCache.TryGetValue(nameOrPath.ToLowerInvariant(), out var ctrl);
        return ctrl;
    }

    // ---- Inspector test buttons (ithappy Medical Pack) ----
    // Right-click the component header in the Inspector during Play Mode to use these.
    // They use the test prefabs assigned in the Inspector and are not tracked in scene_state.json.

    /// <summary>Context menu: spawns <see cref="ithappyFurniturePrefab"/> at (0, 0, 2).</summary>
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

    /// <summary>Context menu: spawns <see cref="ithappyPropPrefab"/> at (1.5, 0, 2).</summary>
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

    /// <summary>Context menu: spawns <see cref="ithappyCharacterPrefab"/> at (-1.5, 0, 2) for the animation tests below.</summary>
    [ContextMenu("ithappy: Spawn Character (test)")]
    void TestSpawnIthappyCharacter()
    {
        if (ithappyCharacterPrefab == null)
        {
            Debug.LogWarning("EnvironmentAgent: ithappyCharacterPrefab not assigned in Inspector. Assign a prefab from Medical_Pack/Characters/Prefabs.");
            return;
        }
        ithappyCharacterInstance = Instantiate(ithappyCharacterPrefab, new Vector3(-1.5f, 0, 2), Quaternion.identity);
        SnapHumanToFloor(ithappyCharacterInstance);
        Debug.Log($"EnvironmentAgent: Spawn ithappy character '{ithappyCharacterPrefab.name}' (set as ithappyCharacterInstance)");
    }

    /// <summary>
    /// Crossfades the test character into <paramref name="stateName"/>, assigning
    /// <see cref="ithappyCharacterController"/> first if its Animator has no controller.
    /// </summary>
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
