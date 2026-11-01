using System;
using System.Collections;
using System.IO;
using UnityEngine;

/// <summary>
/// Periodically renders the scene from fixed cameras and saves PNGs that the Python
/// agent sends to the LLM as visual feedback on the room layout.
/// </summary>
/// <remarks>
/// Every <see cref="intervalSeconds"/> seconds four images are written:
/// <list type="bullet">
/// <item><description><c>{base}_xz.png</c>: top-down orthographic view (camera below the
/// ceiling looking straight down; shows the X/Z layout).</description></item>
/// <item><description><c>{base}_xy.png</c>: front orthographic view (camera near the
/// far wall looking toward -Z; shows the X/Y layout).</description></item>
/// <item><description><c>{base}_yz.png</c>: side orthographic view (camera near the
/// left wall looking toward +X; shows the Y/Z layout).</description></item>
/// <item><description><c>{base}_iso.png</c>: elevated 45-degree perspective view from
/// outside the front-right corner (used for paper figures).</description></item>
/// </list>
/// The room is assumed to be centered on x = 0 and to extend from z = 0 to z = depth.
/// Its size is re-detected before every capture from the floor and wall Cubes created
/// in Phase 1 (falling back to the Inspector values), and wall/ceiling Cubes are
/// hidden during capture so they do not block the view.
///
/// The output base path is, in order of priority: <see cref="overrideBasePath"/>, the
/// <c>UNITY_SCREENSHOT_PATH</c> environment variable (a trailing ".png" is removed), or
/// <c>Agent/api_files/scene_screenshot</c>. Relative paths are resolved against the
/// repository root (the folder that contains the Unity project).
///
/// Setup: none required. <see cref="AutoScreenshotBootstrap"/> (runtime) and
/// <c>ScreenshotLoopEditorBootstrap</c> (Editor) create a persistent "ScreenshotSender"
/// GameObject with this component automatically.
/// </remarks>
public class ScreenshotLoopSender : MonoBehaviour
{
    /// <summary>Seconds between capture rounds.</summary>
    [Tooltip("Seconds between screenshot captures.")]
    public float intervalSeconds = 3f;

    /// <summary>Optional output base path (without extension); overrides the environment variable and default.</summary>
    [Tooltip("Base path for output files (without extension). Leave empty to use the UNITY_SCREENSHOT_PATH environment variable or the repository default.")]
    public string overrideBasePath = "";

    /// <summary>Fallback orthographic half-size; in practice each view computes its own size from the room.</summary>
    [Tooltip("Fallback orthographic half-size. Each view normally derives its size from the room dimensions.")]
    public float orthoSize = 5.5f;

    /// <summary>Width in pixels of each orthographic view.</summary>
    [Tooltip("Render resolution (width, height) for each orthographic view.")]
    public int renderWidth = 1024;
    /// <summary>Height in pixels of each orthographic view.</summary>
    public int renderHeight = 1024;

    /// <summary>Room width (X) used when no floor Cube is found.</summary>
    [Tooltip("Fallback room dimensions (width, depth, height) used when no floor or wall Cubes can be detected in the scene.")]
    public float fallbackRoomWidth  = 10f;
    /// <summary>Room depth (Z) used when no floor Cube is found.</summary>
    public float fallbackRoomDepth  = 10f;
    /// <summary>Room height (Y) used when no wall Cubes are found.</summary>
    public float fallbackRoomHeight = 3.5f;

    /// <summary>Extra meters of view around the room edge.</summary>
    [Tooltip("Extra meters added around the room edge so objects flush with walls aren't clipped.")]
    public float viewMargin = 0.5f;

    /// <summary>Width in pixels of the perspective view.</summary>
    [Tooltip("Render resolution (width, height) for the 45° perspective view used for paper figures. Larger than the orthographic views for print quality.")]
    public int isoRenderWidth  = 1920;
    /// <summary>Height in pixels of the perspective view.</summary>
    public int isoRenderHeight = 1280;

    /// <summary>Vertical field of view of the perspective camera, in degrees.</summary>
    [Tooltip("Field of view (degrees) for the 45° perspective camera.")]
    public float isoFieldOfView = 45f;

    /// <summary>Distance of the perspective camera outside the room, as a multiple of the longer room side.</summary>
    [Tooltip("How far outside the room the perspective camera sits, as a multiple of the longer room side.")]
    public float isoOffsetMultiplier = 0.35f;

    /// <summary>Perspective camera height as a multiple of the room height.</summary>
    [Tooltip("Perspective camera height as a multiple of the room height (e.g. 1.5 = 50% above the ceiling).")]
    public float isoHeightMultiplier = 1.6f;

    /// <summary>Logs the output location and starts the capture loop.</summary>
    private void Start()
    {
        string basePath = ResolveBasePath();
        Debug.Log($"[ScreenshotLoopSender] Output base: {basePath}_xz/xy/yz/iso.png");
        StartCoroutine(CaptureLoop());
    }

    /// <summary>Captures all four views, then waits <see cref="intervalSeconds"/>, forever.</summary>
    private IEnumerator CaptureLoop()
    {
        yield return null; // Wait one frame for the scene to initialize.
        while (true)
        {
            string basePath = ResolveBasePath();

            // Hide walls and ceiling so they don't occlude the interior view.
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

            // 45° perspective overview for paper figures.
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
    /// Reads the room size from the structural Cubes created in Phase 1: width and depth
    /// from the floor (a flat slab near y = 0 with large X/Z scale and small Y scale) and
    /// height from the top of the tallest wall. Falls back to the Inspector values for
    /// any dimension that cannot be detected.
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
    /// Temporarily disables the renderers of wall and ceiling Cubes so they don't block
    /// the camera. Floor Cubes (flat, near y = 0) stay visible.
    /// </summary>
    /// <returns>The renderers that were disabled, for <see cref="RestoreRenderers"/>.</returns>
    private System.Collections.Generic.List<Renderer> HideStructuralCubes()
    {
        var hidden = new System.Collections.Generic.List<Renderer>();
        foreach (var go in FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go == null || !go.name.Contains("Cube")) continue;
            // Keep the floor visible (near the ground and flat).
            Vector3 pos = go.transform.position;
            Vector3 scale = go.transform.localScale;
            bool isFloor = pos.y <= 0.15f && scale.y <= 0.5f && scale.x >= 1f && scale.z >= 1f;
            if (isFloor) continue;
            // This is a wall or ceiling: hide it.
            var r = go.GetComponent<Renderer>();
            if (r != null && r.enabled)
            {
                r.enabled = false;
                hidden.Add(r);
            }
        }
        return hidden;
    }

    /// <summary>Re-enables renderers hidden by <see cref="HideStructuralCubes"/>.</summary>
    private void RestoreRenderers(System.Collections.Generic.List<Renderer> hidden)
    {
        foreach (var r in hidden)
            if (r != null) r.enabled = true;
    }

    /// <summary>
    /// Renders a perspective view with a temporary camera and saves it as a PNG.
    /// Used for the 45° overview; renders at <see cref="isoRenderWidth"/> x
    /// <see cref="isoRenderHeight"/> for higher print quality.
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

    /// <summary>
    /// Renders an orthographic view with a temporary camera at
    /// <see cref="renderWidth"/> x <see cref="renderHeight"/> and saves it as a PNG.
    /// </summary>
    /// <param name="orthoOverride">Orthographic half-size; values &lt;= 0 use <see cref="orthoSize"/>.</param>
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

    /// <summary>Returns the absolute output base path (see the class remarks for the priority order).</summary>
    private string ResolveBasePath()
    {
        if (!string.IsNullOrEmpty(overrideBasePath))
            return MakeAbsolute(overrideBasePath);

        string envPath = Environment.GetEnvironmentVariable("UNITY_SCREENSHOT_PATH");
        if (!string.IsNullOrEmpty(envPath))
        {
            // Strip the .png suffix to get the base path.
            string stripped = envPath.EndsWith(".png", StringComparison.OrdinalIgnoreCase)
                ? envPath.Substring(0, envPath.Length - 4)
                : envPath;
            return MakeAbsolute(stripped);
        }

        return MakeAbsolute(Path.Combine("Agent", "api_files", "scene_screenshot"));
    }

    /// <summary>Resolves a relative path against the repository root (the parent of the Unity project folder).</summary>
    private string MakeAbsolute(string path)
    {
        if (Path.IsPathRooted(path))
            return path;
        string repoRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        return Path.GetFullPath(Path.Combine(repoRoot, path));
    }
}
