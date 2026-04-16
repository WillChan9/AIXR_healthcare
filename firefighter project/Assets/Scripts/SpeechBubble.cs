using System.Collections;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// World-space speech bubble that floats above a character's head and faces the camera.
/// Created entirely in code — no prefab or Canvas setup required.
///
/// Usage: call Show(text, duration) from EnvironmentAgent.ShowSpeechBubble().
/// The component is auto-added to character GameObjects as needed.
/// </summary>
public class SpeechBubble : MonoBehaviour
{
    [Tooltip("Height above the GameObject's local origin (metres).")]
    public float heightOffset = 2.2f;

    [Tooltip("Font size for bubble text.")]
    public int fontSize = 28;

    // ── Runtime ───────────────────────────────────────────────────────────────
    private Canvas        _canvas;
    private Text          _label;
    private RectTransform _panel;
    private Coroutine     _hideCoroutine;
    private Transform     _cam;

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    private void Awake()
    {
        BuildUI();
        _canvas.gameObject.SetActive(false);
    }

    private void LateUpdate()
    {
        if (_canvas == null || !_canvas.gameObject.activeSelf) return;

        // Float above the character's current world position
        _canvas.transform.position = transform.position + Vector3.up * heightOffset;

        // Billboard: match camera rotation so text is never mirrored
        if (_cam == null && Camera.main != null)
            _cam = Camera.main.transform;
        if (_cam != null)
            _canvas.transform.rotation = _cam.rotation;
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /// <summary>Display <paramref name="text"/> for <paramref name="duration"/> seconds.</summary>
    public void Show(string text, float duration = 5f)
    {
        if (_hideCoroutine != null) StopCoroutine(_hideCoroutine);
        _label.text = text;

        // Grow panel height based on approximate line count
        int charsPerLine = 26;
        int lines = Mathf.Max(1, Mathf.CeilToInt((float)text.Length / charsPerLine));
        _panel.sizeDelta = new Vector2(300, 44 + lines * 30);

        _canvas.gameObject.SetActive(true);
        _hideCoroutine = StartCoroutine(HideAfter(duration));
    }

    /// <summary>Hide immediately.</summary>
    public void Hide()
    {
        if (_hideCoroutine != null) { StopCoroutine(_hideCoroutine); _hideCoroutine = null; }
        if (_canvas != null) _canvas.gameObject.SetActive(false);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private IEnumerator HideAfter(float duration)
    {
        yield return new WaitForSeconds(duration);
        _canvas.gameObject.SetActive(false);
    }

    private void BuildUI()
    {
        // World-space canvas — separate GameObject so it can be repositioned freely
        var canvasGo = new GameObject("[SpeechBubble]");
        canvasGo.transform.SetParent(transform, worldPositionStays: false);

        _canvas = canvasGo.AddComponent<Canvas>();
        _canvas.renderMode  = RenderMode.WorldSpace;
        _canvas.sortingOrder = 100;
        // 0.005 scale → 300×80 canvas units = 1.5m × 0.4m in world space (readable in VR)
        _canvas.transform.localScale = Vector3.one * 0.005f;

        // Background panel
        var panelGo = new GameObject("Panel");
        panelGo.transform.SetParent(_canvas.transform, worldPositionStays: false);
        _panel = panelGo.AddComponent<RectTransform>();
        _panel.sizeDelta = new Vector2(300, 80);
        var img = panelGo.AddComponent<Image>();
        img.color = new Color(0.04f, 0.04f, 0.12f, 0.88f);

        // Text label
        var textGo = new GameObject("Label");
        textGo.transform.SetParent(panelGo.transform, worldPositionStays: false);
        var textRect = textGo.AddComponent<RectTransform>();
        textRect.anchorMin = Vector2.zero;
        textRect.anchorMax = Vector2.one;
        textRect.offsetMin = new Vector2(10, 8);
        textRect.offsetMax = new Vector2(-10, -8);
        _label = textGo.AddComponent<Text>();

        // Use whichever built-in font is available (Unity 6 vs earlier)
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
