#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

/// <summary>
/// Editor-only bootstrap that adds a <see cref="ScreenshotLoopSender"/> to the scene
/// whenever Play Mode is entered, so the LLM pipeline always receives screenshots
/// without any manual scene setup.
///
/// Loaded automatically on every domain reload via <see cref="InitializeOnLoadAttribute"/>.
/// The check runs both from the play-mode-changed callback and on the next editor
/// update tick (as a fallback in case the scene was not ready yet). If a sender
/// already exists (for example one created by <see cref="AutoScreenshotBootstrap"/>),
/// nothing is added.
/// </summary>
[InitializeOnLoad]
public static class ScreenshotLoopEditorBootstrap
{
    /// <summary>Set on any play-mode change; consumed by the next <see cref="OnEditorUpdate"/> while playing.</summary>
    private static bool _ensureScheduled;

    /// <summary>Registers the editor callbacks. Called by Unity on domain reload.</summary>
    static ScreenshotLoopEditorBootstrap()
    {
        EditorApplication.playModeStateChanged += OnPlayModeChanged;
        EditorApplication.update += OnEditorUpdate;
        Debug.LogWarning("ScreenshotLoopEditorBootstrap loaded.");
    }

    /// <summary>Creates the ScreenshotSender as soon as Play Mode has been entered.</summary>
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

    /// <summary>
    /// Fallback check on the first editor update after a play-mode change, in case
    /// the sender could not be created from <see cref="OnPlayModeChanged"/>.
    /// </summary>
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
