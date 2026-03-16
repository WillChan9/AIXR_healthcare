#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class ScreenshotLoopEditorBootstrap
{
    private static bool _ensureScheduled;

    static ScreenshotLoopEditorBootstrap()
    {
        EditorApplication.playModeStateChanged += OnPlayModeChanged;
        EditorApplication.update += OnEditorUpdate;
        Debug.LogWarning("ScreenshotLoopEditorBootstrap loaded.");
    }

    private static void OnPlayModeChanged(PlayModeStateChange state)
    {
        Debug.LogWarning($"ScreenshotLoopEditorBootstrap: PlayModeStateChange={state}");
        _ensureScheduled = true;
        if (state != PlayModeStateChange.EnteredPlayMode)
        {
            return;
        }

        if (Object.FindObjectOfType<ScreenshotLoopSender>() != null)
        {
            Debug.LogWarning("ScreenshotLoopEditorBootstrap: ScreenshotLoopSender already present (EnteredPlayMode).");
            return;
        }

        var go = new GameObject("ScreenshotSender");
        Object.DontDestroyOnLoad(go);
        var sender = go.AddComponent<ScreenshotLoopSender>();
        Debug.LogWarning(sender != null
            ? "ScreenshotLoopEditorBootstrap: created ScreenshotSender (EnteredPlayMode)."
            : "ScreenshotLoopEditorBootstrap: FAILED to add ScreenshotLoopSender.");
    }

    private static void OnEditorUpdate()
    {
        if (!EditorApplication.isPlaying)
        {
            return;
        }
        if (!_ensureScheduled)
        {
            return;
        }
        _ensureScheduled = false;

        if (Object.FindObjectOfType<ScreenshotLoopSender>() != null)
        {
            Debug.LogWarning("ScreenshotLoopEditorBootstrap: ScreenshotLoopSender already present (EditorUpdate).");
            return;
        }

        var go = new GameObject("ScreenshotSender");
        Object.DontDestroyOnLoad(go);
        var sender = go.AddComponent<ScreenshotLoopSender>();
        Debug.LogWarning(sender != null
            ? "ScreenshotLoopEditorBootstrap: created ScreenshotSender (EditorUpdate)."
            : "ScreenshotLoopEditorBootstrap: FAILED to add ScreenshotLoopSender (EditorUpdate).");
    }
}
#endif
