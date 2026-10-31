#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

/// <summary>
/// Editor-only bootstrap that adds an <see cref="AgentNetworkBridge"/> to the scene
/// when Play Mode starts, mirroring the pattern used by
/// <see cref="ScreenshotLoopEditorBootstrap"/>.
///
/// If the scene already contains a bridge, nothing happens. Otherwise the bridge is
/// added to the GameObject that holds <see cref="EnvironmentAgent"/> (typically
/// AI_Manager) and wired to it; if there is no EnvironmentAgent, a new persistent
/// "AIAgent" GameObject is created instead. No manual scene setup is required.
/// </summary>
[InitializeOnLoad]
public static class AgentNetworkBridgeBootstrap
{
    /// <summary>Set on any play-mode change; consumed by the next <see cref="OnEditorUpdate"/> while playing.</summary>
    private static bool _ensureScheduled;

    /// <summary>Registers the editor callbacks. Called by Unity on domain reload.</summary>
    static AgentNetworkBridgeBootstrap()
    {
        EditorApplication.playModeStateChanged += OnPlayModeChanged;
        EditorApplication.update += OnEditorUpdate;
    }

    /// <summary>Adds the bridge as soon as Play Mode has been entered.</summary>
    private static void OnPlayModeChanged(PlayModeStateChange state)
    {
        _ensureScheduled = true;
        if (state != PlayModeStateChange.EnteredPlayMode)
            return;

        EnsureBridgeExists("OnPlayModeChanged");
    }

    /// <summary>Fallback check on the first editor update after a play-mode change.</summary>
    private static void OnEditorUpdate()
    {
        if (!EditorApplication.isPlaying || !_ensureScheduled)
            return;

        _ensureScheduled = false;
        EnsureBridgeExists("OnEditorUpdate");
    }

    /// <summary>Adds an <see cref="AgentNetworkBridge"/> to the scene unless one already exists.</summary>
    /// <param name="source">Label used in log messages to identify the caller.</param>
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
            // Wire the reference directly. Awake has already run inside AddComponent,
            // so this overrides whatever its FindFirstObjectByType fallback found.
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
