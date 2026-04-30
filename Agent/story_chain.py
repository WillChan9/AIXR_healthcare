"""
Phase 2 — Layer 2: Scripted interaction chain.

Reads story_chain.json (written at the end of Phase 1 by generate_story_chain)
and advances a single cursor through a linear list of beats. A beat fires when
the player produces an event that matches the beat's trigger; firing dispatches
the beat's actions to Unity and advances the cursor to the next beat.

Layer 1 (interaction_animations.InteractionAnimationHandler) handles ambient
interactions for every object. Layer 2 handles the scripted narrative on top.

Beats whose trigger matches an event are "consumed" and the event is reported
back so the caller can suppress the ambient handler for the same event (avoids
the ambient Talking command overwriting the scripted character animation).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── path bootstrap so this module can be imported OR run directly ──────────
_AGENT_ROOT = Path(__file__).parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import main as unity  # Agent/main.py — add_command()

try:
    from agent.logger import get_logger
except ImportError:
    import logging
    def get_logger(name: str, **_):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s [%(name)s] %(message)s")
        return logging.getLogger(name)

logger = get_logger("StoryChain",
                    module_log_file="api_files/story_chain.log")


_DEFAULT_CHAIN_FILE = _AGENT_ROOT / "api_files" / "story_chain.json"


class StoryChainHandler:
    """Linear story chain — fires beat N when its trigger matches an event,
    then advances cursor to beat N+1."""

    def __init__(self, chain_file: Optional[Path] = None):
        self._chain_file: Path = chain_file or _DEFAULT_CHAIN_FILE
        self._beats: List[Dict] = []
        self._cursor: int = 0
        self._title: str = ""
        # Track which event IDs we've consumed so we never advance twice on the
        # same event (events can be replayed within a single tick).
        self._consumed_ids: set = set()
        self._load_chain()

    # ── loading ───────────────────────────────────────────────────────────

    def _load_chain(self):
        if not self._chain_file.exists():
            logger.info("[StoryChain] No story_chain.json — Layer 2 inactive.")
            return
        try:
            data = json.loads(self._chain_file.read_text(encoding="utf-8"))
            self._title = data.get("title", "")
            self._beats = data.get("beats", []) or []
            if self._beats:
                logger.info(
                    "[StoryChain] Loaded '%s' with %d beat(s). First trigger: %s",
                    self._title, len(self._beats), self._format_trigger(self._beats[0]),
                )
            else:
                logger.info("[StoryChain] Chain file present but empty — Layer 2 inactive.")
        except Exception as exc:
            logger.warning("[StoryChain] Could not load chain: %s", exc)
            self._beats = []

    @staticmethod
    def _format_trigger(beat: Dict) -> str:
        t = beat.get("trigger") or {}
        return f"{t.get('event', '?')} → {t.get('object', '?')}"

    # ── public API ────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """True iff there are beats remaining to fire."""
        return self._cursor < len(self._beats)

    def process_events_list(self, events: List[Dict]) -> List[str]:
        """Try to advance the chain on each event. Returns the list of event IDs
        that were consumed by the chain (so the caller can skip Layer 1 for them).
        """
        consumed: List[str] = []
        if not self.is_active:
            return consumed

        for ev in events:
            eid = ev.get("id", "")
            if eid and eid in self._consumed_ids:
                continue
            if not self.is_active:
                break
            if self._matches_current_beat(ev):
                self._fire_beat(self._beats[self._cursor], ev)
                self._cursor += 1
                if eid:
                    self._consumed_ids.add(eid)
                    consumed.append(eid)
                if self.is_active:
                    logger.info(
                        "[StoryChain] Advanced to beat %d/%d. Next trigger: %s",
                        self._cursor + 1, len(self._beats),
                        self._format_trigger(self._beats[self._cursor]),
                    )
                else:
                    logger.info("[StoryChain] Chain complete — all %d beats fired.", len(self._beats))
        return consumed

    # ── matching + dispatch ───────────────────────────────────────────────

    def _matches_current_beat(self, event: Dict) -> bool:
        beat = self._beats[self._cursor]
        trig = beat.get("trigger") or {}
        want_event = trig.get("event")
        want_object = trig.get("object")
        if not want_event or not want_object:
            return False
        ev_type = event.get("type", "")
        ev_obj = (event.get("objectName") or "").replace("(Clone)", "").strip()
        return ev_type == want_event and ev_obj == want_object

    def _fire_beat(self, beat: Dict, triggering_event: Dict):
        beat_id = beat.get("id", f"#{self._cursor}")
        actions = beat.get("actions") or []
        logger.info(
            "[StoryChain] Firing beat %s (%s) with %d action(s)",
            beat_id, self._format_trigger(beat), len(actions),
        )
        for act in actions:
            try:
                self._dispatch_action(act)
            except Exception as exc:
                logger.warning("[StoryChain] Action failed (%s): %s", act.get("action", "?"), exc)

    def _dispatch_action(self, act: Dict):
        """Forward the action dict to Unity via the generic add_command channel.
        Unity's AgentNetworkBridge already validates each action shape."""
        action = act.get("action")
        if not action:
            return
        kwargs = {k: v for k, v in act.items() if k != "action"}
        unity.add_command(action, **kwargs)
        logger.debug("[StoryChain] dispatched %s → %s", action, kwargs)


# ---------------------------------------------------------------------------
# Standalone smoke test (no Unity)
# ---------------------------------------------------------------------------

def _smoke_test():
    """Run a synthetic event sequence against a chain file and print progress.
    Usage: python story_chain.py [path/to/story_chain.json]
    """
    chain_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_CHAIN_FILE
    handler = StoryChainHandler(chain_file=chain_path)
    if not handler.is_active:
        print(f"No active chain at {chain_path}.")
        return

    # Synthesize one event per beat using the beat's own trigger
    print(f"\nFiring {len(handler._beats)} synthetic events to walk the chain:\n")
    for i, beat in enumerate(handler._beats):
        trig = beat.get("trigger") or {}
        evt = {
            "id": f"smoke_{i}",
            "type": trig.get("event"),
            "objectName": trig.get("object"),
        }
        consumed = handler.process_events_list([evt])
        print(f"  step {i + 1}: trigger={evt['type']}→{evt['objectName']}  "
              f"consumed={bool(consumed)}  cursor={handler._cursor}")
    print(f"\nChain active after walk-through? {handler.is_active}")


if __name__ == "__main__":
    _smoke_test()
