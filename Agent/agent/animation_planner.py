"""
LLM-powered animation planner — Phase 2 background thread.

Runs as a daemon thread alongside the deterministic InteractionAnimationHandler.
When an interaction event is submitted, it calls the Groq LLM to choose the most
contextually appropriate animation from the available animator controllers,
taking into account:
  - the object being interacted with and its position
  - nearby objects in the scene
  - recent interaction history (from the log)
  - the type of interaction (grab, activate, hover_enter, …)

The deterministic handler fires immediately (zero LLM latency).
This planner fires a *second*, richer response once the LLM has reasoned.

Usage (embedded in phase2_interaction_loop):
    from agent.animation_planner import AnimationPlannerThread
    from agent.interaction_log    import InteractionLog

    log     = InteractionLog()
    planner = AnimationPlannerThread(groq_client, animators, all_placed, log)
    planner.start()
    ...
    planner.submit_event(event)
    ...
    planner.stop()
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── path bootstrap ─────────────────────────────────────────────────────────
_AGENT_DIR  = Path(__file__).parent      # Agent/agent/
_AGENT_ROOT = _AGENT_DIR.parent          # Agent/
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import main as unity                     # Agent/main.py

try:
    from .logger import get_logger
except ImportError:
    import logging
    def get_logger(name: str, **_):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s [%(name)s] %(message)s")
        return logging.getLogger(name)

from interaction_log import InteractionLog

logger = get_logger("AnimationPlanner",
                    module_log_file="api_files/animation_planner.log")

# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM = """\
You are an animation director for a VR healthcare training simulation.
A player has just interacted with an object or character in the scene.
Your job is to choose the most contextually appropriate animation response
from the available controllers, then return a single JSON command object.

Consider:
- What the object is and its role in a healthcare setting
- What the interaction type suggests about the player's intent
- What nearby objects and characters suggest about the workflow
- Recent interaction history (to avoid repetition and build narrative flow)

Respond with ONLY a single JSON object (no array, no markdown):
{
  "action": "PlayHumanAnimationFromResources",
  "controllerPathInResources": "<exact name from available_controllers, no path prefix>",
  "stateName": "<same value as controllerPathInResources>",
  "crossFadeTime": 0.25,
  "targetModel": "player" or null,
  "reasoning": "<one sentence explaining why>",
  "speechBubbleText": "<optional — only include when interacting with a CHARACTER (doctor/nurse/patient/emt/surgeon). Write what the character says in 1-2 short sentences. Omit this key entirely for non-character interactions.>",
  "speechBubbleDuration": 5.0
}

If no animation response is appropriate, return:
{"action": "none", "reasoning": "<why no action is needed>"}

Rules:
- Only use controller names that appear verbatim in available_controllers.
- Never invent controller names.
- targetModel should be "player" only when the player character itself should
  animate (e.g. picking something up). Leave null to animate the default NPC.
- For hover_enter events, prefer idle or walk animations unless strongly contextual.
- For grab/activate events, choose the most purposeful animation.
- For touch events on characters, always include speechBubbleText with something
  contextually appropriate (greeting, reassurance, clinical instruction, etc.).
"""

# Character name prefixes — objects with these prefixes get speech bubbles
_CHARACTER_PREFIXES = frozenset({
    "doctor", "nurse", "patient", "emt", "surgeon", "infectiologist",
})

# ---------------------------------------------------------------------------
# Planner thread
# ---------------------------------------------------------------------------

class AnimationPlannerThread(threading.Thread):
    """
    Background daemon thread — LLM-based animation planner for Phase 2.

    Events are submitted via submit_event() and processed asynchronously.
    Results are dispatched to unity_commands.json and logged to InteractionLog.
    """

    # Events of these types are silently dropped (low-value for LLM reasoning)
    _SKIP_TYPES = frozenset({"hover_exit"})

    def __init__(
        self,
        groq_client,
        animators: List[str],
        all_placed: List[Dict],
        interaction_log: InteractionLog,
        model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
        max_queue_size: int = 20,
    ):
        super().__init__(daemon=True, name="AnimationPlanner")
        self._client     = groq_client
        self._animators  = animators          # list of available controller stems
        self._all_placed = all_placed         # shared reference (main thread appends)
        self._log        = interaction_log
        self._model      = model
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()

    # ── public API ────────────────────────────────────────────────────────

    def submit_event(self, event: Dict[str, Any]):
        """
        Non-blocking enqueue of an interaction event.
        Drops the oldest queued event if the queue is full.
        """
        if event.get("type") in self._SKIP_TYPES:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(event)

    def stop(self):
        """Signal the thread to finish after its current task."""
        self._stop_event.set()

    # ── thread body ───────────────────────────────────────────────────────

    def run(self):
        logger.info("AnimationPlannerThread started (model=%s).", self._model)
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._plan_and_dispatch(event)
            except Exception as exc:
                logger.error("Unhandled error in planner: %s", exc, exc_info=True)
        logger.info("AnimationPlannerThread stopped.")

    # ── core logic ────────────────────────────────────────────────────────

    def _plan_and_dispatch(self, event: Dict):
        event_type = event.get("type", "")
        obj_name   = event.get("objectName", "")

        # Build context payload for the LLM
        obj_pos = self._find_object_position(obj_name)
        nearby  = self._nearby_objects(obj_pos, radius=2.5) if obj_pos else []
        recent  = self._log.recent(n=5)

        # Strip heavy fields from recent history before sending
        recent_slim = [
            {
                "event_type":          r.get("event_type"),
                "object_name":         r.get("object_name"),
                "animation_triggered": r.get("animation_triggered"),
                "llm_chosen":          r.get("llm_chosen"),
            }
            for r in recent
        ]

        user_payload = {
            "event": {
                "type":           event_type,
                "objectName":     obj_name,
                "playerPosition": event.get("playerPosition"),
                "playerLookDir":  event.get("playerLookDir"),
            },
            "nearby_objects":         nearby,
            "recent_interactions":    recent_slim,
            "available_controllers":  self._animators,
        }

        # Call LLM
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _PLANNER_SYSTEM},
                    {"role": "user",   "content": json.dumps(user_payload)},
                ],
                temperature=0.3,
                max_completion_tokens=300,
            )
            raw = (resp.choices[0].message.content or "").strip()
            logger.debug("LLM raw response: %s", raw)
            cmd = self._parse_response(raw)
        except Exception as exc:
            logger.warning("LLM call failed for event %s→%s: %s", event_type, obj_name, exc)
            return

        if not cmd or cmd.get("action") == "none":
            reasoning = (cmd or {}).get("reasoning", "no action")
            logger.info("[Planner] %s→%s : no action (%s)", event_type, obj_name, reasoning)
            self._log.append(
                event_type, obj_name,
                animation_triggered=None,
                llm_chosen=True,
                reasoning=reasoning,
                player_pos=event.get("playerPosition"),
            )
            return

        # Validate controller name is in our list
        ctrl = cmd.get("controllerPathInResources", "")
        ctrl_stem = ctrl.split("/")[-1] if "/" in ctrl else ctrl
        if ctrl_stem not in self._animators and ctrl not in self._animators:
            logger.warning("[Planner] LLM chose unknown controller %r — dropping.", ctrl)
            return

        # Dispatch to Unity
        try:
            unity.play_human_animation(
                controller_path=ctrl,
                state_name=cmd.get("stateName", ctrl_stem),
                cross_fade_time=float(cmd.get("crossFadeTime", 0.25)),
                target_model=cmd.get("targetModel") or None,
            )
            anim_name = cmd.get("stateName", ctrl_stem)
            reasoning = cmd.get("reasoning", "")
            logger.info(
                "[Planner] %s→%s : %s (reason: %s)",
                event_type, obj_name, anim_name, reasoning,
            )

            # Speech bubble — only for character interactions
            obj_prefix = obj_name.split("_")[0].lower() if obj_name else ""
            speech_text = cmd.get("speechBubbleText", "")
            if speech_text and obj_prefix in _CHARACTER_PREFIXES:
                duration = float(cmd.get("speechBubbleDuration", 5.0))
                unity.add_command(
                    "ShowSpeechBubble",
                    objectName=obj_name,
                    message=speech_text,
                    duration=duration,
                )
                logger.info("[Planner] Speech bubble → %s: %r", obj_name, speech_text)

            self._log.append(
                event_type, obj_name,
                animation_triggered=anim_name,
                llm_chosen=True,
                reasoning=reasoning,
                player_pos=event.get("playerPosition"),
            )
        except Exception as exc:
            logger.error("[Planner] Dispatch failed: %s", exc)

    # ── helpers ───────────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> Optional[Dict]:
        """Extract a JSON object from the LLM response, tolerating code fences."""
        if "```" in raw:
            start = raw.find("```") + 3
            end   = raw.rfind("```")
            inner = raw[start:end].strip()
            raw   = inner[4:].strip() if inner.startswith("json") else inner

        s = raw.find("{")
        e = raw.rfind("}") + 1
        if s == -1 or e <= s:
            return None
        try:
            return json.loads(raw[s:e])
        except json.JSONDecodeError:
            return None

    def _find_object_position(self, obj_name: str) -> Optional[Dict]:
        """Look up x/y/z of a placed object by name."""
        lo = obj_name.lower()
        for c in self._all_placed:
            name = c.get("objectName") or c.get("humanName", "")
            if name and name.lower() == lo:
                return {"x": c.get("x", 0.0), "y": c.get("y", 0.0), "z": c.get("z", 0.0)}
        return None

    def _nearby_objects(self, pos: Dict, radius: float) -> List[Dict]:
        """Return objects within *radius* metres of *pos* (XZ plane), sorted by distance."""
        _place_actions = {"PlaceObject", "PlaceHuman", "CreateObjectByName"}
        px, pz = pos.get("x", 0.0), pos.get("z", 0.0)
        result = []
        for c in self._all_placed:
            if c.get("action") not in _place_actions:
                continue
            cx, cz = c.get("x", 0.0), c.get("z", 0.0)
            dist = ((cx - px) ** 2 + (cz - pz) ** 2) ** 0.5
            if dist <= radius:
                result.append({
                    "objectName": c.get("objectName") or c.get("humanName", "?"),
                    "distance_m": round(dist, 2),
                })
        return sorted(result, key=lambda o: o["distance_m"])
