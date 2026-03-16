using UnityEngine;

public static class AutoScreenshotBootstrap
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    private static void EnsureScreenshotSenderEarly()
    {
        EnsureScreenshotSender("BeforeSceneLoad");
    }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void EnsureScreenshotSenderLate()
    {
        EnsureScreenshotSender("AfterSceneLoad");
    }

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
