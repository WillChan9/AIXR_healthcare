using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit;

/// <summary>
/// Unity-side interaction → animation system.
///
/// Subscribes directly to every XRBaseInteractable in the scene (including
/// objects spawned at runtime by EnvironmentAgent) and fires character
/// animations and highlight pulses the instant the player interacts —
/// no Python polling delay.
///
/// Mirrors the ANIMATION_MAP from Agent/agent/interaction_animations.py.
/// Category is derived from the object name prefix before the first '_' or
/// digit: "Treadmill_01" → "treadmill", "Utility_Cart_01" → "utility", etc.
///
/// Attach to the same GameObject as EnvironmentAgent / AgentNetworkBridge
/// (i.e. AI_Manager).  Works independently of whether the Python agent is
/// running — and complements it when it is.
/// </summary>
public class InteractionAnimationController : MonoBehaviour
{
    // ── Animation config ──────────────────────────────────────────────────────

    private struct AnimConfig
    {
        public string Controller;   // Resources path, e.g. "AnimatorControllers/HumanM@Walk01_Forward"
        public string State;        // Animator state name to CrossFade into
        public float  Fade;         // CrossFade duration (seconds)
        public float  IdleDelay;    // Seconds before auto-return to idle (0 = no return)

        public AnimConfig(string controller, string state, float fade, float idleDelay = 0f)
        {
            Controller = controller;
            State      = state;
            Fade       = fade;
            IdleDelay  = idleDelay;
        }
    }

    // ── Constants ─────────────────────────────────────────────────────────────

    private const string IdleCtrl  = "AnimatorControllers/HumanM@Idles";
    private const string IdleState = "HumanM@Idles";

    private const string WalkFwd  = "AnimatorControllers/HumanM@Walk01_Forward";
    private const string WalkLeft = "AnimatorControllers/HumanM@Walk01_Left";
    private const string WalkBack = "AnimatorControllers/HumanM@Walk01_Backward";
    private const string Talking  = "AnimatorControllers/HumanM@Talking";

    private const string WalkFwdState  = "HumanM@Walk01_Forward";
    private const string WalkLeftState = "HumanM@Walk01_Left";
    private const string WalkBackState = "HumanM@Walk01_Backward";
    private const string TalkingState  = "HumanM@Talking";

    // ── Animation map ─────────────────────────────────────────────────────────
    // Key: "category:event_type"  (category = lowercase prefix before first '_' or digit)

    private static readonly Dictionary<string, AnimConfig> AnimMap =
        new Dictionary<string, AnimConfig>
    {
        // ── Treadmill ──────────────────────────────────────────────────────
        { "treadmill:grab",         new AnimConfig(WalkFwd,  WalkFwdState,  0.35f, 6.0f) },
        { "treadmill:release",      new AnimConfig(IdleCtrl, IdleState,     0.5f)        },
        { "treadmill:hover_enter",  new AnimConfig(WalkFwd,  WalkFwdState,  0.4f,  2.5f) },

        // ── Wheelchair ─────────────────────────────────────────────────────
        { "wheelchair:hover_enter", new AnimConfig(WalkFwd,  WalkFwdState,  0.4f,  2.0f) },
        { "wheelchair:grab",        new AnimConfig(IdleCtrl, IdleState,     0.4f)        },
        { "wheelchair:release",     new AnimConfig(WalkFwd,  WalkFwdState,  0.35f, 3.0f) },

        // ── Trolley ────────────────────────────────────────────────────────
        { "trolley:grab",           new AnimConfig(WalkFwd,  WalkFwdState,  0.3f,  4.0f) },
        { "trolley:release",        new AnimConfig(IdleCtrl, IdleState,     0.4f)        },

        // ── Utility cart ───────────────────────────────────────────────────
        { "utility:grab",           new AnimConfig(WalkLeft, WalkLeftState, 0.3f,  3.0f) },
        { "utility:release",        new AnimConfig(IdleCtrl, IdleState,     0.4f)        },

        // ── Patient ────────────────────────────────────────────────────────
        { "patient:hover_enter",    new AnimConfig(Talking,  TalkingState,  0.3f,  5.0f) },
        { "patient:grab",           new AnimConfig(WalkFwd,  WalkFwdState,  0.4f,  4.0f) },
        { "patient:activate",       new AnimConfig(Talking,  TalkingState,  0.25f, 5.0f) },

        // ── Folder / chart ─────────────────────────────────────────────────
        { "folder:hover_enter",     new AnimConfig(IdleCtrl, IdleState,     0.3f)        },
        { "folder:grab",            new AnimConfig(Talking,  TalkingState,  0.3f,  5.0f) },
        { "folder:activate",        new AnimConfig(Talking,  TalkingState,  0.25f, 6.0f) },

        // ── Monitor (both "monitor" and "screen" prefixes) ─────────────────
        { "monitor:hover_enter",    new AnimConfig(Talking,  TalkingState,  0.3f,  4.0f) },
        { "monitor:grab",           new AnimConfig(Talking,  TalkingState,  0.25f, 5.0f) },
        { "screen:hover_enter",     new AnimConfig(Talking,  TalkingState,  0.3f,  4.0f) },
        { "screen:grab",            new AnimConfig(Talking,  TalkingState,  0.25f, 5.0f) },

        // ── Reception / desk ───────────────────────────────────────────────
        { "reception:hover_enter",  new AnimConfig(WalkFwd,  WalkFwdState,  0.3f,  2.0f) },
        { "reception:grab",         new AnimConfig(Talking,  TalkingState,  0.25f, 4.0f) },
        { "desk:hover_enter",       new AnimConfig(WalkFwd,  WalkFwdState,  0.3f,  2.0f) },
        { "desk:grab",              new AnimConfig(Talking,  TalkingState,  0.25f, 4.0f) },

        // ── Sanitizer dispenser ────────────────────────────────────────────
        { "sanitizer:grab",         new AnimConfig(IdleCtrl, IdleState,     0.2f,  2.0f) },
        { "sanitizer:activate",     new AnimConfig(IdleCtrl, IdleState,     0.2f,  2.0f) },

        // ── Waste bin ──────────────────────────────────────────────────────
        { "waste:grab",             new AnimConfig(WalkFwd,  WalkFwdState,  0.3f,  2.0f) },

        // ── Caution / sign ─────────────────────────────────────────────────
        { "caution:hover_enter",    new AnimConfig(IdleCtrl, IdleState,     0.3f)        },
        { "caution:grab",           new AnimConfig(WalkBack, WalkBackState, 0.3f,  2.0f) },
        { "sign:hover_enter",       new AnimConfig(IdleCtrl, IdleState,     0.3f)        },
        { "sign:grab",              new AnimConfig(WalkBack, WalkBackState, 0.3f,  2.0f) },

        // ── Table / plinth ─────────────────────────────────────────────────
        { "table:hover_enter",      new AnimConfig(WalkFwd,  WalkFwdState,  0.4f,  1.5f) },
        { "table:grab",             new AnimConfig(WalkFwd,  WalkFwdState,  0.3f,  2.0f) },
        { "plinth:hover_enter",     new AnimConfig(WalkFwd,  WalkFwdState,  0.4f,  1.5f) },
        { "plinth:grab",            new AnimConfig(WalkFwd,  WalkFwdState,  0.3f,  2.0f) },
        { "mat:hover_enter",        new AnimConfig(WalkFwd,  WalkFwdState,  0.4f,  1.5f) },
        { "mat:grab",               new AnimConfig(WalkFwd,  WalkFwdState,  0.3f,  2.0f) },

        // ── Towel dispenser ────────────────────────────────────────────────
        { "towel:grab",             new AnimConfig(IdleCtrl, IdleState,     0.2f)        },
        { "towel:activate",         new AnimConfig(IdleCtrl, IdleState,     0.2f)        },

        // ── Clock ──────────────────────────────────────────────────────────
        { "clock:hover_enter",      new AnimConfig(IdleCtrl, IdleState,     0.3f)        },

        // ── Poster ─────────────────────────────────────────────────────────
        { "poster:hover_enter",     new AnimConfig(IdleCtrl, IdleState,     0.3f)        },
        { "poster:grab",            new AnimConfig(IdleCtrl, IdleState,     0.3f)        },
    };

    // ── Inspector fields ──────────────────────────────────────────────────────

    [Tooltip("Seconds between rescans for newly-spawned interactables.")]
    public float rescanInterval = 2f;

    [Tooltip("Highlight color flashed on the interacted object.")]
    public Color highlightColor = new Color(0.3f, 0.8f, 1.0f);

    [Tooltip("Automatically add XR interaction components to pre-placed scene objects that don't have them yet.")]
    public bool autoMakeSceneObjectsInteractable = true;


    // ── Character categories ──────────────────────────────────────────────────
    // Objects in these categories are themselves character prefabs; when the player
    // interacts with them we animate the character directly rather than defaultHumanModel.
    private static readonly HashSet<string> CharacterCategories = new HashSet<string>
        { "patient", "doctor", "nurse", "surgeon", "therapist", "emt", "infectiologist" };

    // ── Runtime state ─────────────────────────────────────────────────────────

    private EnvironmentAgent _envAgent;
    private InteractionSetup _interactionSetup;
    private readonly HashSet<int> _subscribedIds = new HashSet<int>();

    // Single pending idle-return coroutine (EnvironmentAgent targets defaultHumanModel)
    private Coroutine _pendingIdle;

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    private void Awake()
    {
        _envAgent = GetComponent<EnvironmentAgent>()
                 ?? FindFirstObjectByType<EnvironmentAgent>();

        _interactionSetup = GetComponent<InteractionSetup>()
                         ?? FindFirstObjectByType<InteractionSetup>();

        if (_envAgent == null)
            Debug.LogWarning("[InteractionAnimationController] EnvironmentAgent not found — animations will not play.");
        else
            Debug.Log("[InteractionAnimationController] Awake — EnvironmentAgent found.");

        if (_interactionSetup == null)
            Debug.LogWarning("[InteractionAnimationController] InteractionSetup not found — pre-placed objects will not be auto-made interactable.");
    }

    private void Start()
    {
        // Auto-add XR interaction components to pre-placed scene objects so they
        // fire hover/grab/activate events even before the Python agent spawns anything.
        if (autoMakeSceneObjectsInteractable && _interactionSetup != null)
            MakeSceneObjectsInteractable();

        // Run one immediate scan so objects already in the scene at startup are covered
        SubscribeToNewInteractables();

        Debug.Log("[InteractionAnimationController] Start complete. Animations will target EnvironmentAgent.defaultHumanModel.");
    }

    private float _nextRescan;

    private void Update()
    {
        if (Time.time >= _nextRescan)
        {
            _nextRescan = Time.time + rescanInterval;
            SubscribeToNewInteractables();
        }
    }

    // ── Auto-setup ────────────────────────────────────────────────────────────

    /// <summary>
    /// Walk every root GameObject in the scene and call MakeInteractable on
    /// anything that has a Renderer (i.e. is visible) but not yet an
    /// XRBaseInteractable.  Structural objects (walls, floor, etc.) are
    /// skipped by InteractionSetup.MakeInteractable itself.
    /// </summary>
    private void MakeSceneObjectsInteractable()
    {
        int count = 0;
        // GetComponentsInChildren on every root covers the full hierarchy
        var allRenderers = FindObjectsByType<Renderer>(FindObjectsSortMode.None);
        var seen = new HashSet<int>();

        foreach (var r in allRenderers)
        {
            // transform.root gives the topmost parent in the scene hierarchy,
            // which is the prefab/prop root for direct scene objects.
            GameObject root = r.transform.root.gameObject;

            int id = root.GetInstanceID();
            if (seen.Contains(id)) continue;
            seen.Add(id);

            // Skip if already interactable anywhere in the hierarchy
            if (root.GetComponentInChildren<XRBaseInteractable>() != null) continue;

            _interactionSetup.MakeInteractable(root);
            count++;
        }

        if (count > 0)
            Debug.Log($"[InteractionAnimationController] Auto-made {count} scene object(s) interactable.");
        else
            Debug.Log("[InteractionAnimationController] No new scene objects needed interaction setup.");
    }

    // ── Subscription ─────────────────────────────────────────────────────────

    private void SubscribeToNewInteractables()
    {
        var interactables = FindObjectsByType<XRBaseInteractable>(FindObjectsSortMode.None);
        int newCount = 0;

        foreach (var ia in interactables)
        {
            int id = ia.GetInstanceID();
            if (_subscribedIds.Contains(id)) continue;
            _subscribedIds.Add(id);
            newCount++;

            string objName   = ia.gameObject.name;
            string category  = GetCategory(objName);
            bool   isMapped  = false;
            foreach (string evt in new[] { "grab", "hover_enter", "activate", "release" })
                if (AnimMap.ContainsKey($"{category}:{evt}")) { isMapped = true; break; }

            Debug.Log($"[InteractionAnimationController] Subscribed: {objName}  category=\"{category}\"  mapped={isMapped}");

            // Capture local ref — lambdas inside foreach need this
            XRBaseInteractable captured = ia;
            ia.hoverEntered.AddListener(_ => OnInteract("hover_enter", captured.gameObject));
            ia.hoverExited.AddListener(_  => OnInteract("hover_exit",  captured.gameObject));
            ia.selectEntered.AddListener(_ => OnInteract("grab",        captured.gameObject));
            ia.selectExited.AddListener(_  => OnInteract("release",     captured.gameObject));
            ia.activated.AddListener(_     => OnInteract("activate",    captured.gameObject));
        }

        if (newCount > 0)
            Debug.Log($"[InteractionAnimationController] +{newCount} interactable(s) this scan ({_subscribedIds.Count} total tracked)");
    }

    // ── Event handler ─────────────────────────────────────────────────────────

    private void OnInteract(string eventType, GameObject target)
    {
        if (eventType == "hover_exit") return;

        string cleanName = target.name.Replace("(Clone)", "").Trim();
        string category  = GetCategory(target.name);
        string key       = $"{category}:{eventType}";

        Debug.Log($"[InteractionAnimationController] {eventType} → {cleanName}  (key=\"{key}\")");

        // ── Resolve character root ────────────────────────────────────────────
        // For character-category objects, use the prefab root (transform.root) as both the
        // highlight target and the animation target.  The Animator may live on a sub-bone
        // (e.g. Kevin Iglesias "B-root"), so targeting the bone directly would give a
        // GameObject with no Renderer children.  Using the prefab root means:
        //   • HighlightCoroutine finds all renderers via GetComponentsInChildren
        //   • PlayHumanAnimationFromResources finds the primary Animator via FindPrimaryAnimator
        GameObject animTarget = null;
        if (CharacterCategories.Contains(category))
        {
            animTarget = target.transform.root.gameObject;
            Debug.Log($"[InteractionAnimationController] Character root: {animTarget.name}");
        }

        // ── Always highlight the touched/hovered object ───────────────────────
        if (_envAgent != null)
        {
            float hlDuration = eventType == "hover_enter" ? 0.6f : 1.5f;
            if (animTarget != null)
                _envAgent.HighlightObject(animTarget,
                    highlightColor.r, highlightColor.g, highlightColor.b, hlDuration);
            else
                _envAgent.HighlightObject(cleanName,
                    highlightColor.r, highlightColor.g, highlightColor.b, hlDuration);
        }

        // ── Look up NPC animation (all animations target the NPC character) ──
        AnimConfig cfg;
        if (!AnimMap.TryGetValue(key, out cfg))
        {
            // Fallback for grab/activate so there's always some NPC reaction
            if (eventType == "grab" || eventType == "activate")
                cfg = new AnimConfig(IdleCtrl, IdleState, 0.3f);
            else
                return;  // no animation for unmapped hover/release
        }

        if (_envAgent == null) return;

        // Cancel any pending idle-return
        if (_pendingIdle != null)
        {
            StopCoroutine(_pendingIdle);
            _pendingIdle = null;
        }

        _envAgent.PlayHumanAnimationFromResources(animTarget, cfg.Controller, cfg.State, cfg.Fade);
        Debug.Log($"[InteractionAnimationController] Animation: {cfg.State} → {(animTarget != null ? animTarget.name : "defaultNPC")}");

        if (cfg.IdleDelay > 0f)
            _pendingIdle = StartCoroutine(IdleAfterDelay(cfg.IdleDelay));
    }

    // ── Idle scheduling ───────────────────────────────────────────────────────

    private IEnumerator IdleAfterDelay(float delay)
    {
        yield return new WaitForSeconds(delay);
        _pendingIdle = null;

        if (_envAgent != null)
            _envAgent.PlayHumanAnimationFromResources(null, IdleCtrl, IdleState, 0.5f);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /// <summary>
    /// Extract the category token from an object name: the lowercase prefix
    /// before the first underscore or digit.
    ///   "Treadmill_01"    → "treadmill"
    ///   "WheelChair_01"   → "wheelchair"
    ///   "Utility_Cart_01" → "utility"
    ///   "Screen_02"       → "screen"
    /// </summary>
    private static string GetCategory(string objName)
    {
        if (string.IsNullOrEmpty(objName)) return "";
        objName = objName.Replace("(Clone)", "").Trim();

        var sb = new System.Text.StringBuilder();
        foreach (char ch in objName)
        {
            if (ch == '_' || char.IsDigit(ch)) break;
            sb.Append(ch);
        }
        return sb.ToString().ToLower();
    }

}
