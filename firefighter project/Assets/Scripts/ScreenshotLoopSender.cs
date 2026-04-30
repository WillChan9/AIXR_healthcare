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

    [Tooltip("Orthographic half-size fallback. Auto-derived from room dimensions when a floor Cube is detected.")]
    public float orthoSize = 5.5f;

    [Tooltip("Render resolution for each view.")]
    public int renderWidth = 1024;
    public int renderHeight = 1024;

    [Tooltip("Fallback room dimensions used when no floor Cube can be detected in the scene.")]
    public float fallbackRoomWidth  = 10f;
    public float fallbackRoomDepth  = 10f;
    public float fallbackRoomHeight = 3.5f;

    [Tooltip("Extra metres added around the room edge so objects flush with walls aren't clipped.")]
    public float viewMargin = 0.5f;

    [Tooltip("Render resolution for the 45° perspective view (paper figures). Larger than ortho views for print quality.")]
    public int isoRenderWidth  = 1920;
    public int isoRenderHeight = 1280;

    [Tooltip("Field of view (degrees) for the 45° perspective camera.")]
    public float isoFieldOfView = 45f;

    [Tooltip("How far outside the room the iso camera sits, as a multiple of the longer room side.")]
    public float isoOffsetMultiplier = 0.35f;

    [Tooltip("Iso camera height as a multiple of room height (1.5 = 50% above ceiling).")]
    public float isoHeightMultiplier = 1.6f;

    private void Start()
    {
        string basePath = ResolveBasePath();
        Debug.Log($"[ScreenshotLoopSender] Output base: {basePath}_xz/xy/yz/iso.png");
        StartCoroutine(CaptureLoop());
    }

    private IEnumerator CaptureLoop()
    {
        yield return null; // wait one frame for scene to initialize
        while (true)
        {
            string basePath = ResolveBasePath();

            // Hide walls/ceiling so they don't occlude the interior view
            System.Collections.Generic.List<Renderer> hidden = null;
            try { hidden = HideStructuralCubes(); }
            catch (Exception ex) { Debug.LogWarning($"[ScreenshotLoopSender] HideStructuralCubes failed: {ex.Message}"); }

            // Re-derive room size + camera placement every capture so resized rooms are picked up automatically.
            DetectRoomDimensions(out float roomW, out float roomD, out float roomH);
            float halfW = roomW / 2f;
            float halfD = roomD / 2f;
            float frameSize = Mathf.Max(halfW, halfD) + viewMargin;
            float diag = Mathf.Sqrt(roomW * roomW + roomD * roomD) + 1f;

            Vector3 topDownPos = new Vector3(0f, roomH - 0.45f, halfD);
            Vector3 frontPos   = new Vector3(0f, roomH * 0.5f, roomD - 0.1f);
            Vector3 sidePos    = new Vector3(-(halfW - 0.1f), roomH * 0.5f, halfD);

            CaptureView(
                topDownPos,
                Quaternion.LookRotation(Vector3.down, Vector3.forward),
                basePath + "_xz.png",
                "XZ (top-down)",
                orthoOverride: frameSize,
                farClip: roomH + 1f
            );
            CaptureView(
                frontPos,
                Quaternion.LookRotation(Vector3.back, Vector3.up),
                basePath + "_xy.png",
                "XY (front)",
                orthoOverride: Mathf.Max(halfW, roomH * 0.5f) + viewMargin,
                farClip: diag
            );
            CaptureView(
                sidePos,
                Quaternion.LookRotation(Vector3.right, Vector3.up),
                basePath + "_yz.png",
                "YZ (side)",
                orthoOverride: Mathf.Max(halfD, roomH * 0.5f) + viewMargin,
                farClip: diag
            );

            // 45° perspective hero shot for paper figures.
            // Camera sits outside the front-right corner, elevated above the ceiling,
            // looking down toward the room interior so 3 walls + floor + objects are visible.
            float isoOffset = Mathf.Max(roomW, roomD) * isoOffsetMultiplier;
            Vector3 isoPos    = new Vector3( halfW + isoOffset,
                                             roomH * isoHeightMultiplier,
                                            -isoOffset );
            Vector3 isoTarget = new Vector3( 0f, 0.6f, halfD );
            CapturePerspectiveView(
                isoPos,
                isoTarget,
                basePath + "_iso.png",
                "ISO (45 perspective)",
                fov: isoFieldOfView,
                farClip: Vector3.Distance(isoPos, isoTarget) + diag
            );

            if (hidden != null) RestoreRenderers(hidden);

            yield return new WaitForSeconds(intervalSeconds);
        }
    }

    /// <summary>
    /// Try to read the room footprint from the structural floor Cube spawned by Phase 1.
    /// The floor is a flat slab near y=0 with large X/Z scale and small Y scale.
    /// Falls back to the inspector defaults when no floor is found.
    /// </summary>
    private void DetectRoomDimensions(out float roomWidth, out float roomDepth, out float roomHeight)
    {
        roomWidth  = fallbackRoomWidth;
        roomDepth  = fallbackRoomDepth;
        roomHeight = fallbackRoomHeight;

        bool foundFloor = false;
        float maxWallTop = 0f;

        foreach (var go in FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go == null || !go.name.Contains("Cube")) continue;
            Vector3 pos = go.transform.position;
            Vector3 scale = go.transform.localScale;

            // Floor: thin slab near y=0, both horizontal extents large.
            bool isFloor = !foundFloor
                           && pos.y <= 0.15f
                           && scale.y <= 0.5f
                           && scale.x >= 1.5f
                           && scale.z >= 1.5f;
            if (isFloor)
            {
                roomWidth = scale.x;
                roomDepth = scale.z;
                foundFloor = true;
            }

            // Wall: tall (y scale large). Top of the wall ≈ ceiling height.
            if (scale.y >= 1.2f && (scale.x < 0.6f || scale.z < 0.6f))
            {
                float top = pos.y + scale.y / 2f;
                if (top > maxWallTop) maxWallTop = top;
            }
        }

        if (maxWallTop > 0.5f) roomHeight = maxWallTop;
    }

    /// <summary>
    /// Temporarily disable MeshRenderers on wall/ceiling Cubes so they don't block the camera.
    /// Floor Cubes (near y=0, flat) are kept visible.
    /// </summary>
    private System.Collections.Generic.List<Renderer> HideStructuralCubes()
    {
        var hidden = new System.Collections.Generic.List<Renderer>();
        foreach (var go in FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go == null || !go.name.Contains("Cube")) continue;
            // Keep floor visible (near ground + flat)
            Vector3 pos = go.transform.position;
            Vector3 scale = go.transform.localScale;
            bool isFloor = pos.y <= 0.15f && scale.y <= 0.5f && scale.x >= 1f && scale.z >= 1f;
            if (isFloor) continue;
            // This is a wall or ceiling — hide it
            var r = go.GetComponent<Renderer>();
            if (r != null && r.enabled)
            {
                r.enabled = false;
                hidden.Add(r);
            }
        }
        return hidden;
    }

    private void RestoreRenderers(System.Collections.Generic.List<Renderer> hidden)
    {
        foreach (var r in hidden)
            if (r != null) r.enabled = true;
    }

    /// <summary>
    /// Capture a perspective view (used for the 45° iso hero shot for paper figures).
    /// Renders at <see cref="isoRenderWidth"/> × <see cref="isoRenderHeight"/> for higher print quality.
    /// </summary>
    private void CapturePerspectiveView(Vector3 position, Vector3 lookAt, string outputPath, string label, float fov, float farClip)
    {
        RenderTexture rt = new RenderTexture(isoRenderWidth, isoRenderHeight, 24);

        GameObject camGO = new GameObject($"_PerspCapture_{label}");
        Camera cam = camGO.AddComponent<Camera>();
        cam.orthographic = false;
        cam.fieldOfView = fov;
        cam.transform.position = position;
        cam.transform.LookAt(lookAt, Vector3.up);
        cam.targetTexture = rt;
        cam.cullingMask = -1;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0.12f, 0.12f, 0.12f);
        cam.nearClipPlane = 0.05f;
        cam.farClipPlane = Mathf.Max(farClip, 5f);
        cam.Render();

        RenderTexture.active = rt;
        Texture2D tex = new Texture2D(isoRenderWidth, isoRenderHeight, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, isoRenderWidth, isoRenderHeight), 0, 0);
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

    private void CaptureView(Vector3 position, Quaternion rotation, string outputPath, string label, float orthoOverride = -1f, float farClip = 10f)
    {
        RenderTexture rt = new RenderTexture(renderWidth, renderHeight, 24);

        GameObject camGO = new GameObject($"_OrthoCapture_{label}");
        Camera cam = camGO.AddComponent<Camera>();
        cam.orthographic = true;
        cam.orthographicSize = orthoOverride > 0f ? orthoOverride : orthoSize;
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
