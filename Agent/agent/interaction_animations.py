"""
Phase 2 – Interaction Animation Handler.

Polls interaction_events.json (written by InteractionEventReporter.cs)
and responds with appropriate human animations via unity_commands.json.

Trigger types captured by Unity:
    hover_enter  – player looks at / ray-hits the object
    hover_exit   – player looks away
    grab         – player selects / grabs the object
    release      – player releases the object
    activate     – player presses "activate" while holding

Animation controllers live in  Resources/AnimatorControllers/.
State names match the controller filename (Kevin Iglesias Human Animations pack).

Usage (standalone):
    python -m agent.interaction_animations

Usage (embedded in main loop):
    from agent.interaction_animations import InteractionAnimationHandler
    handler = InteractionAnimationHandler()
    while True:
        handler.tick()
        time.sleep(0.5)
"""

from __future__ import annotations

import json
import time
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── path bootstrap so this module can be imported OR run directly ──────────
_AGENT_DIR   = Path(__file__).parent          # Agent/agent/
_AGENT_ROOT  = _AGENT_DIR.parent              # Agent/
_PROJECT_ROOT = _AGENT_ROOT.parent            # repo root

if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import main as unity  # Agent/main.py  (play_human_animation, add_command)

try:
    from .logger import get_logger
except ImportError:
    import logging
    def get_logger(name: str, **_):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s [%(name)s] %(message)s")
        return logging.getLogger(name)

logger = get_logger("InteractionAnimations",
                    module_log_file="api_files/interaction_animations.log")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EVENTS_FILE = (
    _PROJECT_ROOT
    / "firefighter project"
    / "Assets"
    / "Scripts"
    / "interaction_events.json"
)

_IDLE_CTRL  = "AnimatorControllers/HumanM@Idles"
_IDLE_STATE = "HumanM@Idles"

# ---------------------------------------------------------------------------
# Animation map
#
# Key   : (object_category, event_type)
# Value : {
#   controller  : Resources path for RuntimeAnimatorController (no extension)
#   state       : Animator state name to CrossFade into
#   fade        : crossFadeTime (seconds)
#   idle_delay  : seconds until return-to-idle is queued (0 = no auto-return)
# }
#
# object_category is derived from the object name by taking the lowercase
# prefix before the first underscore or digit, e.g.:
#   "Treadmill_01"   → "treadmill"
#   "WheelChair_01"  → "wheelchair"
#   "Utility_Cart_01"→ "utility"
#   "Patient_01"     → "patient"
# ---------------------------------------------------------------------------

ANIMATION_MAP: Dict[tuple, Dict[str, Any]] = {

    # ── Treadmill ──────────────────────────────────────────────────────────
    # Grab  → patient steps on and begins walking
    ("treadmill", "grab"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.35, "idle_delay": 6.0,
    },
    # Release → patient steps off, return to idle
    ("treadmill", "release"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.5,
    },
    # Hover → approach the machine
    ("treadmill", "hover_enter"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.4, "idle_delay": 2.5,
    },

    # ── WheelChair ─────────────────────────────────────────────────────────
    # Hover → therapist approaches wheelchair
    ("wheelchair", "hover_enter"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.4, "idle_delay": 2.0,
    },
    # Grab → patient is helped into it; therapist goes to idle (assisting)
    ("wheelchair", "grab"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.4,
    },
    # Release → push wheelchair → walk forward
    ("wheelchair", "release"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.35, "idle_delay": 3.0,
    },

    # ── Trolley ────────────────────────────────────────────────────────────
    # Grab → push trolley forward
    ("trolley", "grab"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.3, "idle_delay": 4.0,
    },
    ("trolley", "release"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.4,
    },

    # ── Utility Cart ───────────────────────────────────────────────────────
    # Grab → move cart sideways (strafing)
    ("utility", "grab"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Left",
        "state": "HumanM@Walk01_Left", "fade": 0.3, "idle_delay": 3.0,
    },
    ("utility", "release"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.4,
    },

    # ── Patient ────────────────────────────────────────────────────────────
    # Hover → therapist greets / checks on patient
    ("patient", "hover_enter"): {
        "controller": "AnimatorControllers/HumanM@Talking",
        "state": "HumanM@Talking", "fade": 0.3, "idle_delay": 5.0,
    },
    # Grab → guide the patient (walk forward together)
    ("patient", "grab"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.4, "idle_delay": 4.0,
    },
    # Activate → therapist gives instructions
    ("patient", "activate"): {
        "controller": "AnimatorControllers/HumanM@Talking",
        "state": "HumanM@Talking", "fade": 0.25, "idle_delay": 5.0,
    },

    # ── Folder / Chart ─────────────────────────────────────────────────────
    # Grab → review the chart
    ("folder", "grab"): {
        "controller": "AnimatorControllers/HumanM@Talking",
        "state": "HumanM@Talking", "fade": 0.3, "idle_delay": 5.0,
    },
    ("folder", "activate"): {
        "controller": "AnimatorControllers/HumanM@Talking",
        "state": "HumanM@Talking", "fade": 0.25, "idle_delay": 6.0,
    },
    ("folder", "hover_enter"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.3,
    },

    # ── Monitor ────────────────────────────────────────────────────────────
    # Hover → therapist explains results on screen
    ("monitor", "hover_enter"): {
        "controller": "AnimatorControllers/HumanM@Talking",
        "state": "HumanM@Talking", "fade": 0.3, "idle_delay": 4.0,
    },
    ("monitor", "grab"): {
        "controller": "AnimatorControllers/HumanM@Talking",
        "state": "HumanM@Talking", "fade": 0.25, "idle_delay": 5.0,
    },

    # ── Reception Desk ─────────────────────────────────────────────────────
    # Hover → approaching desk
    ("reception", "hover_enter"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.3, "idle_delay": 2.0,
    },
    ("reception", "grab"): {
        "controller": "AnimatorControllers/HumanM@Talking",
        "state": "HumanM@Talking", "fade": 0.25, "idle_delay": 4.0,
    },

    # ── Sanitizer Dispenser ────────────────────────────────────────────────
    # Grab / activate → brief pause (cleaning hands), then idle
    ("sanitizer", "grab"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.2, "idle_delay": 2.0,
    },
    ("sanitizer", "activate"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.2, "idle_delay": 2.0,
    },

    # ── Waste Bucket ───────────────────────────────────────────────────────
    # Grab → walk toward bin to dispose
    ("waste", "grab"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.3, "idle_delay": 2.0,
    },

    # ── Caution Sign ───────────────────────────────────────────────────────
    # Hover → stop and notice sign (idle)
    ("caution", "hover_enter"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.3,
    },
    # Grab → back away carefully
    ("caution", "grab"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Backward",
        "state": "HumanM@Walk01_Backward", "fade": 0.3, "idle_delay": 2.0,
    },

    # ── Table (mat / therapy table) ────────────────────────────────────────
    ("table", "grab"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.3, "idle_delay": 2.0,
    },
    ("table", "hover_enter"): {
        "controller": "AnimatorControllers/HumanM@Walk01_Forward",
        "state": "HumanM@Walk01_Forward", "fade": 0.4, "idle_delay": 1.5,
    },

    # ── Towel Dispenser ────────────────────────────────────────────────────
    ("towel", "grab"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.2,
    },
    ("towel", "activate"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.2,
    },

    # ── Clock ──────────────────────────────────────────────────────────────
    ("clock", "hover_enter"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.3,
    },

    # ── Poster ─────────────────────────────────────────────────────────────
    ("poster", "hover_enter"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.3,
    },
    ("poster", "grab"): {
        "controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.3,
    },
}

# Fallback if the exact (category, event) is not in the map
_FALLBACK: Dict[str, Dict[str, Any]] = {
    "grab":        {"controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.3},
    "activate":    {"controller": _IDLE_CTRL, "state": _IDLE_STATE, "fade": 0.25},
    "hover_enter": None,   # most hovers with no mapping are silently ignored
    "hover_exit":  None,
    "release":     None,
}

# Interactions that are low-value to respond to unless explicitly mapped
_SILENT_TYPES = {"hover_exit"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _object_category(name: str) -> str:
    """
    Derive a category token from a Unity object name.

    Examples
    --------
    "Treadmill_01"    → "treadmill"
    "WheelChair_01"   → "wheelchair"
    "Utility_Cart_01" → "utility"
    "Patient_01"      → "patient"
    "Reception_Desk_01" → "reception"
    """
    if not name:
        return ""
    # Drop "(Clone)" suffix Unity adds on Instantiate
    name = name.replace("(Clone)", "").strip()
    # Take the part before the first underscore or digit
    token = ""
    for ch in name:
        if ch == "_" or ch.isdigit():
            break
        token += ch
    return token.lower()


# ---------------------------------------------------------------------------
# Main handler class
# ---------------------------------------------------------------------------

class InteractionAnimationHandler:
    """
    Polls interaction_events.json and drives human character animations
    in response to player interactions with scene objects.
    """

    def __init__(self, events_file: Optional[Path] = None):
        self._events_file: Path = events_file or _EVENTS_FILE
        # IDs we have already acted on (survive across ticks)
        self._processed_ids: set = set()
        # Scheduled idle returns: list of (trigger_at_epoch, ctrl, state)
        self._pending_idles: List[tuple] = []

    # ── public API ────────────────────────────────────────────────────────

    def tick(self) -> int:
        """
        Standalone polling cycle — reads the events file, fires animations,
        and marks events as processed in the file.

        Use this when running the handler independently (not embedded in the
        groq_static_test phase2 loop, which manages file marking itself).

        Returns the number of events that triggered an animation.
        """
        self._fire_pending_idles()
        events = self._load_events()
        triggered = 0
        for event in events:
            eid = event.get("id", "")
            if event.get("processed") or eid in self._processed_ids:
                continue
            if self._handle_event(event):
                triggered += 1
            self._processed_ids.add(eid)
            self._mark_processed(eid)
        return triggered

    def process_events_list(self, events: List[dict]) -> int:
        """
        Fire animations for a pre-loaded list of events without touching the
        events file.  Use this when the caller (e.g. phase2_interaction_loop)
        already loaded and will mark the events itself.

        Also fires any overdue idle-return animations.

        Returns the number of events that triggered an animation.
        """
        self._fire_pending_idles()
        triggered = 0
        for event in events:
            eid = event.get("id", "")
            if eid in self._processed_ids:
                continue
            if self._handle_event(event):
                triggered += 1
            self._processed_ids.add(eid)
        return triggered

    def run(self, poll_interval: float = 0.5):
        """Block and poll indefinitely.  Ctrl-C to stop."""
        logger.info("InteractionAnimationHandler running (poll=%.1fs) …", poll_interval)
        while True:
            try:
                n = self.tick()
                if n:
                    logger.debug("Processed %d new interaction event(s)", n)
            except KeyboardInterrupt:
                logger.info("Stopped.")
                break
            except Exception as exc:
                logger.error("tick() error: %s", exc)
            time.sleep(poll_interval)

    # ── event handling ────────────────────────────────────────────────────

    def _handle_event(self, event: dict) -> bool:
        """
        Look up the animation config for this event and dispatch it.
        Returns True if an animation was triggered.
        """
        event_type = event.get("type", "")
        obj_name   = event.get("objectName", "")
        category   = _object_category(obj_name)

        # Skip event types that are universally silent
        if event_type in _SILENT_TYPES:
            return False

        # Look up specific mapping, then fallback
        config = ANIMATION_MAP.get((category, event_type))
        if config is None:
            config = _FALLBACK.get(event_type)
        if config is None:
            return False

        logger.info(
            "[%s] %s (%s) → %s",
            event_type, obj_name, category, config["state"],
        )

        # Cancel any previously queued idle return — new interaction takes over
        self._pending_idles.clear()

        self._play(config["controller"], config["state"], config.get("fade", 0.25))

        idle_delay = config.get("idle_delay", 0.0)
        if idle_delay > 0:
            self._schedule_idle(idle_delay)

        # Highlight the object so the player gets visual feedback
        self._highlight(obj_name, duration=min(idle_delay, 2.0) if idle_delay else 1.0)

        return True

    # ── animation helpers ─────────────────────────────────────────────────

    def _play(self, controller: str, state: str, fade: float = 0.25):
        """Send PlayHumanAnimationFromResources command (targets NPC/defaultHumanModel)."""
        unity.play_human_animation(controller, state, fade)

    def _schedule_idle(self, delay: float):
        """Queue a return-to-idle N seconds from now."""
        trigger_at = time.time() + delay
        self._pending_idles.append((trigger_at, _IDLE_CTRL, _IDLE_STATE))

    def _fire_pending_idles(self):
        """Play return-to-idle for any overdue scheduled returns."""
        now  = time.time()
        keep = []
        for trigger_at, ctrl, state in self._pending_idles:
            if now >= trigger_at:
                logger.info("Auto-return to idle (%s)", state)
                self._play(ctrl, state, 0.5)
            else:
                keep.append((trigger_at, ctrl, state))
        self._pending_idles = keep

    def _highlight(self, obj_name: str, duration: float = 1.0):
        """Send a brief highlight pulse on the interacted object."""
        unity.add_command(
            "HighlightObject",
            objectName=obj_name,
            r=0.3, g=0.8, b=1.0,
            duration=duration,
        )

    # ── file I/O ──────────────────────────────────────────────────────────

    def _load_events(self) -> List[dict]:
        """Read the events JSON written by InteractionEventReporter.cs."""
        if not self._events_file.exists():
            return []
        try:
            text = self._events_file.read_text(encoding="utf-8").strip()
            if not text or text == "[]":
                return []
            return json.loads(text)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read events file: %s", exc)
            return []

    def _mark_processed(self, event_id: str):
        """
        Set processed=true for the given event ID in the events file.
        This signals InteractionEventReporter.cs that we handled the event,
        so it won't re-write it as unprocessed on next load.
        """
        if not self._events_file.exists():
            return
        try:
            text   = self._events_file.read_text(encoding="utf-8").strip()
            events = json.loads(text) if text and text != "[]" else []
            changed = False
            for ev in events:
                if ev.get("id") == event_id and not ev.get("processed"):
                    ev["processed"] = True
                    changed = True
            if changed:
                self._events_file.write_text(
                    json.dumps(events, indent=2), encoding="utf-8"
                )
        except Exception as exc:
            logger.warning("_mark_processed failed for %s: %s", event_id, exc)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    handler = InteractionAnimationHandler()
    handler.run(poll_interval=0.5)
