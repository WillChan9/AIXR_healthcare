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
from typing import Dict, List, Optional

# ── path bootstrap so this module can be imported OR run directly ──────────
_AGENT_ROOT   = Path(__file__).parent          # Agent/
_PROJECT_ROOT = _AGENT_ROOT.parent             # repo root

if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import main as unity  # Agent/main.py  (play_human_animation, add_command)

try:
    from agent.logger import get_logger
except ImportError:
    import logging
    def get_logger(name: str, **_):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s [%(name)s] %(message)s")
        return logging.getLogger(name)

from interaction_log import InteractionLog

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

# Interaction types that are never worth responding to
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

    # Path to the agent-generated per-object map written at the end of Phase 1
    _MAP_FILE = _AGENT_ROOT / "api_files" / "interaction_map.json"

    def __init__(self, events_file: Optional[Path] = None, interaction_log: Optional["InteractionLog"] = None):
        self._events_file: Path = events_file or _EVENTS_FILE
        # IDs we have already acted on (survive across ticks)
        self._processed_ids: set = set()
        # Scheduled idle returns: list of (trigger_at_epoch, ctrl, state)
        self._pending_idles: List[tuple] = []
        # Optional persistent log — always created so standalone runs also log
        self._log: InteractionLog = interaction_log or InteractionLog()
        # Agent-generated per-object map: (objectName, event_type) → config dict
        # Loaded from interaction_map.json written at end of Phase 1.
        # Takes priority over the hardcoded ANIMATION_MAP (category-level fallback).
        self._object_map: Dict[tuple, Dict] = {}
        self._load_object_map()

    # ── object map loading ────────────────────────────────────────────────

    def _load_object_map(self):
        """Load the agent-generated interaction_map.json (written at end of Phase 1).
        Entries are keyed by (objectName, event_type) and override the hardcoded
        ANIMATION_MAP when a match is found."""
        if not self._MAP_FILE.exists():
            logger.debug("[InteractionAnimations] No interaction_map.json found — using hardcoded map only.")
            return
        try:
            data = json.loads(self._MAP_FILE.read_text(encoding="utf-8"))
            for obj_name, events in data.items():
                clean = obj_name.replace("(Clone)", "").strip()
                for event_type, cfg in events.items():
                    self._object_map[(clean, event_type)] = cfg
            logger.info(
                "[InteractionAnimations] Loaded agent interaction map: %d entries across %d objects",
                len(self._object_map), len(data),
            )
        except Exception as exc:
            logger.warning("[InteractionAnimations] Could not load interaction_map.json: %s", exc)

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
            self._log.append(
                event_type, obj_name,
                animation_triggered=None,
                llm_chosen=False,
                reasoning="silent event type — no response",
                player_pos=event.get("playerPosition"),
            )
            return False

        # Look up config from the agent-generated per-object map only.
        clean_name = obj_name.replace("(Clone)", "").strip()
        config = self._object_map.get((clean_name, event_type))

        # ── Touch events: deduplicate into a single log entry with a counter ──
        if event_type == "touch":
            entry = self._log.touch_count_or_append(
                obj_name,
                reasoning=f"proximity touch ({category})",
                player_pos=event.get("playerPosition"),
            )
            count = entry.get("touch_count", 1)
            # Only trigger animation on the first touch per object
            if count > 1:
                logger.debug("[touch] %s — repeat #%d, skipping animation", obj_name, count)
                return False
            if config is None:
                logger.debug("[touch] %s (%s) — first touch, no animation mapped", obj_name, category)
                return False
            # First touch with a mapped animation — fall through to dispatch below

        elif config is None:
            reasoning = f"no mapping for ({category}, {event_type})"
            logger.debug("[%s] %s (%s) — %s", event_type, obj_name, category, reasoning)
            self._log.append(
                event_type, obj_name,
                animation_triggered=None,
                llm_chosen=False,
                reasoning=reasoning,
                player_pos=event.get("playerPosition"),
            )
            return False

        anim_state = config["state"]
        logger.info("[%s] %s (%s) → %s", event_type, obj_name, category, anim_state)

        # Cancel any previously queued idle return — new interaction takes over
        self._pending_idles.clear()

        self._play(config["controller"], anim_state, config.get("fade", 0.25))

        idle_delay = config.get("idle_delay", 0.0)
        if idle_delay > 0:
            self._schedule_idle(idle_delay)

        # Highlight the object — skip for touch (PlayerTouchHighlighter already does it
        # and a second concurrent HighlightCoroutine would save the tinted colour as
        # "original" and never restore it, leaving the character permanently blue).
        if event_type != "touch":
            self._highlight(obj_name, duration=min(idle_delay, 2.0) if idle_delay else 1.0)

        # Log the deterministic animation choice (touch already logged above)
        if event_type != "touch":
            self._log.append(
                event_type, obj_name,
                animation_triggered=anim_state,
                llm_chosen=False,
                reasoning=f"static map ({category}, {event_type})",
                player_pos=event.get("playerPosition"),
            )

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
