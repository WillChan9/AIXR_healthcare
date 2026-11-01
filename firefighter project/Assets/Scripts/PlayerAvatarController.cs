using UnityEngine;

/// <summary>
/// Gives the VR player a visible humanoid body that follows the XR rig, so the player
/// can look down and see themselves, and so the agent can drive player-body animations
/// (sit, lie down, etc.) through EnvironmentAgent's PlayHumanAnimation* methods with
/// <c>targetModel = "player"</c>.
/// </summary>
/// <remarks>
/// Behavior:
/// <list type="bullet">
/// <item><description>The avatar prefab is instantiated at the scene root (not under
/// the XR rig) so it does not jitter with tracking updates.</description></item>
/// <item><description>Every LateUpdate the avatar's X/Z position follows
/// <c>Camera.main</c> (works for both VR locomotion and keyboard movement), its Y stays
/// at the XR rig root's floor level plus <see cref="floorOffset"/>, and its yaw matches
/// the camera's.</description></item>
/// <item><description>For the ithappy patient presets, the separate clothing / face /
/// hair prefabs are attached and rebound to the body skeleton.</description></item>
/// <item><description>Head, face and hair renderers are disabled so the camera does not
/// see the inside of the avatar's head (or all renderers, if
/// <see cref="hideFromPlayer"/> is set).</description></item>
/// <item><description>The avatar is tagged "Player" so touch detection and ClearScene
/// ignore it.</description></item>
/// </list>
/// Setup: <see cref="EnvironmentAgent"/> adds this component to its own GameObject
/// automatically. Pick an <see cref="avatarPreset"/> or assign <see cref="avatarPrefab"/>
/// in the Inspector, or call <see cref="AttachAvatar"/> at runtime (the agent action
/// <c>AttachPlayerAvatar</c> does this).
/// </remarks>
public class PlayerAvatarController : MonoBehaviour
{
    /// <summary>Built-in player body models. Each maps to a prefab path inside a Resources folder.</summary>
    public enum AvatarPreset
    {
        // Kevin Iglesias base dummies (grey tracksuit); copied into Resources by PlayerAvatarResourceLinker.
        Male,
        Female,
        // ithappy Medical Pack patients (hospital gown)
        Patient_01,
        Patient_02,
        Patient_03,
        Patient_04,
        Patient_05,
        Patient_06,
    }

    // ---- Inspector ----

    /// <summary>Preset body model, used when <see cref="avatarPrefab"/> is not assigned.</summary>
    [Tooltip("Player body model. Male/Female = Kevin Iglesias grey tracksuit dummies; Patient_01 to Patient_06 = ithappy hospital-gown characters. Ignored if Avatar Prefab is assigned.")]
    public AvatarPreset avatarPreset = AvatarPreset.Patient_01;

    /// <summary>Optional prefab that overrides <see cref="avatarPreset"/>.</summary>
    [Tooltip("Optional override: any prefab to use instead of the preset above.")]
    public GameObject avatarPrefab;

    /// <summary>XR rig root, used for the avatar's floor height. Auto-found if empty.</summary>
    [Tooltip("The XR rig root transform. Auto-found if left empty (by tag 'Player', then by name, then the Main Camera's root).")]
    public Transform xrRigRoot;

    /// <summary>If true, every avatar renderer is disabled; otherwise only head renderers are hidden.</summary>
    [Tooltip("If true, all renderers on the player avatar are disabled: the body exists for animation and physics but is invisible. If false, only head/face/hair renderers are hidden.")]
    public bool hideFromPlayer = false;

    /// <summary>Vertical offset of the avatar root relative to the XR rig's floor height.</summary>
    [Tooltip("Vertical offset of the avatar root relative to the XR rig floor position (negative values sink the feet slightly into the floor).")]
    public float floorOffset = 0f;

    // ---- Runtime state ----

    private GameObject _avatarRoot;
    private Animator   _avatarAnimator;
    private Transform  _cameraTransform;

    // ---- Lifecycle ----

    /// <summary>Finds the XR rig root (if not assigned) and caches the main camera.</summary>
    private void Awake()
    {
        if (xrRigRoot == null)
            xrRigRoot = FindXRRigRoot();

        _cameraTransform = Camera.main != null ? Camera.main.transform : null;
    }

    /// <summary>Loads the preset prefab if no override is assigned, then attaches the avatar.</summary>
    private void Start()
    {
        // Load from the preset if no explicit prefab was assigned.
        if (avatarPrefab == null)
        {
            string resourcePath = PresetToResourcePath(avatarPreset);
            avatarPrefab = Resources.Load<GameObject>(resourcePath);
            if (avatarPrefab == null)
                Debug.LogWarning($"[PlayerAvatarController] Could not find '{resourcePath}' in Resources. " +
                                 "Check that the asset is imported and under a Resources folder.");
        }

        if (avatarPrefab != null && _avatarRoot == null)
            AttachAvatar(avatarPrefab);
    }

    /// <summary>Resources path of the body prefab for a preset.</summary>
    private static string PresetToResourcePath(AvatarPreset preset) => preset switch
    {
        AvatarPreset.Female     => "Prefabs/Human_BasicMotionsDummy_F",
        AvatarPreset.Patient_01 => "Medical_Pack/Characters/Prefabs/Patient_01_Body",
        AvatarPreset.Patient_02 => "Medical_Pack/Characters/Prefabs/Patient_02_Body",
        AvatarPreset.Patient_03 => "Medical_Pack/Characters/Prefabs/Patient_03_Body",
        AvatarPreset.Patient_04 => "Medical_Pack/Characters/Prefabs/Patient_04_Body",
        AvatarPreset.Patient_05 => "Medical_Pack/Characters/Prefabs/Patient_05_Body",
        AvatarPreset.Patient_06 => "Medical_Pack/Characters/Prefabs/Patient_06_Body",
        _                       => "Prefabs/Human_BasicMotionsDummy_M",  // Male + default
    };

    /// <summary>
    /// Resources paths of the extra part prefabs (gown, face, hair, slippers) for an
    /// ithappy patient preset; empty for the Kevin Iglesias dummies.
    /// </summary>
    private static string[] PresetToClothingPaths(AvatarPreset preset)
    {
        // Kevin Iglesias dummies have all geometry in one prefab, with no separate parts.
        if (preset == AvatarPreset.Male || preset == AvatarPreset.Female)
            return System.Array.Empty<string>();

        // ithappy patient presets: Coat (gown), Face, Hair, Slippers.
        string n = preset.ToString().Replace("_", "_");  // e.g. "Patient_01"
        string root = "Medical_Pack/Characters/Prefabs/";
        return new[]
        {
            root + n + "_Coat",
            root + n + "_Face",
            root + n + "_Hair",
            root + n + "_Slippers",
        };
    }

    /// <summary>Builds a bone-name to Transform map from a character root's hierarchy (first occurrence wins).</summary>
    private static System.Collections.Generic.Dictionary<string, Transform> BuildBoneMap(GameObject root)
    {
        var map = new System.Collections.Generic.Dictionary<string, Transform>();
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
            if (!map.ContainsKey(t.name)) map[t.name] = t;
        return map;
    }

    /// <summary>
    /// Rebinds the SkinnedMeshRenderers on <paramref name="part"/> to bones of the same
    /// name in <paramref name="boneMap"/>; bones without a match are kept. Build the map
    /// with <see cref="BuildBoneMap"/> on the body root before parenting parts to it.
    /// </summary>
    private static void RebindSkinnedMeshes(
        GameObject part,
        System.Collections.Generic.Dictionary<string, Transform> boneMap)
    {
        foreach (var smr in part.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            var newBones = new Transform[smr.bones.Length];
            for (int i = 0; i < smr.bones.Length; i++)
            {
                if (smr.bones[i] != null && boneMap.TryGetValue(smr.bones[i].name, out var mapped))
                    newBones[i] = mapped;
                else
                    newBones[i] = smr.bones[i];
            }
            smr.bones = newBones;

            if (smr.rootBone != null && boneMap.TryGetValue(smr.rootBone.name, out var rootMapped))
                smr.rootBone = rootMapped;
        }
    }

    /// <summary>Moves the avatar under the camera at floor height and matches the camera's yaw.</summary>
    private void LateUpdate()
    {
        if (_avatarRoot == null) return;

        if (_cameraTransform == null && Camera.main != null)
            _cameraTransform = Camera.main.transform;

        if (_cameraTransform == null) return;

        // Use the camera's X/Z position so the avatar follows whether the player moves
        // via VR locomotion (the rig root moves) or keyboard controls (the camera moves).
        // Y comes from the XR rig root (floor level) so the avatar stays grounded.
        float floorY = floorOffset;
        if (xrRigRoot != null)
            floorY = xrRigRoot.position.y + floorOffset;
        else
            xrRigRoot = FindXRRigRoot(); // Keep trying until found.

        Vector3 camPos  = _cameraTransform.position;
        _avatarRoot.transform.position = new Vector3(camPos.x, floorY, camPos.z);

        // Match the camera's horizontal facing (yaw only).
        float yaw = _cameraTransform.eulerAngles.y;
        _avatarRoot.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
    }

    // ---- Public API ----

    /// <summary>
    /// Spawns (or replaces) the player avatar from <paramref name="prefab"/>: attaches the
    /// clothing parts of the current <see cref="avatarPreset"/>, hides head renderers (or
    /// all renderers), and starts the idle animation.
    /// </summary>
    /// <returns>The new avatar root (named "PlayerAvatar"), or null if <paramref name="prefab"/> is null.</returns>
    public GameObject AttachAvatar(GameObject prefab)
    {
        if (prefab == null)
        {
            Debug.LogWarning("[PlayerAvatarController] AttachAvatar: prefab is null.");
            return null;
        }

        // Destroy the previous avatar.
        if (_avatarRoot != null)
            Destroy(_avatarRoot);

        Vector3 spawnPos = xrRigRoot != null ? xrRigRoot.position : Vector3.zero;
        _avatarRoot = Instantiate(prefab, spawnPos, Quaternion.identity);
        _avatarRoot.name = "PlayerAvatar";

        // Tag as "Player" so PlayerTouchHighlighter.IsPlayerPart() filters it out and the
        // player's own body is never detected as a touchable character.
        _avatarRoot.tag = "Player";

        // Build bone map from the body skeleton BEFORE adding any clothing children,
        // so clothing parts don't contaminate each other's bone lookups.
        var boneMap = BuildBoneMap(_avatarRoot);

        foreach (var partPath in PresetToClothingPaths(avatarPreset))
        {
            var partPrefab = Resources.Load<GameObject>(partPath);
            if (partPrefab == null)
            {
                Debug.LogWarning($"[PlayerAvatarController] Clothing part not found: {partPath}");
                continue;
            }
            var part = Instantiate(partPrefab, _avatarRoot.transform);
            part.transform.localPosition = Vector3.zero;
            part.transform.localRotation = Quaternion.identity;
            part.transform.localScale    = Vector3.one;
            RebindSkinnedMeshes(part, boneMap);
        }

        if (hideFromPlayer)
            HideAllRenderers(_avatarRoot);
        else
            HideHeadRenderers(_avatarRoot);

        _avatarAnimator = FindPrimaryAnimator(_avatarRoot);

        // Start in idle so the character stands naturally.
        var idleCtrl = Resources.Load<RuntimeAnimatorController>("AnimatorControllers/HumanM@Idles");
        if (idleCtrl != null && _avatarAnimator != null)
        {
            _avatarAnimator.runtimeAnimatorController = idleCtrl;
            _avatarAnimator.Update(0f);
        }

        Debug.Log($"[PlayerAvatarController] Avatar attached: {prefab.name}  animator={(_avatarAnimator != null ? _avatarAnimator.name : "none")}");
        return _avatarRoot;
    }

    /// <summary>Returns the current avatar GameObject, or null if none is attached.</summary>
    public GameObject GetAvatarRoot() => _avatarRoot;

    /// <summary>Returns the avatar's primary Animator, or null if none is attached.</summary>
    public Animator GetAnimator() => _avatarAnimator;

    // ---- Renderer hiding ----

    /// <summary>Disables every renderer on the avatar so the player cannot see their own body.</summary>
    private void HideAllRenderers(GameObject root)
    {
        if (root == null) return;
        var renderers = root.GetComponentsInChildren<Renderer>(true);
        foreach (var r in renderers)
            r.enabled = false;
        Debug.Log($"[PlayerAvatarController] Hid all {renderers.Length} renderer(s) on {root.name} (player-invisible mode).");
    }

    /// <summary>
    /// Disables only renderers whose GameObject name contains a head-related keyword
    /// (head, face, hair, hat, beard, glasses, mask, helmet). Used when
    /// <see cref="hideFromPlayer"/> is false.
    /// </summary>
    private void HideHeadRenderers(GameObject root)
    {
        string[] headKeywords = { "head", "face", "hair", "hat", "beard", "glasses", "mask", "helmet" };
        if (root == null) return;
        var renderers = root.GetComponentsInChildren<Renderer>(true);
        int hidden = 0;
        foreach (var r in renderers)
        {
            string lowerName = r.gameObject.name.ToLowerInvariant();
            foreach (var kw in headKeywords)
            {
                if (lowerName.Contains(kw))
                {
                    r.enabled = false;
                    hidden++;
                    break;
                }
            }
        }
        Debug.Log($"[PlayerAvatarController] Hid {hidden} head renderer(s) on {root.name}.");
    }

    // ---- Helpers ----

    /// <summary>
    /// Finds the primary (humanoid) Animator on a character root, preferring ones with
    /// a human Avatar, more skinned meshes, and a shallower hierarchy depth.
    /// </summary>
    internal static Animator FindPrimaryAnimator(GameObject root)
    {
        if (root == null) return null;
        var animators = root.GetComponentsInChildren<Animator>(true);
        if (animators == null || animators.Length == 0) return null;
        if (animators.Length == 1) return animators[0];

        Animator best = null;
        int bestScore = int.MinValue;
        foreach (var a in animators)
        {
            if (a == null) continue;
            int score = 0;
            if (a.avatar != null) score += 100;
            if (a.avatar != null && a.avatar.isHuman) score += 100;
            score += a.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length * 10;
            score -= GetDepth(a.transform);
            if (score > bestScore) { bestScore = score; best = a; }
        }
        return best;
    }

    /// <summary>Returns the number of ancestors of <paramref name="t"/>.</summary>
    private static int GetDepth(Transform t)
    {
        int d = 0;
        while (t != null && t.parent != null) { d++; t = t.parent; }
        return d;
    }

    /// <summary>
    /// Locates the XR rig root: the first object tagged "Player", then a root object
    /// whose name contains a known rig name (e.g. "XR Origin", "Camera Rig"), then the
    /// Main Camera's root. Also used by <see cref="EnvironmentAgent"/>.
    /// </summary>
    internal static Transform FindXRRigRoot()
    {
        // 1. Tag.
        var tagged = GameObject.FindWithTag("Player");
        if (tagged != null) return tagged.transform;

        // 2. Partial name search across all root GameObjects.
        string[] nameHints = { "xr origin", "xrorigin", "camera rig", "camerarig", "xr rig", "xrrig" };
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go.transform.parent != null) continue; // Only root objects.
            string lower = go.name.ToLowerInvariant();
            foreach (var hint in nameHints)
                if (lower.Contains(hint)) return go.transform;
        }

        // 3. Camera root fallback.
        if (Camera.main != null) return Camera.main.transform.root;
        return null;
    }
}
