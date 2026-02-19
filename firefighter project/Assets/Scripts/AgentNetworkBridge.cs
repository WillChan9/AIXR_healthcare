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
            HandleCommand(cmd);
        }

        // Clear file after processing to avoid re-executing commands.
        try
        {
            System.IO.File.WriteAllText(path, "[]");
        }
        catch
        {
            // If we can't clear it, commands will repeat; log once in Console if needed.
        }
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
                environmentAgent.PlaceObject(cmd.objectName, pos, cmd.rotationY);
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

            case "PlaceHuman":
                var humanPos = new Vector3(cmd.x, cmd.y, cmd.z);
                environmentAgent.PlaceHuman(cmd.humanName ?? cmd.objectName, humanPos, cmd.rotationY);
                break;

            case "SetHumanAnimationController":
                environmentAgent.SetHumanAnimationController(null, cmd.controllerPathInResources ?? "");
                break;

            case "PlayHumanAnimationFromResources":
                float t = cmd.crossFadeTime > 0 ? cmd.crossFadeTime : 0.25f;
                environmentAgent.PlayHumanAnimationFromResources(null, cmd.controllerPathInResources ?? "", cmd.stateName ?? "", t);
                break;

            default:
                Debug.LogWarning("AgentNetworkBridge: Unknown command action = " + cmd.action);
                break;
        }
    }
}

