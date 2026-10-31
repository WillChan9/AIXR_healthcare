using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// Polls Physics.OverlapSphere each frame around the player (camera rig / head)
/// and highlights any scene object the player is physically touching.
///
/// Does NOT rely on OnTriggerEnter — XR rigs use CharacterController and
/// don't generate trigger callbacks reliably.
///
/// Setup:
///   1. Attach this script to any convenient GameObject (e.g. AI_Manager).
///   2. Drag your XR Origin / Camera Rig (or Main Camera) into "Player Transform".
///   3. Tune "Touch Radius" to match how close the player needs to be.
/// </summary>
public class PlayerTouchHighlighter : MonoBehaviour
{
    [Header("Player Reference")]
    [Tooltip("Drag your XR Origin, Camera Rig, or Main Camera here.")]
    public Transform playerTransform;

    [Header("Detection")]
    [Tooltip("Radius (metres) around the player that counts as a 'touch'.")]
    public float touchRadius = 0.5f;

    [Tooltip("LayerMask to restrict which objects are checked. Leave as 'Everything' to check all.")]
    public LayerMask touchLayers = ~0;

    [Header("Highlight")]
    [Tooltip("Colour flashed on touched scene objects.")]
    public Color touchColor = new Color(1.0f, 0.7f, 0.1f);

    [Tooltip("Colour flashed on touched characters (NPCs with an Animator).")]
    public Color characterTouchColor = new Color(0.2f, 0.8f, 1.0f);

    [Tooltip("Seconds the highlight lasts.")]
    public float highlightDuration = 1.5f;

    [Tooltip("Minimum seconds between highlights on the SAME object (prevents spam).")]
    public float cooldownPerObject = 2.0f;

    // ---- Runtime ----

    private EnvironmentAgent _envAgent;
    private InteractionEventReporter _eventReporter;

    // instanceID → last-highlighted timestamp
    private readonly Dictionary<int, float> _lastHighlight = new Dictionary<int, float>();

    // ---- Lifecycle ----

    private void Awake()
    {
        // Stays disabled until EnvironmentAgent.SetPhase(2) enables it
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

    private void Update()
    {
        if (playerTransform == null) return;

        // Lazy-resolve reporter in case it wasn't ready during Awake()
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

            // Log the touch event so the Python agent can process it
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

    private static bool IsPrimitive(GameObject go)
    {
        var mf = go.GetComponent<MeshFilter>();
        return mf != null && mf.sharedMesh != null && PrimitiveMeshNames.Contains(mf.sharedMesh.name);
    }

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

    // ---- Gizmo: visualise touch sphere in Scene view ----

    private void OnDrawGizmosSelected()
    {
        if (playerTransform == null) return;
        Gizmos.color = new Color(touchColor.r, touchColor.g, touchColor.b, 0.25f);
        Gizmos.DrawSphere(playerTransform.position, touchRadius);
        Gizmos.color = touchColor;
        Gizmos.DrawWireSphere(playerTransform.position, touchRadius);
        // Second ring shows character highlight colour for reference
        Gizmos.color = characterTouchColor;
        Gizmos.DrawWireSphere(playerTransform.position, touchRadius * 0.9f);
    }
}
