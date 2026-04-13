"""
Live interaction event watcher — run this in the Claude Code terminal
while Unity is playing to see player interactions in real time.

Usage:
    py Agent/watch_interactions.py
"""

import json
import time
import sys
from pathlib import Path

EVENTS_FILE = (
    Path(__file__).parent.parent
    / "firefighter project"
    / "Assets"
    / "Scripts"
    / "interaction_events.json"
)

ICONS = {
    "hover_enter": "[hover ]",
    "hover_exit":  "[exit  ]",
    "grab":        "[grab  ]",
    "release":     "[release]",
    "activate":    "[activ ]",
}

seen_ids: set = set()


def read_events():
    if not EVENTS_FILE.exists():
        return []
    try:
        text = EVENTS_FILE.read_text(encoding="utf-8").strip()
        return json.loads(text) if text and text != "[]" else []
    except Exception:
        return []


def fmt(event: dict) -> str:
    etype     = event.get("type", "?")
    obj       = event.get("objectName", "?")
    icon      = ICONS.get(etype, "[?     ]")
    processed = "done" if event.get("processed") else "    "
    return f"  {icon}  {etype:<14} -> {obj:<30} {processed}"


print("\n" + "-"*60)
print("  Interaction watcher -- watching for player interactions")
print(f"  File: {EVENTS_FILE}")
print("-"*60 + "\n")

if not EVENTS_FILE.exists():
    print("  (waiting for interaction_events.json — start Unity Play mode)\n")

try:
    while True:
        events = read_events()
        for ev in events:
            eid = ev.get("id", "")
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                if ev.get("type") != "hover_exit":   # skip noisy exit events
                    print(fmt(ev))
        time.sleep(0.25)
except KeyboardInterrupt:
    print("\n  Watcher stopped.\n")
    sys.stdout.flush()
