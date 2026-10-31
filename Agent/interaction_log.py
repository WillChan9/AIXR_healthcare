"""
Persistent interaction log for Phase 2.

Records each player interaction event, which animation (if any) it triggered,
whether that choice came from the LLM or the static interaction map, and the
reasoning behind it. Entries are kept in ``api_files/interaction_log.json`` as a
JSON array capped at the most recent 200 entries.

Writers: ``interaction_animations.InteractionAnimationHandler`` (map-driven
responses) and ``agent/animation_planner.py`` (LLM-chosen responses), which
also reads recent entries back as context.

Usage (with Agent/ on sys.path)::

    from interaction_log import InteractionLog
    log = InteractionLog()
    log.append("grab", "Treadmill_01", animation_triggered="HumanM@Walk01_Forward",
               llm_chosen=False, reasoning="deterministic map")
    recent = log.recent(n=5)
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_AGENT_ROOT  = Path(__file__).parent          # Agent/
_DEFAULT_LOG = _AGENT_ROOT / "api_files" / "interaction_log.json"
_MAX_ENTRIES = 200                            # oldest entries are dropped beyond this


class InteractionLog:
    """Thread-safe, append-only interaction log backed by a JSON file."""

    def __init__(self, log_path: Optional[Path] = None):
        """Open (creating if needed) the log at ``log_path`` or api_files/interaction_log.json."""
        self._path = log_path or _DEFAULT_LOG
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def append(
        self,
        event_type: str,
        object_name: str,
        *,
        animation_triggered: Optional[str] = None,
        llm_chosen: bool = False,
        reasoning: str = "",
        player_pos: Optional[Any] = None,
    ) -> Dict:
        """Append one interaction record and return it."""
        now = time.time()
        entry = {
            "id":                  f"log_{int(now * 1000)}",
            "timestamp":           now,
            "iso_time":            datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "event_type":          event_type,
            "object_name":         object_name,
            "animation_triggered": animation_triggered,
            "llm_chosen":          llm_chosen,
            "reasoning":           reasoning,
            "player_position":     player_pos,
        }
        with self._lock:
            entries = self._load()
            entries.append(entry)
            if len(entries) > _MAX_ENTRIES:
                entries = entries[-_MAX_ENTRIES:]
            self._path.write_text(
                json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return entry

    def touch_count_or_append(
        self,
        object_name: str,
        *,
        reasoning: str = "",
        player_pos: Optional[Any] = None,
    ) -> Dict:
        """
        Record a touch event, collapsing consecutive touches on one object.

        If the most recent entry is already a touch on ``object_name``, its
        ``touch_count`` is incremented instead of appending a new entry, so
        callers can use ``touch_count`` to decide whether to keep responding.
        Returns the new or updated entry.
        """
        now = time.time()
        with self._lock:
            entries = self._load()
            if entries:
                last = entries[-1]
                if (last.get("event_type") == "touch"
                        and last.get("object_name") == object_name):
                    last["touch_count"] = last.get("touch_count", 1) + 1
                    last["last_touch_time"] = now
                    last["reasoning"] = reasoning
                    self._path.write_text(
                        json.dumps(entries, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return last
            # No matching prior touch: create a new entry.
            entry = {
                "id":                  f"log_{int(now * 1000)}",
                "timestamp":           now,
                "iso_time":            datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                "event_type":          "touch",
                "object_name":         object_name,
                "touch_count":         1,
                "last_touch_time":     now,
                "animation_triggered": None,
                "llm_chosen":          False,
                "reasoning":           reasoning,
                "player_position":     player_pos,
            }
            entries.append(entry)
            if len(entries) > _MAX_ENTRIES:
                entries = entries[-_MAX_ENTRIES:]
            self._path.write_text(
                json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return entry

    def recent(self, n: int = 10) -> List[Dict]:
        """Return the last *n* log entries (newest last)."""
        with self._lock:
            entries = self._load()
        return entries[-n:]

    def all(self) -> List[Dict]:
        """Return all log entries."""
        with self._lock:
            return self._load()

    def clear(self):
        """Erase all log entries."""
        with self._lock:
            self._path.write_text("[]", encoding="utf-8")

    # ---------------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------------

    def _load(self) -> List[Dict]:
        """Read all entries (caller holds the lock); returns [] if unreadable."""
        try:
            text = self._path.read_text(encoding="utf-8").strip()
            if not text or text == "[]":
                return []
            return json.loads(text)
        except Exception:
            return []
