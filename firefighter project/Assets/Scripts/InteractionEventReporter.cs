using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

/// <summary>
/// Records player interactions to a JSON file that the Python agent reads during
/// Phase 2 (player interaction).
///
/// Listens for XR events (hover enter/exit, grab, release, activate) on every
/// <see cref="XRBaseInteractable"/> in the scene, rescanning periodically to pick up
/// objects spawned at runtime. <see cref="PlayerTouchHighlighter"/> also reports
/// proximity "touch" events through <see cref="RecordEvent"/>.
///
/// Output: <c>Assets/{eventsFileRelativePath}</c> (default
/// <c>Assets/Scripts/interaction_events.json</c>, next to <c>unity_commands.json</c>),
/// a JSON array of events with <c>id</c>, <c>timestamp</c> (Unix seconds), <c>type</c>,
/// <c>objectName</c>, <c>objectPosition</c>, <c>playerPosition</c>, <c>playerLookDir</c>
/// and <c>processed</c>. Existing events are loaded once at startup; afterwards the
/// whole file is rewritten from the in-memory list on every new event, keeping at most
/// <see cref="maxEvents"/> entries. The Python agent sets <c>"processed": true</c> on
/// events it has handled.
///
/// Setup: added automatically to the EnvironmentAgent GameObject (AI_Manager) through
/// <c>RequireComponent</c>.
/// </summary>
public class InteractionEventReporter : MonoBehaviour
{
    /// <summary>Output file path, relative to the Assets folder.</summary>
    [Header("Output path (relative to Assets/)")]
    public string eventsFileRelativePath = "Scripts/interaction_events.json";

    /// <summary>Seconds between scans for interactables that have no listeners yet.</summary>
    [Tooltip("Seconds between scans for new interactables that have no listeners yet.")]
    public float rescanInterval = 2f;

    /// <summary>Maximum number of events kept in the file; the oldest are dropped first.</summary>
    [Tooltip("Maximum number of events kept in the file (oldest are dropped first).")]
    public int maxEvents = 200;

    // ---- Runtime state ----

    private string _filePath;
    private List<InteractionEvent> _events = new List<InteractionEvent>();
    private readonly HashSet<int> _subscribedIds = new HashSet<int>();
    private float _nextRescan;

    // Player head (Main Camera), used for player position and look direction.
    private Transform _playerHead;

    // ---- Data model ----

    /// <summary>One interaction record. Field names are part of the JSON format read by Python.</summary>
    [Serializable]
    private class InteractionEvent
    {
        public string id;
        public double timestamp;
        public string type;         // "hover_enter", "hover_exit", "grab", "release", "activate", "touch"
        public string objectName;
        public Vec3 objectPosition;
        public Vec3 playerPosition;
        public Vec3 playerLookDir;
        public bool processed;
    }

    /// <summary>Serializable 3D vector, written as <c>{"x":..,"y":..,"z":..}</c>.</summary>
    [Serializable]
    private class Vec3
    {
        public float x, y, z;
        public Vec3(Vector3 v) { x = v.x; y = v.y; z = v.z; }
    }

    /// <summary>Wrapper that lets JsonUtility parse the top-level JSON array.</summary>
    [Serializable]
    private class EventList
    {
        public InteractionEvent[] events;
    }

    // ---- Lifecycle ----

    /// <summary>Resolves the output path, loads existing events and finds the player head.</summary>
    private void Awake()
    {
        _filePath = Path.Combine(Application.dataPath, eventsFileRelativePath);
        LoadExisting();
        FindPlayerHead();
    }

    /// <summary>Rescans for new interactables every <see cref="rescanInterval"/> seconds.</summary>
    private void Update()
    {
        if (Time.time >= _nextRescan)
        {
            _nextRescan = Time.time + rescanInterval;
            SubscribeToNewInteractables();
        }
    }

    // ---- Subscribe to XR events ----

    /// <summary>Adds event listeners to every XRBaseInteractable that has not been subscribed yet.</summary>
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

    // ---- Record and persist ----

    /// <summary>
    /// Appends an event for <paramref name="target"/> (with the current object and
    /// player transforms), trims the list to <see cref="maxEvents"/> and writes the file.
    /// </summary>
    /// <param name="eventType">Event type, e.g. "grab" or "touch".</param>
    /// <param name="target">Object the player interacted with; "(Clone)" is stripped from its name.</param>
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

        // Drop the oldest events once the cap is exceeded.
        while (_events.Count > maxEvents)
            _events.RemoveAt(0);

        WriteToDisk();
    }

    /// <summary>Rewrites the events file from the in-memory list as a pretty-printed JSON array.</summary>
    private void WriteToDisk()
    {
        try
        {
            string dir = Path.GetDirectoryName(_filePath);
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);

            // Build the array by hand: JsonUtility cannot serialize a top-level array or List<T>.
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
    /// Loads existing events from disk at startup so events the Python agent has not
    /// processed yet are not lost when the file is next rewritten.
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
            // Unreadable or corrupted file: start with an empty list.
            _events = new List<InteractionEvent>();
        }
    }

    // ---- Player tracking ----

    /// <summary>Uses the Main Camera (the XR camera under the XR Origin) as the player's head.</summary>
    private void FindPlayerHead()
    {
        var cam = Camera.main;
        if (cam != null)
            _playerHead = cam.transform;
    }

    /// <summary>Player head position, or the origin if no camera is available.</summary>
    private Vector3 GetPlayerPosition()
    {
        if (_playerHead == null) FindPlayerHead();
        return _playerHead != null ? _playerHead.position : Vector3.zero;
    }

    /// <summary>Player head forward direction, or world forward if no camera is available.</summary>
    private Vector3 GetPlayerLookDir()
    {
        if (_playerHead == null) FindPlayerHead();
        return _playerHead != null ? _playerHead.forward : Vector3.forward;
    }

    // ---- Helpers ----

    /// <summary>Strips the "(Clone)" suffix that Unity adds on Instantiate.</summary>
    private static string CleanName(string name)
    {
        if (string.IsNullOrEmpty(name)) return name;
        int idx = name.IndexOf("(Clone)", StringComparison.Ordinal);
        return idx >= 0 ? name.Substring(0, idx).Trim() : name;
    }
}
