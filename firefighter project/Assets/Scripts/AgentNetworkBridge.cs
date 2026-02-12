using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// 负责和外部 Python FastAPI 通信的桥接脚本。
/// Unity 作为客户端，定期轮询 Python 的 /command 接口，
/// 收到指令后调用 EnvironmentAgent 来修改环境。
/// </summary>
public class AgentNetworkBridge : MonoBehaviour
{
    [Header("Python FastAPI 服务器地址，例如 http://192.168.1.10:8000")]
    public string serverBaseUrl = "http://127.0.0.1:8000";

    [Header("轮询间隔（秒）")]
    public float pollIntervalSeconds = 1.0f;

    [Header("环境控制脚本引用")]
    public EnvironmentAgent environmentAgent;

    [System.Serializable]
    private class AgentCommand
    {
        public string action;

        // 通用参数（根据 action 使用）
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
                // 可以根据需要打印日志，但避免每帧刷屏
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
                Debug.LogWarning("AgentNetworkBridge: 无法解析 JSON 指令：" + json);
            }

            if (cmd == null || string.IsNullOrEmpty(cmd.action) || environmentAgent == null)
                yield break;

            HandleCommand(cmd);
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
                Debug.LogWarning("AgentNetworkBridge: 未知指令 action = " + cmd.action);
                break;
        }
    }
}

