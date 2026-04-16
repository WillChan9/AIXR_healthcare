using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

/// <summary>
/// Listens for XR interaction events (hover, grab, release, activate) on every
/// <see cref="XRBaseInteractable"/> in the scene and writes them to a JSON file
/// that the Python Agent reads during Phase 2.
///
/// Attach to the same GameObject as EnvironmentAgent / AgentNetworkBridge.
///
/// Output file: <c>interaction_events.json</c> next to <c>unity_commands.json</c>.
/// The Python agent marks events as <c>"processed": true</c>; this script only
/// appends new events and never touches processed ones.
/// </summary>
public class InteractionEventReporter : MonoBehaviour
{
    [Header("Output path (relative to Assets/)")]
    public string eventsFileRelativePath = "Scripts/interaction_events.json";

    [Tooltip("Seconds between scanning for new interactables that lack listeners.")]
    public float rescanInterval = 2f;

    [Tooltip("Maximum events kept in file (oldest are trimmed).")]
    public int maxEvents = 200;

    // --- runtime state -------------------------------------------------------
    private string _filePath;
    private List<InteractionEvent> _events = new List<InteractionEvent>();
    private readonly HashSet<int> _subscribedIds = new HashSet<int>();
    private float _nextRescan;

    // Reference to the XR player rig (auto-found)
    private Transform _playerHead;

    // ========== Data model ====================================================

    [Serializable]
    private class InteractionEvent
    {
        public string id;
        public double timestamp;
        public string type;         // "hover_enter", "hover_exit", "grab", "release", "activate"
        public string objectName;
        public Vec3 objectPosition;
        public Vec3 playerPosition;
        public Vec3 playerLookDir;
        public bool processed;
    }

    [Serializable]
    private class Vec3
    {
        public float x, y, z;
        public Vec3(Vector3 v) { x = v.x; y = v.y; z = v.z; }
    }

    [Serializable]
    private class EventList
    {
        public InteractionEvent[] events;
    }

    // ========== Lifecycle =====================================================

    private void Awake()
    {
        _filePath = Path.Combine(Application.dataPath, eventsFileRelativePath);
        LoadExisting();
        FindPlayerHead();
    }

    private void Update()
    {
        if (Time.time >= _nextRescan)
        {
            _nextRescan = Time.time + rescanInterval;
            SubscribeToNewInteractables();
        }
    }

    // ========== Subscribe to XR events ========================================

    private void SubscribeToNewInteractables()
    {
        var interactables = FindObjectsByType<XRBaseInteractable>(FindObjectsSortMode.None);
        int newlySubscribed = 0;
        foreach (var ia in interactables)
        {
            int iid = ia.GetInstanceID();
            if (_subscribedIds.Contains(iid)) continue;
            _subscribedIds.Add(iid);
            newlySubscribed++;

            ia.hoverEntered.AddListener(args => RecordEvent("hover_enter", ia.gameObject));
            ia.hoverExited.AddListener(args  => RecordEvent("hover_exit",  ia.gameObject));
            ia.selectEntered.AddListener(args => RecordEvent("grab",        ia.gameObject));
            ia.selectExited.AddListener(args  => RecordEvent("release",     ia.gameObject));
            ia.activated.AddListener(args     => RecordEvent("activate",    ia.gameObject));
        }
        if (newlySubscribed > 0)
        {
            Debug.Log($"[InteractionEventReporter] Subscribed to {newlySubscribed} new interactables (total tracked: {_subscribedIds.Count})");
        }
    }

    // ========== Record + persist ==============================================

    public void RecordEvent(string eventType, GameObject target)
    {
        var evt = new InteractionEvent
        {
            id             = $"evt_{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}_{_events.Count}",
            timestamp      = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            type           = eventType,
            objectName     = CleanName(target.name),
            objectPosition = new Vec3(target.transform.position),
            playerPosition = new Vec3(GetPlayerPosition()),
            playerLookDir  = new Vec3(GetPlayerLookDir()),
            processed      = false
        };

        _events.Add(evt);
        Debug.Log($"[InteractionEvent] {evt.type} -> {evt.objectName}");

        // Trim oldest events if we exceed the cap
        while (_events.Count > maxEvents)
            _events.RemoveAt(0);

        WriteToDisk();
    }

    private void WriteToDisk()
    {
        try
        {
            string dir = Path.GetDirectoryName(_filePath);
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);

            // Manual JSON serialisation (JsonUtility doesn't handle List<T> at top level)
            var sb = new System.Text.StringBuilder();
            sb.Append("[\n");
            for (int i = 0; i < _events.Count; i++)
            {
                if (i > 0) sb.Append(",\n");
                sb.Append(JsonUtility.ToJson(_events[i], true));
            }
            sb.Append("\n]");
            File.WriteAllText(_filePath, sb.ToString());
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[InteractionEventReporter] Write failed: {ex.Message}");
        }
    }

    /// <summary>
    /// Load existing events from disk so we don't clobber events the Python
    /// agent hasn't processed yet.
    /// </summary>
    private void LoadExisting()
    {
        if (!File.Exists(_filePath)) return;
        try
        {
            string json = File.ReadAllText(_filePath);
            if (string.IsNullOrWhiteSpace(json) || json.Trim() == "[]") return;
            var wrapped = "{\"events\":" + json + "}";
            var list = JsonUtility.FromJson<EventList>(wrapped);
            if (list?.events != null)
                _events = new List<InteractionEvent>(list.events);
        }
        catch
        {
            // Corrupted file — start fresh
            _events = new List<InteractionEvent>();
        }
    }

    // ========== Player tracking ===============================================

    private void FindPlayerHead()
    {
        // Try to find the XR camera (Main Camera under XR Origin)
        var cam = Camera.main;
        if (cam != null)
            _playerHead = cam.transform;
    }

    private Vector3 GetPlayerPosition()
    {
        if (_playerHead == null) FindPlayerHead();
        return _playerHead != null ? _playerHead.position : Vector3.zero;
    }

    private Vector3 GetPlayerLookDir()
    {
        if (_playerHead == null) FindPlayerHead();
        return _playerHead != null ? _playerHead.forward : Vector3.forward;
    }

    // ========== Utility =======================================================

    /// <summary>Strip "(Clone)" suffix that Unity adds on Instantiate.</summary>
    private static string CleanName(string name)
    {
        if (string.IsNullOrEmpty(name)) return name;
        int idx = name.IndexOf("(Clone)", StringComparison.Ordinal);
        return idx >= 0 ? name.Substring(0, idx).Trim() : name;
    }
}
