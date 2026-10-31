using UnityEngine;

/// <summary>
/// Runtime bootstrap that guarantees a <see cref="ScreenshotLoopSender"/> exists in
/// every scene, in both the Editor and standalone builds.
///
/// No scene setup is required: Unity calls the methods below automatically through
/// <see cref="RuntimeInitializeOnLoadMethodAttribute"/>. If no sender is found, a
/// persistent "ScreenshotSender" GameObject is created and marked
/// <c>DontDestroyOnLoad</c>. The check runs both before and after the first scene
/// loads so a sender placed manually in the scene is never duplicated.
/// </summary>
public static class AutoScreenshotBootstrap
{
    /// <summary>Runs before the first scene is loaded.</summary>
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    private static void EnsureScreenshotSenderEarly()
    {
        EnsureScreenshotSender("BeforeSceneLoad");
    }

    /// <summary>Runs after the first scene has finished loading.</summary>
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void EnsureScreenshotSenderLate()
    {
        EnsureScreenshotSender("AfterSceneLoad");
    }

    /// <summary>
    /// Creates a persistent ScreenshotSender GameObject unless a
    /// <see cref="ScreenshotLoopSender"/> already exists.
    /// </summary>
    /// <param name="phase">Label used in the log message to identify the caller.</param>
    private static void EnsureScreenshotSender(string phase)
    {
        var existing = Object.FindObjectOfType<ScreenshotLoopSender>();
        if (existing != null)
        {
            Debug.LogWarning($"AutoScreenshotBootstrap: ScreenshotLoopSender already present ({phase}).");
            return;
        }

        var go = new GameObject("ScreenshotSender");
        Object.DontDestroyOnLoad(go);
        go.AddComponent<ScreenshotLoopSender>();
        Debug.LogWarning($"AutoScreenshotBootstrap: created ScreenshotSender ({phase}).");
    }
}
