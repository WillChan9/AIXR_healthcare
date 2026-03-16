#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

/// <summary>
/// Automatically adds AgentNetworkBridge to the scene when Play Mode starts,
/// mirroring the pattern used by ScreenshotLoopEditorBootstrap.
/// The component's Awake() already auto-finds EnvironmentAgent, so no manual wiring is needed.
/// </summary>
[InitializeOnLoad]
public static class AgentNetworkBridgeBootstrap
{
    private static bool _ensureScheduled;

    static AgentNetworkBridgeBootstrap()
    {
        EditorApplication.playModeStateChanged += OnPlayModeChanged;
        EditorApplication.update += OnEditorUpdate;
    }

    private static void OnPlayModeChanged(PlayModeStateChange state)
    {
        _ensureScheduled = true;
        if (state != PlayModeStateChange.EnteredPlayMode)
            return;

        EnsureBridgeExists("OnPlayModeChanged");
    }

    private static void OnEditorUpdate()
    {
        if (!EditorApplication.isPlaying || !_ensureScheduled)
            return;

        _ensureScheduled = false;
        EnsureBridgeExists("OnEditorUpdate");
    }

    private static void EnsureBridgeExists(string source)
    {
        if (Object.FindObjectOfType<AgentNetworkBridge>() != null)
        {
            Debug.Log($"AgentNetworkBridgeBootstrap [{source}]: AgentNetworkBridge already present.");
            return;
        }

        // Prefer attaching to the same GameObject as EnvironmentAgent so the reference is co-located.
        EnvironmentAgent envAgent = Object.FindObjectOfType<EnvironmentAgent>();
        GameObject target = envAgent != null ? envAgent.gameObject : new GameObject("AIAgent");

        if (envAgent == null)
            Object.DontDestroyOnLoad(target);

        AgentNetworkBridge bridge = target.AddComponent<AgentNetworkBridge>();
        if (bridge != null)
        {
            // Wire the reference directly so Awake's FindFirstObjectByType isn't needed.
            if (envAgent != null)
                bridge.environmentAgent = envAgent;

            Debug.Log($"AgentNetworkBridgeBootstrap [{source}]: added AgentNetworkBridge to '{target.name}'.");
        }
        else
        {
            Debug.LogWarning($"AgentNetworkBridgeBootstrap [{source}]: FAILED to add AgentNetworkBridge.");
        }
    }
}
#endif
