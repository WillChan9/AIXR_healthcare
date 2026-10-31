"""
Phase 2, Layer 2: scripted story chain.

Phase 2 reacts to player interactions in two layers:

* Layer 1 (``interaction_animations.InteractionAnimationHandler``) gives every
  object an ambient response from ``interaction_map.json``.
* Layer 2 (this module) plays a linear, scripted narrative on top of that.

``StoryChainHandler`` reads ``api_files/story_chain.json`` (written at the end
of Phase 1 by ``generate_story_chain`` in ``groq_static_test.py``) and keeps a
single cursor into its list of beats. When a player event matches the current
beat's trigger, the beat's actions are sent to Unity via ``main.add_command``
(i.e. appended to ``unity_commands.json``) and the cursor advances.

Events that fire a beat are reported back as "consumed" so the caller can skip
Layer 1 for them; otherwise the ambient response (e.g. a generic Talking
animation) could overwrite the scripted character animation.

Chain file format (``synopsis`` is ignored here)::

    {"title": "<short title>",
     "synopsis": "<one-sentence summary>",
     "beats": [{"id": "B1",
                "trigger": {"event": "<hover_enter|grab|release|activate|touch>",
                            "object": "<exact objectName>"},
                "actions": [{<AgentNetworkBridge command>}, ...]},
               ...]}

Usage::

    from story_chain import StoryChainHandler      # with Agent/ on sys.path
    chain = StoryChainHandler()
    consumed_ids = chain.process_events_list(new_events)

Running ``python Agent/story_chain.py [path/to/story_chain.json]`` walks the
chain with synthetic events (note: this does append the beats' actions to the
Unity command file).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrap (lets this module be imported or run directly)
# ---------------------------------------------------------------------------

_AGENT_ROOT = Path(__file__).parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import main as unity  # noqa: E402  Agent/main.py: Unity command-file writer

try:
    from agent.logger import get_logger
except ImportError:
    import logging

    def get_logger(name: str, **_):
        """Fallback logger used when agent.logger is unavailable."""
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s [%(name)s] %(message)s")
        return logging.getLogger(name)

logger = get_logger("StoryChain",
                    module_log_file="api_files/story_chain.log")


_DEFAULT_CHAIN_FILE = _AGENT_ROOT / "api_files" / "story_chain.json"


# ---------------------------------------------------------------------------
# Story chain handler
# ---------------------------------------------------------------------------

class StoryChainHandler:
    """Linear story chain: fires beat N when an event matches its trigger, then moves to N+1."""

    def __init__(self, chain_file: Optional[Path] = None):
        self._chain_file: Path = chain_file or _DEFAULT_CHAIN_FILE
        self._beats: List[Dict] = []
        self._cursor: int = 0
        self._title: str = ""
        # Event IDs already consumed, so the same event never advances the chain
        # twice (events can be replayed within a single tick).
        self._consumed_ids: set = set()
        self._load_chain()

    # ---------------------------------------------------------------------------
    # Loading
    # ---------------------------------------------------------------------------

    def _load_chain(self):
        """Read the chain file; leaves the chain empty (inactive) on any problem."""
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
        """Render a beat's trigger as 'event → object' for log messages."""
        t = beat.get("trigger") or {}
        return f"{t.get('event', '?')} → {t.get('object', '?')}"

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """True if there are beats left to fire."""
        return self._cursor < len(self._beats)

    def process_events_list(self, events: List[Dict]) -> List[str]:
        """Try to advance the chain on each event, in order.

        Returns the IDs of events that fired a beat, so the caller can skip
        Layer 1 for them.
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
                    logger.info("[StoryChain] Chain complete — all %d beats fired.",
                                len(self._beats))
        return consumed

    # ---------------------------------------------------------------------------
    # Matching and dispatch
    # ---------------------------------------------------------------------------

    def _matches_current_beat(self, event: Dict) -> bool:
        """True if the event's type and object (minus "(Clone)") match the current trigger."""
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
        """Dispatch every action of a beat; a failing action is logged and skipped."""
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
        """Forward an action dict to Unity via ``main.add_command``.

        No validation is done here; Unity's AgentNetworkBridge checks each action.
        """
        action = act.get("action")
        if not action:
            return
        kwargs = {k: v for k, v in act.items() if k != "action"}
        unity.add_command(action, **kwargs)
        logger.debug("[StoryChain] dispatched %s → %s", action, kwargs)


# ---------------------------------------------------------------------------
# Standalone smoke test (Unity need not be running)
# ---------------------------------------------------------------------------

def _smoke_test():
    """Walk a chain file with one synthetic event per beat and print progress.

    Usage: python story_chain.py [path/to/story_chain.json]
    """
    chain_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_CHAIN_FILE
    handler = StoryChainHandler(chain_file=chain_path)
    if not handler.is_active:
        print(f"No active chain at {chain_path}.")
        return

    # Synthesize one event per beat from the beat's own trigger.
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
