using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Attaches a visible humanoid avatar to the XR player rig so the player can
/// look down and see their own body, and so the agent can drive player-body
/// animations (sit, lie down, etc.) through the existing PlayHumanAnimation*
/// pipeline.
///
/// Behaviour
/// ─────────
/// • Instantiates an avatar prefab and keeps it parented to the scene root
///   (NOT the XR rig) so it doesn't jitter with tracking updates.
/// • Every LateUpdate the avatar's XZ position tracks Camera.main (works for
///   both VR locomotion and WASD), Y stays at floor level from the XR rig root,
///   and horizontal rotation matches the camera's yaw.
/// • Head/face/hair renderers are disabled so the player doesn't see the inside
///   of their own skull through the camera.
///
/// Attach this component to AI_Manager (or any persistent GameObject).
/// Assign avatarPrefab in the Inspector, or call AttachAvatar() at runtime.
/// </summary>
public class PlayerAvatarController : MonoBehaviour
{
    /// <summary>Which model to use as the player body.</summary>
    public enum AvatarPreset
    {
        // Kevin Iglesias base dummies (grey tracksuit)
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

    // ── Inspector ─────────────────────────────────────────────────────────────

    [Tooltip("Player body model. KI Male/Female = grey tracksuit dummies. Patient_01–06 = ithappy hospital gown characters. Ignored if avatarPrefab is assigned directly.")]
    public AvatarPreset avatarPreset = AvatarPreset.Patient_01;

    [Tooltip("Override: drag in any prefab to use it instead of the preset above.")]
    public GameObject avatarPrefab;

    [Tooltip("The XR rig root transform. Auto-found if left empty (searches by tag 'Player', then by name).")]
    public Transform xrRigRoot;

    [Tooltip("When true, all renderers on the player avatar are disabled — the body exists for animation/physics but is invisible from the player's camera.")]
    public bool hideFromPlayer = false;

    [Tooltip("Vertical offset applied to the avatar's root relative to the XR rig floor position (negative = sink into floor slightly to hide feet seam).")]
    public float floorOffset = 0f;

    // ── Runtime state ─────────────────────────────────────────────────────────

    private GameObject _avatarRoot;
    private Animator   _avatarAnimator;
    private Transform  _cameraTransform;

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    private void Awake()
    {
        if (xrRigRoot == null)
            xrRigRoot = FindXRRigRoot();

        _cameraTransform = Camera.main != null ? Camera.main.transform : null;
    }

    private void Start()
    {
        // Load from preset if no explicit prefab was dragged in
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

    private static string[] PresetToClothingPaths(AvatarPreset preset)
    {
        // KI dummies already have all geometry in one prefab — no separate parts.
        if (preset == AvatarPreset.Male || preset == AvatarPreset.Female)
            return System.Array.Empty<string>();

        // ithappy patient presets: Coat (gown), Face, Hair, Slippers
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

    /// <summary>Build a bone name → Transform map from a character root's hierarchy.</summary>
    private static System.Collections.Generic.Dictionary<string, Transform> BuildBoneMap(GameObject root)
    {
        var map = new System.Collections.Generic.Dictionary<string, Transform>();
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
            if (!map.ContainsKey(t.name)) map[t.name] = t;
        return map;
    }

    /// <summary>
    /// Rebind SkinnedMeshRenderers on <paramref name="part"/> to a pre-built bone map.
    /// Call BuildBoneMap() on the body root BEFORE parenting clothing parts to it.
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

    private void LateUpdate()
    {
        if (_avatarRoot == null) return;

        if (_cameraTransform == null && Camera.main != null)
            _cameraTransform = Camera.main.transform;

        if (_cameraTransform == null) return;

        // Use the camera's XZ position so the avatar follows regardless of whether
        // the player is moving via VR locomotion (rig root moves) or WASD (camera moves).
        // Y comes from the XR rig root (floor level) so the avatar stays grounded.
        float floorY = floorOffset;
        if (xrRigRoot != null)
            floorY = xrRigRoot.position.y + floorOffset;
        else
            xrRigRoot = FindXRRigRoot(); // keep trying until found

        Vector3 camPos  = _cameraTransform.position;
        _avatarRoot.transform.position = new Vector3(camPos.x, floorY, camPos.z);

        // Align avatar horizontal facing to camera (Y-axis only)
        float yaw = _cameraTransform.eulerAngles.y;
        _avatarRoot.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /// <summary>
    /// Spawn (or replace) the player avatar from <paramref name="prefab"/>.
    /// Returns the new avatar root GameObject so EnvironmentAgent can register it.
    /// </summary>
    public GameObject AttachAvatar(GameObject prefab)
    {
        if (prefab == null)
        {
            Debug.LogWarning("[PlayerAvatarController] AttachAvatar: prefab is null.");
            return null;
        }

        // Destroy previous avatar
        if (_avatarRoot != null)
            Destroy(_avatarRoot);

        Vector3 spawnPos = xrRigRoot != null ? xrRigRoot.position : Vector3.zero;
        _avatarRoot = Instantiate(prefab, spawnPos, Quaternion.identity);
        _avatarRoot.name = "PlayerAvatar";

        // Tag as "Player" so PlayerTouchHighlighter.IsPlayerPart() filters it out —
        // prevents the player's own body from being detected as a touchable character.
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

        // Boot into idle so the character stands naturally
        var idleCtrl = Resources.Load<RuntimeAnimatorController>("AnimatorControllers/HumanM@Idles");
        if (idleCtrl != null && _avatarAnimator != null)
        {
            _avatarAnimator.runtimeAnimatorController = idleCtrl;
            _avatarAnimator.Update(0f);
        }

        Debug.Log($"[PlayerAvatarController] Avatar attached: {prefab.name}  animator={(_avatarAnimator != null ? _avatarAnimator.name : "none")}");
        return _avatarRoot;
    }

    /// <summary>Returns the live avatar GameObject (null if not attached yet).</summary>
    public GameObject GetAvatarRoot() => _avatarRoot;

    /// <summary>Returns the avatar's primary Animator (null if not attached yet).</summary>
    public Animator GetAnimator() => _avatarAnimator;

    // ── Renderer hiding ───────────────────────────────────────────────────────

    /// <summary>Disable every renderer on the avatar so the player cannot see their own body.</summary>
    private void HideAllRenderers(GameObject root)
    {
        if (root == null) return;
        var renderers = root.GetComponentsInChildren<Renderer>(true);
        foreach (var r in renderers)
            r.enabled = false;
        Debug.Log($"[PlayerAvatarController] Hid all {renderers.Length} renderer(s) on {root.name} (player-invisible mode).");
    }

    /// <summary>Disable only head/hair/face renderers (legacy — used when hideFromPlayer is false).</summary>
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

    // ── Helpers ───────────────────────────────────────────────────────────────

    /// <summary>
    /// Find the primary (humanoid) Animator on a character root, preferring
    /// ones with a human Avatar and more skinned meshes.
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

    private static int GetDepth(Transform t)
    {
        int d = 0;
        while (t != null && t.parent != null) { d++; t = t.parent; }
        return d;
    }

    /// <summary>
    /// Locate the XR rig root: tag "Player" → known names (partial match) → camera root.
    /// </summary>
    internal static Transform FindXRRigRoot()
    {
        // 1. Tag
        var tagged = GameObject.FindWithTag("Player");
        if (tagged != null) return tagged.transform;

        // 2. Partial name search across all root GameObjects
        string[] nameHints = { "xr origin", "xrorigin", "camera rig", "camerarig", "xr rig", "xrrig" };
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go.transform.parent != null) continue; // only root objects
            string lower = go.name.ToLowerInvariant();
            foreach (var hint in nameHints)
                if (lower.Contains(hint)) return go.transform;
        }

        // 3. Camera root fallback
        if (Camera.main != null) return Camera.main.transform.root;
        return null;
    }
}
