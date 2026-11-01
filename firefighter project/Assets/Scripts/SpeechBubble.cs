using System.Collections;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// World-space speech bubble that floats above a character's head and faces the camera.
/// The UI (canvas, background panel and text) is built entirely in code, so no prefab
/// or Canvas setup is required.
///
/// Usage: <see cref="EnvironmentAgent.ShowSpeechBubble"/> adds this component to a
/// character on first use and calls <see cref="Show"/>. A new message replaces the
/// current one and restarts the timer.
/// </summary>
public class SpeechBubble : MonoBehaviour
{
    /// <summary>Height of the bubble above the character's origin, in meters.</summary>
    [Tooltip("Height above the GameObject's origin (meters).")]
    public float heightOffset = 2.2f;

    /// <summary>Font size of the bubble text. Read once, when the UI is built.</summary>
    [Tooltip("Font size for the bubble text.")]
    public int fontSize = 28;

    // ---- Runtime ----
    private Canvas        _canvas;
    private Text          _label;
    private RectTransform _panel;
    private Coroutine     _hideCoroutine;
    private Transform     _cam;

    // ---- Lifecycle ----

    /// <summary>Builds the bubble UI and hides it.</summary>
    private void Awake()
    {
        BuildUI();
        _canvas.gameObject.SetActive(false);
    }

    /// <summary>Keeps the visible bubble above the character and facing the camera.</summary>
    private void LateUpdate()
    {
        if (_canvas == null || !_canvas.gameObject.activeSelf) return;

        // Float above the character's current world position.
        _canvas.transform.position = transform.position + Vector3.up * heightOffset;

        // Billboard: match the camera rotation so the text is never mirrored.
        if (_cam == null && Camera.main != null)
            _cam = Camera.main.transform;
        if (_cam != null)
            _canvas.transform.rotation = _cam.rotation;
    }

    // ---- Public API ----

    /// <summary>Displays <paramref name="text"/> for <paramref name="duration"/> seconds, resizing the panel to fit.</summary>
    public void Show(string text, float duration = 5f)
    {
        if (_hideCoroutine != null) StopCoroutine(_hideCoroutine);
        _label.text = text;

        // Grow the panel height based on an approximate line count.
        int charsPerLine = 26;
        int lines = Mathf.Max(1, Mathf.CeilToInt((float)text.Length / charsPerLine));
        _panel.sizeDelta = new Vector2(300, 44 + lines * 30);

        _canvas.gameObject.SetActive(true);
        _hideCoroutine = StartCoroutine(HideAfter(duration));
    }

    /// <summary>Hides the bubble immediately.</summary>
    public void Hide()
    {
        if (_hideCoroutine != null) { StopCoroutine(_hideCoroutine); _hideCoroutine = null; }
        if (_canvas != null) _canvas.gameObject.SetActive(false);
    }

    // ---- Helpers ----

    /// <summary>Hides the bubble after <paramref name="duration"/> seconds.</summary>
    private IEnumerator HideAfter(float duration)
    {
        yield return new WaitForSeconds(duration);
        _canvas.gameObject.SetActive(false);
    }

    /// <summary>Creates the world-space canvas, background panel and text label as children of this GameObject.</summary>
    private void BuildUI()
    {
        // World-space canvas on its own child GameObject so it can be positioned freely.
        var canvasGo = new GameObject("[SpeechBubble]");
        canvasGo.transform.SetParent(transform, worldPositionStays: false);

        _canvas = canvasGo.AddComponent<Canvas>();
        _canvas.renderMode  = RenderMode.WorldSpace;
        _canvas.sortingOrder = 100;
        // A 0.005 scale makes 300x80 canvas units 1.5 m x 0.4 m in world space (readable in VR).
        _canvas.transform.localScale = Vector3.one * 0.005f;

        // Background panel.
        var panelGo = new GameObject("Panel");
        panelGo.transform.SetParent(_canvas.transform, worldPositionStays: false);
        _panel = panelGo.AddComponent<RectTransform>();
        _panel.sizeDelta = new Vector2(300, 80);
        var img = panelGo.AddComponent<Image>();
        img.color = new Color(0.04f, 0.04f, 0.12f, 0.88f);

        // Text label.
        var textGo = new GameObject("Label");
        textGo.transform.SetParent(panelGo.transform, worldPositionStays: false);
        var textRect = textGo.AddComponent<RectTransform>();
        textRect.anchorMin = Vector2.zero;
        textRect.anchorMax = Vector2.one;
        textRect.offsetMin = new Vector2(10, 8);
        textRect.offsetMax = new Vector2(-10, -8);
        _label = textGo.AddComponent<Text>();

        // Use whichever built-in font is available ("LegacyRuntime.ttf" in newer Unity versions, "Arial.ttf" in older ones).
        Font builtIn = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        if (builtIn == null)
            builtIn = Resources.GetBuiltinResource<Font>("Arial.ttf");
        _label.font                  = builtIn;
        _label.fontSize              = fontSize;
        _label.color                 = Color.white;
        _label.alignment             = TextAnchor.MiddleCenter;
        _label.horizontalOverflow    = HorizontalWrapMode.Wrap;
        _label.verticalOverflow      = VerticalWrapMode.Overflow;
        _label.resizeTextForBestFit  = false;
    }
}
