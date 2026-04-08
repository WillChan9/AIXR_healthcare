using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// Bridge script for communicating with the external Python FastAPI server.
/// Unity acts as a client, periodically polling the Python /command endpoint
/// and forwarding received commands to EnvironmentAgent to modify the scene.
/// </summary>
public class AgentNetworkBridge : MonoBehaviour
{
    [Header("Python FastAPI server base URL, e.g. http://192.168.1.10:8000")]
    public string serverBaseUrl = "http://127.0.0.1:8000";

    [Header("Read commands from local JSON file instead of HTTP")]
    public bool useFileCommands = true;

    [Header("Relative to Assets/, default: Scripts/unity_commands.json")]
    public string commandsFileRelativePath = "Scripts/unity_commands.json";

    [Header("Polling interval (seconds)")]
    public float pollIntervalSeconds = 1.0f;

    [Header("Reference to EnvironmentAgent script")]
    public EnvironmentAgent environmentAgent;

    [System.Serializable]
    private class AgentCommand
    {
        public string id;
        public bool executed;
        public string action;

        // Common parameters shared by multiple actions
        public float hour;
        public bool enabled;
        public float density;

        public float r;
        public float g;
        public float b;

        public float gravityY;
        public float scale;

        public string objectName;
        public string humanName;
        public string controllerPath;
        // Legacy: keep for backwards compatibility with older JSON files.
        public string controllerPathInResources;
        public string stateName;
        public float crossFadeTime;
        public float x;
        public float y;
        public float z;
        public float rotationY;
        public float scaleX;
        public float scaleY;
        public float scaleZ;

        // Animation params
        public string paramName;
        public float value;
        public bool boolValue;

        // DebugLog
        public string message;

        // Highlight params
        public float duration;

        // Status flags (optional)
        public bool done;
    }

    [System.Serializable]
    private class AgentCommandList
    {
        public AgentCommand[] commands;
    }

    private void Awake()
    {
        if (environmentAgent == null)
        {
            environmentAgent = FindFirstObjectByType<EnvironmentAgent>();
        }
    }

    private void OnEnable()
    {
        StartCoroutine(PollLoop());
    }

    private IEnumerator PollLoop()
    {
        while (true)
        {
            yield return PollOnce();
            yield return new WaitForSeconds(pollIntervalSeconds);
        }
    }

    private IEnumerator PollOnce()
    {
        if (useFileCommands)
        {
            PollFromFile();
            yield break;
        }

        if (string.IsNullOrEmpty(serverBaseUrl))
            yield break;

        string url = serverBaseUrl.TrimEnd('/') + "/command";

        using (UnityWebRequest www = UnityWebRequest.Get(url))
        {
#if UNITY_2020_2_OR_NEWER
            yield return www.SendWebRequest();
#else
            yield return www.Send();
#endif

            if (www.result == UnityWebRequest.Result.ConnectionError ||
                www.result == UnityWebRequest.Result.ProtocolError)
            {
                // You may choose to log this, but avoid spamming every frame
                yield break;
            }

            var json = www.downloadHandler.text;
            if (string.IsNullOrEmpty(json) || json == "null")
                yield break;

            AgentCommand cmd = null;
            try
            {
                cmd = JsonUtility.FromJson<AgentCommand>(json);
            }
            catch
            {
                Debug.LogWarning("AgentNetworkBridge: Failed to parse JSON command: " + json);
            }

            if (cmd == null || string.IsNullOrEmpty(cmd.action) || environmentAgent == null)
                yield break;

            HandleCommand(cmd);
        }
    }

    private void PollFromFile()
    {
        var path = System.IO.Path.Combine(Application.dataPath, commandsFileRelativePath);
        if (!System.IO.File.Exists(path))
            return;

        string json = "";
        try
        {
            json = System.IO.File.ReadAllText(path);
        }
        catch
        {
            return;
        }

        if (string.IsNullOrWhiteSpace(json))
            return;

        AgentCommandList list = null;
        try
        {
            // File is a JSON array; wrap it so JsonUtility can parse it.
            var wrapped = "{\"commands\":" + json + "}";
            list = JsonUtility.FromJson<AgentCommandList>(wrapped);
        }
        catch
        {
            Debug.LogWarning("AgentNetworkBridge: Failed to parse JSON file: " + path);
            return;
        }

        if (list?.commands == null || list.commands.Length == 0 || environmentAgent == null)
            return;

        foreach (var cmd in list.commands)
        {
            if (cmd == null || string.IsNullOrEmpty(cmd.action))
                continue;
            if (cmd.executed)
                continue;
            Debug.Log($"[AgentBridge] Dispatching: {cmd.action} | object={cmd.objectName ?? cmd.humanName ?? "-"} | pos=({cmd.x:F2},{cmd.y:F2},{cmd.z:F2}) | rotY={cmd.rotationY:F0} | id={cmd.id ?? "?"}");
            HandleCommand(cmd);
            cmd.executed = true;
            cmd.done = true;
        }

        // Write back with done/executed flags so history is preserved.
        try
        {
            System.IO.File.WriteAllText(path, SerializeCommandArray(list.commands));
        }
        catch
        {
            // If we can't write back, commands may repeat; log once in Console if needed.
        }
    }

    private string SerializeCommandArray(AgentCommand[] commands)
    {
        if (commands == null || commands.Length == 0)
            return "[]";

        var sb = new System.Text.StringBuilder();
        sb.Append("[\n");
        for (int i = 0; i < commands.Length; i++)
        {
            if (i > 0) sb.Append(",\n");
            sb.Append(SerializeCommand(commands[i]));
        }
        sb.Append("\n]");
        return sb.ToString();
    }

    private string SerializeCommand(AgentCommand cmd)
    {
        var sb = new System.Text.StringBuilder();
        sb.Append("  {\n");
        bool first = true;

        void AddString(string key, string value)
        {
            if (string.IsNullOrEmpty(value)) return;
            if (!first) sb.Append(",\n");
            first = false;
            sb.Append($"    \"{key}\": \"{EscapeJson(value)}\"");
        }

        void AddBool(string key, bool value)
        {
            if (!first) sb.Append(",\n");
            first = false;
            sb.Append($"    \"{key}\": {(value ? "true" : "false")}");
        }

        void AddFloat(string key, float value)
        {
            if (!first) sb.Append(",\n");
            first = false;
            sb.Append($"    \"{key}\": {value.ToString(System.Globalization.CultureInfo.InvariantCulture)}");
        }

        AddString("id", cmd.id);
        AddBool("executed", cmd.executed);
        AddBool("done", cmd.done);
        AddString("action", cmd.action);

        switch (cmd.action)
        {
            case "SetTimeOfDay":
                AddFloat("hour", cmd.hour);
                break;
            case "SetFogEnabled":
                AddBool("enabled", cmd.enabled);
                break;
            case "SetFogDensity":
                AddFloat("density", cmd.density);
                break;
            case "SetLightIntensity":
                AddFloat("scale", cmd.scale);
                break;
            case "SetLightColor":
                AddFloat("r", cmd.r); AddFloat("g", cmd.g); AddFloat("b", cmd.b);
                break;
            case "SetAmbientColor":
                AddFloat("r", cmd.r); AddFloat("g", cmd.g); AddFloat("b", cmd.b);
                break;
            case "SetGravity":
                AddFloat("gravityY", cmd.gravityY);
                break;
            case "SetTimeScale":
                AddFloat("scale", cmd.scale);
                break;
            case "PlaceObject":
                AddString("objectName", cmd.objectName);
                AddFloat("x", cmd.x); AddFloat("y", cmd.y); AddFloat("z", cmd.z);
                AddFloat("rotationY", cmd.rotationY);
                break;
            case "CreateObjectByName":
                AddString("objectName", cmd.objectName);
                AddFloat("x", cmd.x); AddFloat("y", cmd.y); AddFloat("z", cmd.z);
                AddFloat("rotationY", cmd.rotationY);
                AddFloat("scaleX", cmd.scaleX); AddFloat("scaleY", cmd.scaleY); AddFloat("scaleZ", cmd.scaleZ);
                break;
            case "PlaceHuman":
                AddString("humanName", cmd.humanName ?? cmd.objectName);
                AddFloat("x", cmd.x); AddFloat("y", cmd.y); AddFloat("z", cmd.z);
                AddFloat("rotationY", cmd.rotationY);
                break;
            case "MoveObject":
                AddString("objectName", cmd.objectName);
                AddFloat("x", cmd.x); AddFloat("y", cmd.y); AddFloat("z", cmd.z);
                AddFloat("rotationY", cmd.rotationY);
                break;
            case "DeleteObject":
                AddString("objectName", cmd.objectName);
                break;
            case "SetHumanAnimationController":
                AddString("controllerPath", cmd.controllerPath ?? cmd.controllerPathInResources);
                break;
            case "PlayHumanAnimation":
                AddString("stateName", cmd.stateName);
                AddFloat("crossFadeTime", cmd.crossFadeTime);
                break;
            case "PlayHumanAnimationFromResources":
                AddString("controllerPath", cmd.controllerPath ?? cmd.controllerPathInResources);
                AddString("stateName", cmd.stateName);
                AddFloat("crossFadeTime", cmd.crossFadeTime);
                break;
            case "SetHumanAnimatorFloat":
                AddString("paramName", cmd.paramName);
                AddFloat("value", cmd.value);
                break;
            case "SetHumanAnimatorBool":
                AddString("paramName", cmd.paramName);
                AddBool("boolValue", cmd.boolValue);
                break;
            case "SetHumanAnimatorTrigger":
                AddString("paramName", cmd.paramName);
                break;
            case "DebugLog":
                AddString("message", cmd.message);
                break;
            case "HighlightObject":
                AddString("objectName", cmd.objectName);
                AddFloat("r", cmd.r); AddFloat("g", cmd.g); AddFloat("b", cmd.b);
                AddFloat("duration", cmd.duration);
                break;
            case "ClearHighlight":
                AddString("objectName", cmd.objectName);
                break;
            default:
                break;
        }

        sb.Append("\n  }");
        return sb.ToString();
    }

    private string EscapeJson(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    private void HandleCommand(AgentCommand cmd)
    {
        switch (cmd.action)
        {
            case "SetTimeOfDay":
                environmentAgent.SetTimeOfDay(cmd.hour);
                break;

            case "SetFogEnabled":
                environmentAgent.SetFogEnabled(cmd.enabled);
                break;

            case "SetFogDensity":
                environmentAgent.SetFogDensity(cmd.density);
                break;

            case "SetLightIntensity":
                environmentAgent.SetLightIntensity(cmd.scale);
                break;

            case "SetLightColor":
                environmentAgent.SetLightColor(cmd.r, cmd.g, cmd.b);
                break;

            case "SetAmbientColor":
                environmentAgent.SetAmbientColor(cmd.r, cmd.g, cmd.b);
                break;

            case "SetGravity":
                environmentAgent.SetGravity(cmd.gravityY);
                break;

            case "SetTimeScale":
                environmentAgent.SetTimeScale(cmd.scale);
                break;

            case "PlaceObject":
                var pos = new Vector3(cmd.x, cmd.y, cmd.z);
                if (TryGetFullCharacterName(cmd.objectName, out var fullHumanName))
                {
                    environmentAgent.PlaceHuman(fullHumanName, pos, cmd.rotationY);
                }
                else
                {
                    environmentAgent.PlaceObject(cmd.objectName, pos, cmd.rotationY);
                }
                break;

            case "CreateObjectByName":
                var createPos = new Vector3(cmd.x, cmd.y, cmd.z);
                environmentAgent.CreateObjectByName(
                    cmd.objectName,
                    createPos,
                    cmd.rotationY,
                    cmd.scaleX,
                    cmd.scaleY,
                    cmd.scaleZ
                );
                break;

            case "MoveObject":
                var movePos = new Vector3(cmd.x, cmd.y, cmd.z);
                environmentAgent.MoveObject(cmd.objectName, movePos, cmd.rotationY);
                break;

            case "DeleteObject":
                environmentAgent.DeleteObject(cmd.objectName);
                break;

            case "PlaceHuman":
                var humanPos = new Vector3(cmd.x, cmd.y, cmd.z);
                environmentAgent.PlaceHuman(cmd.humanName ?? cmd.objectName, humanPos, cmd.rotationY);
                break;

            case "SetHumanAnimationController":
                environmentAgent.SetHumanAnimationController(null, (cmd.controllerPath ?? cmd.controllerPathInResources) ?? "");
                break;

            case "PlayHumanAnimation":
                float blend = cmd.crossFadeTime > 0 ? cmd.crossFadeTime : 0.25f;
                environmentAgent.PlayHumanAnimation(null, cmd.stateName ?? "", blend);
                break;

            case "PlayHumanAnimationFromResources":
                float t = cmd.crossFadeTime > 0 ? cmd.crossFadeTime : 0.25f;
                environmentAgent.PlayHumanAnimationFromResources(null, (cmd.controllerPath ?? cmd.controllerPathInResources) ?? "", cmd.stateName ?? "", t);
                break;

            case "SetHumanAnimatorFloat":
                environmentAgent.SetHumanAnimatorFloat(null, cmd.paramName ?? "", cmd.value);
                break;

            case "SetHumanAnimatorBool":
                environmentAgent.SetHumanAnimatorBool(null, cmd.paramName ?? "", cmd.boolValue);
                break;

            case "SetHumanAnimatorTrigger":
                environmentAgent.SetHumanAnimatorTrigger(null, cmd.paramName ?? "");
                break;

            case "DebugLog":
                Debug.Log("[CollisionThread] " + (cmd.message ?? ""));
                break;

            case "HighlightObject":
                environmentAgent.HighlightObject(cmd.objectName, cmd.r, cmd.g, cmd.b, cmd.duration);
                break;

            case "ClearHighlight":
                environmentAgent.ClearHighlight(cmd.objectName);
                break;

            default:
                Debug.LogWarning("AgentNetworkBridge: Unknown command action = " + cmd.action);
                break;
        }
    }

    private static bool TryGetFullCharacterName(string name, out string fullName)
    {
        fullName = null;
        if (string.IsNullOrEmpty(name)) return false;

        string lower = name.ToLowerInvariant();
        string[] prefixes = { "doctor_", "nurse_", "emt_", "patient_", "surgeon_", "infectiologist_" };
        bool hasPrefix = false;
        foreach (var p in prefixes)
        {
            if (lower.StartsWith(p))
            {
                hasPrefix = true;
                break;
            }
        }
        if (!hasPrefix) return false;

        string[] parts = { "_body", "_face", "_coat", "_hat", "_pants", "_shoes", "_sneakers", "_hair", "_mask", "_gloves", "_slippers", "_suit", "_shirt", "_beard", "_glasses" };
        foreach (var part in parts)
        {
            if (lower.EndsWith(part))
            {
                fullName = name.Substring(0, name.Length - part.Length);
                return true;
            }
        }

        // Name already looks like a full character prefab (e.g., Doctor_01)
        fullName = name;
        return true;
    }
}

