using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Phase 2 proximity "touch" detection. Every frame, checks a sphere of
/// <see cref="touchRadius"/> around <see cref="playerTransform"/> with
/// <c>Physics.OverlapSphere</c>, highlights each object or character the player is
/// touching, and logs a "touch" event through <see cref="InteractionEventReporter"/>
/// so the Python agent can react.
/// </summary>
/// <remarks>
/// OnTriggerEnter is deliberately not used: XR rigs move with a CharacterController
/// and do not generate trigger callbacks reliably.
///
/// Objects that belong to the player (tagged "Player" or under
/// <see cref="playerTransform"/>) and bare primitive meshes (walls, floor) are ignored.
/// Each object is reported at most once per <see cref="cooldownPerObject"/> seconds.
///
/// The component disables itself in <c>Awake</c> and is enabled by
/// <c>EnvironmentAgent.SetPhase(2)</c>, which also adds it to the EnvironmentAgent
/// GameObject if the scene does not contain one. To set it up manually instead:
/// <list type="number">
/// <item><description>Add it to any convenient GameObject (e.g. AI_Manager).</description></item>
/// <item><description>Assign the XR Origin / Camera Rig (or Main Camera) to Player Transform;
/// the Main Camera is used if left empty.</description></item>
/// <item><description>Tune Touch Radius to match how close the player needs to be.</description></item>
/// </list>
/// </remarks>
public class PlayerTouchHighlighter : MonoBehaviour
{
    [Header("Player Reference")]
    /// <summary>Center of the touch sphere. Defaults to the Main Camera if empty.</summary>
    [Tooltip("XR Origin, Camera Rig, or Main Camera. Defaults to the Main Camera if empty.")]
    public Transform playerTransform;

    [Header("Detection")]
    /// <summary>Radius in meters around the player that counts as a touch.</summary>
    [Tooltip("Radius (meters) around the player that counts as a 'touch'.")]
    public float touchRadius = 0.5f;

    /// <summary>Layers included in the overlap check.</summary>
    [Tooltip("Layers to check. Leave as 'Everything' to check all objects.")]
    public LayerMask touchLayers = ~0;

    [Header("Highlight")]
    /// <summary>Highlight color for touched objects.</summary>
    [Tooltip("Color flashed on touched scene objects.")]
    public Color touchColor = new Color(1.0f, 0.7f, 0.1f);

    /// <summary>Highlight color for touched characters.</summary>
    [Tooltip("Color flashed on touched characters (NPCs with an Animator).")]
    public Color characterTouchColor = new Color(0.2f, 0.8f, 1.0f);

    /// <summary>Seconds each highlight lasts.</summary>
    [Tooltip("Seconds the highlight lasts.")]
    public float highlightDuration = 1.5f;

    /// <summary>Minimum seconds between two touches reported for the same object.</summary>
    [Tooltip("Minimum seconds between highlights / touch events on the same object (prevents spam).")]
    public float cooldownPerObject = 2.0f;

    // ---- Runtime ----

    private EnvironmentAgent _envAgent;
    private InteractionEventReporter _eventReporter;

    // Instance ID -> time of the last highlight.
    private readonly Dictionary<int, float> _lastHighlight = new Dictionary<int, float>();

    // ---- Lifecycle ----

    /// <summary>Disables the component until Phase 2 and resolves references.</summary>
    private void Awake()
    {
        // Stays disabled until EnvironmentAgent.SetPhase(2) enables it.
        enabled = false;

        _envAgent = FindFirstObjectByType<EnvironmentAgent>();
        if (_envAgent == null)
            Debug.LogWarning("[PlayerTouchHighlighter] EnvironmentAgent not found — highlights disabled.");

        _eventReporter = FindFirstObjectByType<InteractionEventReporter>();
        if (_eventReporter == null)
            Debug.LogWarning("[PlayerTouchHighlighter] InteractionEventReporter not found — touch events will not be logged.");

        if (playerTransform == null && Camera.main != null)
        {
            playerTransform = Camera.main.transform;
            Debug.LogWarning($"[PlayerTouchHighlighter] playerTransform not set — defaulting to Main Camera ({playerTransform.name}). Assign manually in Inspector.");
        }

        if (playerTransform == null)
            Debug.LogError("[PlayerTouchHighlighter] No playerTransform assigned and no Main Camera found. Touch detection will not work.");
        else
            Debug.Log($"[PlayerTouchHighlighter] Registered. Watching '{playerTransform.name}' — waiting for Phase 2.");
    }

    // ---- Per-frame overlap check ----

    /// <summary>Detects overlapping objects and highlights / reports each one (subject to the cooldown).</summary>
    private void Update()
    {
        if (playerTransform == null) return;

        // Resolve the reporter lazily in case it did not exist yet during Awake().
        if (_eventReporter == null)
        {
            _eventReporter = FindFirstObjectByType<InteractionEventReporter>();
            if (_eventReporter != null)
                Debug.Log("[PlayerTouchHighlighter] InteractionEventReporter found — touch events will now be logged.");
        }

        Collider[] hits = Physics.OverlapSphere(playerTransform.position, touchRadius, touchLayers);

        foreach (var col in hits)
        {
            if (IsPlayerPart(col.gameObject)) continue;
            if (IsPrimitive(col.gameObject)) continue;

            GameObject propRoot = GetPropRoot(col.gameObject);
            int id = propRoot.GetInstanceID();

            float now = Time.time;
            if (_lastHighlight.TryGetValue(id, out float last) && now - last < cooldownPerObject)
                continue;

            _lastHighlight[id] = now;

            bool isCharacter = propRoot.GetComponentInChildren<Animator>() != null;
            string objName = propRoot.name.Replace("(Clone)", "").Trim();
            Debug.Log($"[PlayerTouchHighlighter] TOUCH → {objName} ({(isCharacter ? "character" : "object")})  (dist={Vector3.Distance(playerTransform.position, col.transform.position):F2}m)");

            if (_envAgent != null)
            {
                if (isCharacter)
                    _envAgent.HighlightObject(propRoot, characterTouchColor.r, characterTouchColor.g, characterTouchColor.b, highlightDuration);
                else
                    _envAgent.HighlightObject(objName, touchColor.r, touchColor.g, touchColor.b, highlightDuration);
            }

            // Log the touch event so the Python agent can process it.
            if (_eventReporter != null)
            {
                _eventReporter.RecordEvent("touch", propRoot);
                Debug.Log($"[PlayerTouchHighlighter] Logged touch event for '{objName}' → interaction_events.json");
            }
            else
                Debug.LogWarning("[PlayerTouchHighlighter] InteractionEventReporter still null — touch not logged to JSON.");
        }
    }

    // ---- Helpers ----

    private static readonly System.Collections.Generic.HashSet<string> PrimitiveMeshNames
        = new System.Collections.Generic.HashSet<string> { "Cube", "Sphere", "Capsule", "Cylinder", "Plane", "Quad" };

    /// <summary>
    /// True if the object renders a built-in primitive mesh (Cube, Sphere, ...), which
    /// in this project means room structure rather than a prop.
    /// </summary>
    private static bool IsPrimitive(GameObject go)
    {
        var mf = go.GetComponent<MeshFilter>();
        return mf != null && mf.sharedMesh != null && PrimitiveMeshNames.Contains(mf.sharedMesh.name);
    }

    /// <summary>True if the object is tagged "Player" or is <see cref="playerTransform"/> or one of its children.</summary>
    private bool IsPlayerPart(GameObject go)
    {
        if (go.CompareTag("Player")) return true;
        Transform t = go.transform;
        while (t != null)
        {
            if (t == playerTransform) return true;
            t = t.parent;
        }
        return false;
    }

    /// <summary>
    /// Returns the GameObject to highlight and report for a hit collider: the scene
    /// root for characters (anything with an Animator), otherwise the nearest object
    /// with a Renderer, walking up from the collider (excluding the scene root).
    /// </summary>
    private static GameObject GetPropRoot(GameObject go)
    {
        // Character prefabs (NPCs) have Animators. Walk to the scene root so the
        // full character is highlighted and the name lookup in HighlightObject works.
        GameObject root = go.transform.root.gameObject;
        if (root.GetComponentInChildren<Animator>() != null)
            return root;

        Transform t = go.transform;
        while (t.parent != null)
        {
            if (t.GetComponent<Renderer>() != null) return t.gameObject;
            t = t.parent;
        }
        return go;
    }

    // ---- Gizmos ----

    /// <summary>Draws the touch sphere in the Scene view when the component is selected.</summary>
    private void OnDrawGizmosSelected()
    {
        if (playerTransform == null) return;
        Gizmos.color = new Color(touchColor.r, touchColor.g, touchColor.b, 0.25f);
        Gizmos.DrawSphere(playerTransform.position, touchRadius);
        Gizmos.color = touchColor;
        Gizmos.DrawWireSphere(playerTransform.position, touchRadius);
        // Second ring shows the character highlight color for reference.
        Gizmos.color = characterTouchColor;
        Gizmos.DrawWireSphere(playerTransform.position, touchRadius * 0.9f);
    }
}
