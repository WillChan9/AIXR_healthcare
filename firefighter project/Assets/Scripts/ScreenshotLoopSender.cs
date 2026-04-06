using System;
using System.Collections;
using System.IO;
using UnityEngine;

/// <summary>
/// Captures three orthographic views every <see cref="intervalSeconds"/> seconds:
///   _xz.png  – top-down  (camera above looking straight down, shows X/Z layout)
///   _xy.png  – front     (camera in front looking toward +Z, shows X/Y layout)
///   _yz.png  – side      (camera to the right looking toward -X, shows Y/Z layout)
///
/// Output paths are derived from UNITY_SCREENSHOT_PATH env var (strip .png → base),
/// or from <see cref="overrideBasePath"/>, or default to Agent/api_files/scene_screenshot.
/// </summary>
public class ScreenshotLoopSender : MonoBehaviour
{
    [Tooltip("Seconds between screenshot captures.")]
    public float intervalSeconds = 3f;

    [Tooltip("Base path for output files (without extension). Leave empty to use UNITY_SCREENSHOT_PATH env or repo default.")]
    public string overrideBasePath = "";

    [Tooltip("Orthographic half-size in Unity units (how much of the scene is visible).")]
    public float orthoSize = 3.5f;

    [Tooltip("Render resolution for each view.")]
    public int renderWidth = 1024;
    public int renderHeight = 1024;

    // Cameras are placed just inside the room walls so walls/ceiling don't occlude the view.
    // Room: x[-2.5,2.5], z[0,5], y[0,3.2]. Walls at x=±2.5, z=0/5, ceiling at y=3.2.
    private static readonly Vector3 TopDownPos  = new Vector3(0f,    3.05f, 2.5f);  // just below ceiling, centered over room
    private static readonly Vector3 FrontPos    = new Vector3(0f,    1.6f,  4.9f);  // just inside back wall, looking toward door
    private static readonly Vector3 SidePos     = new Vector3(-2.4f, 1.6f,  2.5f); // just inside left wall, looking right

    private void Start()
    {
        string basePath = ResolveBasePath();
        Debug.Log($"[ScreenshotLoopSender] Output base: {basePath}_xz/xy/yz.png");
        StartCoroutine(CaptureLoop());
    }

    private IEnumerator CaptureLoop()
    {
        yield return null; // wait one frame for scene to initialize
        while (true)
        {
            string basePath = ResolveBasePath();
            CaptureView(
                TopDownPos,
                Quaternion.LookRotation(Vector3.down, Vector3.forward),
                basePath + "_xz.png",
                "XZ (top-down)",
                farClip: 4f
            );
            CaptureView(
                FrontPos,
                Quaternion.LookRotation(Vector3.back, Vector3.up),
                basePath + "_xy.png",
                "XY (front)",
                farClip: 8f
            );
            CaptureView(
                SidePos,
                Quaternion.LookRotation(Vector3.right, Vector3.up),
                basePath + "_yz.png",
                "YZ (side)",
                farClip: 8f
            );
            yield return new WaitForSeconds(intervalSeconds);
        }
    }

    private void CaptureView(Vector3 position, Quaternion rotation, string outputPath, string label, float farClip = 10f)
    {
        RenderTexture rt = new RenderTexture(renderWidth, renderHeight, 24);

        GameObject camGO = new GameObject($"_OrthoCapture_{label}");
        Camera cam = camGO.AddComponent<Camera>();
        cam.orthographic = true;
        cam.orthographicSize = orthoSize;
        cam.transform.position = position;
        cam.transform.rotation = rotation;
        cam.targetTexture = rt;
        cam.cullingMask = -1;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0.12f, 0.12f, 0.12f);
        cam.nearClipPlane = 0.05f;
        cam.farClipPlane = farClip;
        cam.Render();

        RenderTexture.active = rt;
        Texture2D tex = new Texture2D(renderWidth, renderHeight, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, renderWidth, renderHeight), 0, 0);
        tex.Apply();
        RenderTexture.active = null;

        byte[] bytes = tex.EncodeToPNG();
        try
        {
            string dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);
            File.WriteAllBytes(outputPath, bytes);
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[ScreenshotLoopSender] {label} capture failed: {ex.Message}");
        }

        Destroy(camGO);
        Destroy(tex);
        rt.Release();
        Destroy(rt);
    }

    private string ResolveBasePath()
    {
        if (!string.IsNullOrEmpty(overrideBasePath))
            return MakeAbsolute(overrideBasePath);

        string envPath = Environment.GetEnvironmentVariable("UNITY_SCREENSHOT_PATH");
        if (!string.IsNullOrEmpty(envPath))
        {
            // Strip .png suffix to get the base
            string stripped = envPath.EndsWith(".png", StringComparison.OrdinalIgnoreCase)
                ? envPath.Substring(0, envPath.Length - 4)
                : envPath;
            return MakeAbsolute(stripped);
        }

        return MakeAbsolute(Path.Combine("Agent", "api_files", "scene_screenshot"));
    }

    private string MakeAbsolute(string path)
    {
        if (Path.IsPathRooted(path))
            return path;
        string repoRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        return Path.GetFullPath(Path.Combine(repoRoot, path));
    }
}
