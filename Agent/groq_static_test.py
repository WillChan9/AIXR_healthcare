"""LLM pipeline that builds and runs a VR healthcare training scene in Unity.

This is the main entry point of the agent. It turns a natural-language scene
description into Unity commands, then reacts to player events at runtime.

Phase 1: scene synthesis
    1. Read ``api_files/experience.txt``: a free-text description, plus an
       optional ``[interactions]`` section (story intent) and an optional
       ``[room_config]`` section (room geometry, object bounds, keywords).
    2. If no ``[room_config]`` exists and a frontier model is enabled, an OpenAI
       model synthesizes both the room config and a layout plan
       (``scene_synthesis``). Otherwise a Groq model generates the plan.
    3. Write a fixed room shell (floor, walls, ceiling, lighting) built from
       the config, then ask a Groq model (Llama-4-Scout) for interior objects.
    4. Run a reflection loop (``ReflectionModule``) over Unity's orthographic
       screenshots. Each iteration checks the current plan step, applies
       MoveObject/DeleteObject corrections directly, and asks the LLM for more
       objects while the room is sparse.
    5. Back-fill any required characters the LLM dropped, then compile an
       ambient interaction map and a linear story chain for Phase 2.

Phase 2: interaction loop
    Poll ``interaction_events.json``, which Unity writes, and dispatch each
    player event, first through the story chain (Layer 2) and then through
    the ambient interaction map (Layer 1). Phase 2 makes no LLM calls: every
    response was compiled at the end of Phase 1.

Inputs:
    api_files/experience.txt          Scene description (see ``--path``).
    <Unity>/Assets/Resources/         Prefab (*.prefab) and animator
                                      (*.controller) names offered to the LLM.
    api_files/scene_screenshot_{xz,xy,yz}.png
                                      Orthographic views written by Unity.
    interaction_events.json           Player events (next to the commands file).

Outputs:
    <Unity>/Assets/Scripts/unity_commands.json
                                      Command queue run by the Unity bridge
                                      (see ``--out``).
    api_files/plan.json               Layout plan used by the reflection module.
    api_files/interaction_map.json    Per-object ambient reactions (Layer 1).
    api_files/story_chain.json        Scripted story beats (Layer 2).
    api_files/agent.log, reflection.json, actions.json
                                      Logs and scratch files, cleared at startup.

Environment variables (read from the environment or a ``.env`` file):
    GROQ_API_KEY            Required. Groq API key for Llama-4-Scout.
    OPENAI_API_KEY          Required when a frontier model is used
                            (``--frontier-model``, on by default).
    UNITY_SCREENSHOT_PATH   Optional base path of the screenshots
                            (default: api_files/scene_screenshot.png).

Usage:
    # Full run: Phase 1, then Phase 2 until Ctrl+C
    python groq_static_test.py

    # Phase 1 only, on a different scenario file
    python groq_static_test.py --path evaluation/scenarios/experience_S01.txt --phase1-only

    # Quick Phase 1 (3 plan steps x 1 iteration) to test Phase 2
    python groq_static_test.py --fast-phase1

    # Fine-grained Phase 1 caps
    python groq_static_test.py --phase1-max-steps 2 --phase1-max-iters 2

    # Use Groq/Llama-4-Scout for every step (no OpenAI calls)
    python groq_static_test.py --frontier-model ""

    # Restore a saved trial and go straight to Phase 2
    python groq_static_test.py --replay-trial path/to/trial_dir --max-iterations 50

    Other flags: --out, --interval, --max-iterations, --screenshot.
    Run with --help for details.
"""

import argparse
import base64
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from groq import Groq

# Make the sibling modules (agent/, interaction_*.py, story_chain.py) importable
# no matter which directory the script is launched from.
sys.path.insert(0, str(Path(__file__).parent))
from agent.Reflection_module import ReflectionModule  # noqa: E402
from agent.logger import get_logger  # noqa: E402
from interaction_animations import InteractionAnimationHandler  # noqa: E402
from interaction_log import InteractionLog  # noqa: E402
from story_chain import StoryChainHandler  # noqa: E402

logger = get_logger("Pipeline")


# ---------------------------------------------------------------------------
# experience.txt parsing
# ---------------------------------------------------------------------------

def _parse_experience_config(txt: str) -> dict:
    """Parse key=value pairs from the [room_config] section of experience.txt."""
    cfg: dict = {}
    in_section = False
    for line in txt.splitlines():
        line = line.strip()
        if line == "[room_config]":
            in_section = True
            continue
        if in_section:
            if line.startswith("["):
                break
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                cfg[key.strip()] = val.strip()
    return cfg


def _clean_description(txt: str) -> str:
    """Return only the human-readable description from experience.txt.

    Drops the [room_config] section and the [interactions] section (if it
    appears before [room_config]), so neither is sent to the LLM as scene text.
    """
    idx = txt.find("[room_config]")
    base = txt[:idx].strip() if idx != -1 else txt.strip()
    iidx = base.find("[interactions]")
    return base[:iidx].strip() if iidx != -1 else base


def _parse_interactions_section(txt: str) -> str:
    """Extract the free-text body of the [interactions] section from experience.txt.

    The block ends at the next [section] header or end-of-file. Returns an empty
    string if no [interactions] section exists.
    """
    idx = txt.find("[interactions]")
    if idx == -1:
        return ""
    rest = txt[idx + len("[interactions]"):]
    next_sec = re.search(r'\n\[[A-Za-z_]+\]', rest)
    body = rest[:next_sec.start()] if next_sec else rest
    return body.strip()


# ---------------------------------------------------------------------------
# Trial replay — restore a saved Phase 1 trial and skip straight to Phase 2
# ---------------------------------------------------------------------------

def _replay_trial(trial_dir: Path, agent_dir: Path, out_path: Path,
                  unity_settle_timeout: float = 90.0) -> bool:
    """Restore the Unity scene from a saved trial and prepare it for Phase 2.

    Steps:
      1. Copy the trial's interaction_map.json, story_chain.json, scene_state.json,
         plan.json, and experience.txt back into Agent/api_files/.
      2. Clear agent.log so the upcoming Phase 2 log is clean.
      3. Send ClearScene to Unity and wait briefly for it to settle.
      4. Re-stamp the trial's unity_commands.json (fresh IDs, executed=false)
         and write it to the live commands file. Unity replays it in order.
      5. Poll for Unity to finish executing all commands (or until timeout).

    Returns False only if the trial cannot be loaded (missing directory, or a
    missing or invalid unity_commands.json). Once the commands are written it
    returns True, even after a partial replay or a timeout, because a partial
    scene is still useful for collecting Phase 2 runtime metrics.
    """
    import main as _unity  # Unity bridge (Agent/main.py); imported lazily

    if not trial_dir.exists() or not trial_dir.is_dir():
        logger.error(f"[Replay] Trial dir not found: {trial_dir}")
        return False

    api = agent_dir / "api_files"
    api.mkdir(parents=True, exist_ok=True)

    # 1. Restore artifacts
    restored = []
    for fname in ("interaction_map.json", "story_chain.json", "scene_state.json",
                  "plan.json", "experience.txt"):
        src = trial_dir / fname
        if src.exists():
            shutil.copy2(src, api / fname)
            restored.append(fname)
    # Fallback: older trials don't have experience.txt — recover it from scenarios/
    # by inferring the scenario id from the trial dir's parent (e.g. "S01").
    if "experience.txt" not in restored:
        scenario_id = trial_dir.parent.name  # e.g. "S01"
        scenario_file = agent_dir / "evaluation" / "scenarios" / f"experience_{scenario_id}.txt"
        if scenario_file.exists():
            shutil.copy2(scenario_file, api / "experience.txt")
            restored.append("experience.txt (from scenarios/)")
            logger.info(f"[Replay] experience.txt absent in trial — fell back to "
                        f"scenarios/{scenario_file.name}")
    logger.info(f"[Replay] Restored {len(restored)} artifact(s) from {trial_dir.name}: "
                f"{restored}")

    # 2. Clear the log and scratch files so Phase 2 starts fresh
    try:
        (api / "agent.log").write_text("", encoding="utf-8")
        (api / "actions.json").write_text("[]", encoding="utf-8")
        (api / "reflection.json").write_text("[]", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[Replay] Could not clear scratch files: {exc}")

    # 3. ClearScene to wipe whatever Unity currently has
    try:
        _unity.add_command("ClearScene")
        logger.info("[Replay] Sent ClearScene; waiting 5s for Unity to settle…")
        time.sleep(5)
    except Exception as exc:
        logger.warning(f"[Replay] ClearScene failed: {exc}")

    # 4. Load + re-stamp the saved commands and write to the live commands file
    saved_cmds_path = trial_dir / "unity_commands.json"
    if not saved_cmds_path.exists():
        logger.error(f"[Replay] Missing unity_commands.json in {trial_dir}")
        return False
    try:
        saved = json.loads(saved_cmds_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        logger.error(f"[Replay] Failed to parse {saved_cmds_path}: {exc}")
        return False
    if not isinstance(saved, list):
        logger.error("[Replay] unity_commands.json is not a list")
        return False

    ts_ms = int(time.time() * 1000)
    fresh: List[Dict] = []
    for i, c in enumerate(saved):
        # Skip the ClearScene we already sent and any DebugLog probes.
        if c.get("action") in ("ClearScene", "DebugLog"):
            continue
        cmd = dict(c)
        cmd["executed"] = False
        cmd["done"] = False
        cmd["id"] = f"cmd_replay_{ts_ms}_{i}"
        fresh.append(cmd)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fresh, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[Replay] Wrote {len(fresh)} commands to {out_path.name} for replay")

    # 5. Poll until Unity has executed everything (or we hit the timeout).
    deadline = time.time() + unity_settle_timeout
    last_executed = -1
    last_progress_ts = time.time()
    while time.time() < deadline:
        time.sleep(2.0)
        try:
            cur = json.loads(out_path.read_text(encoding="utf-8-sig"))
            executed = sum(1 for c in cur if c.get("executed"))
        except Exception:
            continue
        if executed != last_executed:
            logger.info(f"[Replay] Unity executed {executed}/{len(fresh)} commands…")
            last_executed = executed
            last_progress_ts = time.time()
        if executed >= len(fresh):
            logger.info(f"[Replay] Replay complete: {executed}/{len(fresh)} commands.")
            return True
        # If no new executions for 15s and we're past 50%, accept partial replay.
        if executed >= len(fresh) * 0.5 and (time.time() - last_progress_ts) > 15:
            logger.warning(f"[Replay] No progress for 15s — accepting partial replay "
                           f"({executed}/{len(fresh)}).")
            return True
    logger.warning(f"[Replay] Timeout after {unity_settle_timeout}s — proceeding to Phase 2 "
                   f"with whatever Unity executed.")
    return True


# ---------------------------------------------------------------------------
# Frontier LLM (OpenAI) — scene synthesis & high-quality artifact generation
# ---------------------------------------------------------------------------

def _call_frontier(messages: List[Dict], model: str = "gpt-5",
                   max_tokens: int = 6000, temperature: float = 0.2) -> str:
    """Call an OpenAI chat model and return the stripped response text.

    If the error looks like the model is unavailable (not found, no access,
    unsupported), retry once with gpt-4o, so a typo in --frontier-model does
    not end the whole run. Other errors are re-raised.
    """
    from openai import OpenAI  # optional dependency; only needed for frontier runs
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _do_call(m: str) -> str:
        kwargs: Dict = dict(model=m, messages=messages)
        # GPT-5 / o1 / o3 style "reasoning" models reject custom temperature
        # and use max_completion_tokens. Other models use max_tokens.
        if any(m.startswith(p) for p in ("gpt-5", "o1", "o3")):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature
        completion = client.chat.completions.create(**kwargs)
        return (completion.choices[0].message.content or "").strip()

    try:
        return _do_call(model)
    except Exception as exc:
        msg = str(exc).lower()
        if "model" in msg and ("not found" in msg or "does not exist" in msg
                                or "not have access" in msg or "unsupported" in msg):
            logger.warning(f"[Frontier] '{model}' not available ({exc}). Falling back to gpt-4o.")
            return _do_call("gpt-4o")
        raise


def _strip_json_fences(raw: str) -> str:
    """Extract the outermost JSON object from a markdown-fenced or plain LLM response."""
    raw = raw.strip()
    if "```" in raw:
        s = raw.find("```") + 3
        e = raw.rfind("```")
        inner = raw[s:e].strip() if e > s else raw[s:].strip()
        if inner.lower().startswith("json"):
            inner = inner[4:].strip()
        raw = inner
    s = raw.find("{")
    e = raw.rfind("}") + 1
    return raw[s:e] if s != -1 and e > s else raw


def scene_synthesis(
    raw_experience: str,
    prefabs: List[str],
    model: str = "gpt-5",
) -> Tuple[Dict, Dict]:
    """Turn a narrative experience.txt into a room config and a layout plan.

    One frontier-LLM call reads the description and [interactions] text and
    returns:

      cfg : dict   — equivalent to a [room_config] section
                     (room dimensions, bounds, keyword categories, …)
      plan: dict   — equivalent to plan.json
                     (steps[] with action + objects + target_area)

    Returns (cfg, plan). Raises RuntimeError if the response is not valid
    JSON or lacks either key.
    """
    description = _clean_description(raw_experience)
    interactions = _parse_interactions_section(raw_experience)

    # Pass the full prefab list — frontier models handle 350+ names easily, and
    # alphabetic truncation drops critical prefabs (Ventilator, Walker, WheelChair,
    # Closet, Chemical_Sterilizer, etc.) that some scenarios specifically need.
    prefab_sample = prefabs

    system = """\
You are a senior VR scene designer. Read the natural-language description of
a healthcare/medical scene and produce a STRUCTURED scene contract that a
downstream pipeline will use to populate a Unity room.

Output a SINGLE JSON object with exactly these top-level keys: "cfg", "plan".
No markdown, no commentary — just the JSON.

cfg (room_config equivalent):
  scene_type             : short title from the description
  room_width             : float meters, x-axis span
  room_depth             : float meters, z-axis span
  room_height            : float meters (typically 3.0–3.5)
  x_min, x_max           : float floor x bounds (typically symmetric:
                            -room_width/2 + 0.2, +room_width/2 - 0.2)
  z_min, z_max           : float floor z bounds (0.1 to room_depth - 0.1)
  doorway_width          : float (1.0–1.6 typical)
  doorway_x              : float (where doorway is centered, usually 0)
  lighting_intensity     : float (1.4 dim, 1.6 normal, 2.0 bright surgical)
  ambient_r, ambient_g, ambient_b : floats 0.85–1.00 (cool clinical white)
  min_objects, max_objects        : integer object count bounds
  surface_keywords       : comma-separated tokens for objects you can rest
                            small props on (e.g. "Table,Cart,Trolley,Stand")
  supply_keywords        : tokens for movable supply furniture
                            (e.g. "Cart,Trolley,Rack")
  character_prefixes     : comma-separated character family prefixes that
                            appear in the scene (e.g. "EMT,Doctor,Nurse,Patient")
  small_prop_keywords    : tokens for small surface props
                            (e.g. "Folder,Syringe,Stethoscope,Aid_Kit,Bottle,Tray")
  decor_keywords         : tokens for wall decor (e.g. "Poster,Clock,Sign")

plan (mirrors plan.json):
  room_type, description, layout_goals (object {focal_point, minimum_objects,
    interior_bounds}), and steps : [{step, action, objects[], target_area, condition}].
  Each step's "objects" list must use names from `available_prefabs` verbatim
  (preserving exact capitalization and digit suffixes). Pick exact prefab
  names that fit the scene — do not invent.

Rules:
- Choose room dimensions from the description's mood: "tiny/cramped" → 3-4m,
  "small" → 4-5m, "spacious/large" → 7-9m.
- character_prefixes MUST list every character family that the description
  references (Surgeon, Nurse, Doctor, EMT, Patient, …).
- min_objects / max_objects bound how many non-structural items belong in the
  scene. A "sparse" room has 6-10, "dense" 30-40, "extreme density" 40-50.
- All numeric values must be JSON numbers (not strings).
"""

    user_payload = {
        "narrative_description": description,
        "interactions_text": interactions,
        "available_prefabs": prefab_sample,
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": json.dumps(user_payload, ensure_ascii=False)},
    ]

    raw = _call_frontier(messages, model=model, max_tokens=6000, temperature=0.2)
    json_text = _strip_json_fences(raw)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.error(f"[SceneSynthesis] LLM returned invalid JSON: {exc}\n"
                     f"--- raw ---\n{raw[:1000]}")
        raise RuntimeError(f"scene_synthesis: invalid JSON from {model}: {exc}")

    cfg = data.get("cfg") or {}
    plan = data.get("plan") or {}
    if not cfg or not plan:
        raise RuntimeError(f"scene_synthesis: missing cfg or plan in response. "
                           f"Got keys: {list(data.keys())}")

    # The Reflection module expects comma-separated strings for keyword fields,
    # so join any lists. Numeric fields are parsed with float()/int() downstream,
    # so numbers and numeric strings both work.
    for key in ("surface_keywords", "supply_keywords", "character_prefixes",
                "small_prop_keywords", "decor_keywords"):
        if isinstance(cfg.get(key), list):
            cfg[key] = ",".join(str(x) for x in cfg[key])

    logger.info(f"[SceneSynthesis] cfg: scene_type={cfg.get('scene_type')!r} "
                f"size={cfg.get('room_width')}×{cfg.get('room_depth')} "
                f"chars={cfg.get('character_prefixes')!r} "
                f"objects={cfg.get('min_objects')}–{cfg.get('max_objects')}")
    logger.info(f"[SceneSynthesis] plan: {len(plan.get('steps', []))} steps "
                f"focal={plan.get('layout_goals', {}).get('focal_point', '?')}")

    return cfg, plan


def _write_room_config_to_experience(experience_path: Path, cfg: Dict) -> None:
    """Append the synthesized cfg to experience.txt as a [room_config] section.

    The Reflection module parses experience.txt directly, so this is how it
    receives the synthesized config. Any earlier [room_config] section is
    removed first to avoid duplicates.
    """
    text = experience_path.read_text(encoding="utf-8")
    idx = text.find("[room_config]")
    if idx != -1:
        text = text[:idx].rstrip() + "\n"

    lines = ["", "[room_config]"]
    for key, val in cfg.items():
        # Skip nested objects / lists (kept for downstream code via the
        # Python-level cfg dict, not via the file-level [room_config]).
        if isinstance(val, (dict, list)):
            continue
        lines.append(f"{key} = {val}")
    new_text = text.rstrip() + "\n" + "\n".join(lines) + "\n"
    experience_path.write_text(new_text, encoding="utf-8")
    logger.info(f"[SceneSynthesis] Wrote {len(cfg)} cfg fields to [room_config] in "
                f"{experience_path.name}")


# ---------------------------------------------------------------------------
# Required-staff fallback (Phase 1 end)
# ---------------------------------------------------------------------------

def _ensure_required_staff(raw_experience: str, all_placed: List[Dict], out_path: Path,
                           cfg: dict) -> List[Dict]:
    """Back-fill required characters that the Phase 1 LLM failed to place.

    The Phase 1 LLM sometimes truncates its output and drops PlaceHuman
    commands. This parses character names and positions from experience.txt
    and appends any missing PlaceHuman commands to both unity_commands.json
    and ``all_placed`` (in place).

    A character is required when a token of the form ``Prefix_NN`` matches
    one of the configured character prefixes (e.g. Doctor, Nurse, Patient)
    on a line that also contains ``x~...`` and ``z~...`` (``y~...`` optional).
    Lines after the [interactions] header are ignored.

    Returns the list of commands that were appended (possibly empty).
    """
    prefixes_raw = cfg.get("character_prefixes", "Doctor,Nurse,Patient,Surgeon,EMT")
    prefixes = [p.strip() for p in prefixes_raw.split(",") if p.strip()]
    if not prefixes:
        return []

    name_re = re.compile(r'\b((?:' + '|'.join(prefixes) + r')_\d+)\b')
    required: Dict[str, tuple] = {}
    for line in raw_experience.splitlines():
        if line.strip().startswith("[interactions]"):
            break  # don't parse positions out of narrative interaction text
        for m in name_re.finditer(line):
            name = m.group(1)
            if name in required:
                continue
            xm = re.search(r'x\s*[~=]\s*(-?\d+\.?\d*)', line)
            ym = re.search(r'y\s*[~=]\s*(-?\d+\.?\d*)', line)
            zm = re.search(r'z\s*[~=]\s*(-?\d+\.?\d*)', line)
            if xm and zm:
                required[name] = (float(xm.group(1)),
                                  float(ym.group(1)) if ym else 0.0,
                                  float(zm.group(1)))

    if not required:
        return []

    placed_names = {(c.get("humanName") or c.get("objectName"))
                    for c in all_placed if c.get("action") == "PlaceHuman"}
    missing = [(n, p) for n, p in required.items() if n not in placed_names]

    if not missing:
        logger.info(f"[Phase 1] Required-staff check OK ({len(required)} characters all placed).")
        return []

    logger.warning(f"[Phase 1] Required-staff fallback: {len(missing)} missing — "
                   + ", ".join(n for n, _ in missing))

    new_cmds: List[Dict] = []
    for name, (x, y, z) in missing:
        new_cmds.append({
            "action": "PlaceHuman",
            "humanName": name,
            "x": x, "y": y, "z": z,
            "rotationY": 0,
        })
    _stamp_commands(new_cmds)
    all_placed.extend(new_cmds)

    existing = _load_existing_commands(out_path)
    _write_commands_safe(out_path, existing + new_cmds)
    logger.info(f"[Phase 1] Appended {len(new_cmds)} fallback PlaceHuman commands.")
    return new_cmds


# ---------------------------------------------------------------------------
# Prefab / animator discovery and name resolution
# ---------------------------------------------------------------------------

# Matches sub-part prefab suffixes (_Part_01, _Body, _Coat, _Wheel_01, ...).
# Sub-parts are pieces of a larger prefab and must never be placed on their own.
_SUBPART_RE = re.compile(
    r'_Part_\d+$'
    r'|_Body$|_Face$|_Coat$|_Hat$|_Pants$|_Sneakers$|_Hair$|_Mask$'
    r'|_Shoes$|_Slippers$|_Suit$|_Shirt$|_Beard$|_Glasses$|_Gloves$'
    r'|_Back_Wheel$|_Front_Wheel$|_Wheel_\d+$|_Door_Left$|_Door_Right$|_Shelf$',
    re.IGNORECASE,
)


def _load_prefab_names(resources_dir: Path) -> List[str]:
    """Return the sorted, unique prefab names under ``resources_dir``, excluding sub-parts."""
    if not resources_dir.exists():
        return []
    stems = {p.stem for p in resources_dir.rglob("*.prefab")}
    return sorted(s for s in stems if not _SUBPART_RE.search(s))


def _load_animator_names(resources_dir: Path) -> List[str]:
    """Return animator controller names, excluding large-movement ones (run, sprint, jump, ...).

    Large movements would carry characters out of position in a small room.
    """
    if not resources_dir.exists():
        return []
    _exclude_keywords = {"run", "sprint", "jump", "fall", "turn",
                         "backward", "backwardleft", "backwardright"}
    result = []
    for p in resources_dir.rglob("*.controller"):
        norm = (p.stem.lower()
                .replace("@", "").replace("[", "").replace("]", "")
                .replace("_", "").replace(" ", ""))
        if any(kw in norm for kw in _exclude_keywords):
            continue
        result.append(p.stem)
    return sorted(set(result))


def _normalize_name(name: str) -> str:
    """Lower-case ``name`` and drop non-alphanumerics, for fuzzy name matching."""
    return "".join(ch.lower() for ch in name if ch.isalnum())


def _build_prefab_lookup(prefabs: List[str]) -> Dict[str, str]:
    """Map each normalized prefab name to its exact (case-preserving) name."""
    lookup = {}
    for p in prefabs:
        lookup[_normalize_name(p)] = p
    return lookup


def _resolve_prefab_name(name: Optional[str], prefabs: List[str],
                         lookup: Dict[str, str]) -> Optional[str]:
    """Map an LLM-supplied name to a real prefab name, or return None.

    Resolution order: full character prefab for a character part
    (Doctor_01_Body -> Doctor_01), exact normalized match, then the closest-length
    substring match among non-sub-part prefabs.
    """
    if not name:
        return None
    # A referenced character part (e.g. Doctor_01_Body) resolves to the full
    # character prefab (Doctor_01).
    full_name = _to_full_character_name(name)
    if full_name:
        norm_full = _normalize_name(full_name)
        if norm_full in lookup:
            return lookup[norm_full]
    norm = _normalize_name(name)
    if norm in lookup:
        return lookup[norm]

    # Substring match (handles suffixes like _01). Sub-parts are excluded so a
    # name never resolves to one.
    candidates: List[Tuple[str, str]] = [
        (p, _normalize_name(p)) for p in prefabs if not _SUBPART_RE.search(p)
    ]
    matches = [p for p, n in candidates if norm in n or n in norm]
    if matches:
        # Choose closest length match
        matches.sort(key=lambda p: (abs(len(_normalize_name(p)) - len(norm)), len(p)))
        return matches[0]
    return None


def _to_full_character_name(name: str) -> Optional[str]:
    """Map a character part name to its full character name (Doctor_01_Body -> Doctor_01).

    Returns None if ``name`` is not a known character family with a part suffix.
    """
    if not name:
        return None
    parts = [
        "body", "face", "coat", "hat", "pants", "shoes", "sneakers", "hair",
        "mask", "gloves", "slippers", "suit", "shirt", "beard", "glasses"
    ]
    prefixes = [
        "doctor_", "nurse_", "emt_", "patient_", "surgeon_", "infectiologist_"
    ]
    lower = name.lower()
    if not any(lower.startswith(p) for p in prefixes):
        return None
    for part in parts:
        if lower.endswith("_" + part):
            base = name[: -(len(part) + 1)]
            return base
    return None


# ---------------------------------------------------------------------------
# Command sanitizing (repair LLM output before validation)
# ---------------------------------------------------------------------------

def _strip_unknown_fields(commands: List[Dict]) -> List[Dict]:
    """Drop fields the validator would reject from each command.

    For example, x/y/z/objectName added to PlayHumanAnimationFromResources.
    Commands whose action is not listed here pass through unchanged.
    """
    allowed_fields_by_action = {
        "PlaceObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "PlaceHuman": {"action", "humanName", "x", "y", "z", "rotationY"},
        "CreateObjectByName": {"action", "objectName", "x", "y", "z", "rotationY",
                               "scaleX", "scaleY", "scaleZ"},
        "SetTimeOfDay": {"action", "hour"},
        "SetFogEnabled": {"action", "enabled"},
        "SetFogDensity": {"action", "density"},
        "SetLightIntensity": {"action", "scale"},
        "SetLightColor": {"action", "r", "g", "b"},
        "SetAmbientColor": {"action", "r", "g", "b"},
        "SetGravity": {"action", "gravityY"},
        "SetTimeScale": {"action", "scale"},
        "SetHumanAnimationController": {"action", "controllerPath"},
        "PlayHumanAnimation": {"action", "stateName", "crossFadeTime"},
        "PlayHumanAnimationFromResources": {"action", "controllerPath", "stateName",
                                            "crossFadeTime"},
        "SetHumanAnimatorFloat": {"action", "paramName", "value"},
        "SetHumanAnimatorBool": {"action", "paramName", "boolValue"},
        "SetHumanAnimatorTrigger": {"action", "paramName"},
        "MoveObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "DeleteObject": {"action", "objectName"},
        "ClearScene": {"action"},
    }
    meta = {"id", "timestamp", "executed", "done"}
    result = []
    for cmd in commands:
        action = cmd.get("action")
        allowed = allowed_fields_by_action.get(action)
        if allowed:
            cleaned = {k: v for k, v in cmd.items() if k in allowed | meta}
            result.append(cleaned)
        else:
            result.append(cmd)
    return result


def _fix_light_commands(commands: List[Dict]) -> List[Dict]:
    """Normalize LLM field-name variants for SetLightIntensity so validation passes."""
    for cmd in commands:
        if cmd.get("action") == "SetLightIntensity":
            if "scale" not in cmd:
                # Accept common alternatives the LLM produces
                for alt in ("intensity", "value", "brightness", "light_intensity"):
                    if alt in cmd:
                        cmd["scale"] = cmd.pop(alt)
                        break
                else:
                    cmd["scale"] = 1.0  # sensible default
    return commands


def _fix_prefab_names(commands: List[Dict], prefabs: List[str]) -> List[Dict]:
    """Resolve object names in placement commands to real prefabs.

    - PlaceObject naming a character becomes PlaceHuman.
    - PlaceObject / PlaceHuman with an unresolvable name is dropped.
    - CreateObjectByName keeps Unity primitives, becomes PlaceObject if the name
      resolves to a prefab, becomes a Cube if it is structural (large scale),
      and is dropped otherwise.
    Other commands pass through unchanged.
    """
    lookup = _build_prefab_lookup(prefabs)
    _primitive_names = {"cube", "sphere", "capsule", "cylinder", "plane", "quad"}
    result = []

    for cmd in commands:
        action = cmd.get("action")

        if action == "PlaceObject":
            name = cmd.get("objectName", "")
            # Check whether it is actually a character first
            full_name = _to_full_character_name(name)
            if full_name:
                resolved = _resolve_prefab_name(full_name, prefabs, lookup)
                if resolved:
                    cmd["action"] = "PlaceHuman"
                    cmd["humanName"] = resolved
                    cmd.pop("objectName", None)
                    result.append(cmd)
                    continue
                # Character name but no prefab found — drop it
                logger.warning(f"[fix_prefab_names] Dropping PlaceObject "
                               f"(character not found): {name}")
                continue
            resolved = _resolve_prefab_name(name, prefabs, lookup)
            if resolved:
                cmd["objectName"] = resolved
                result.append(cmd)
            else:
                logger.warning(f"[fix_prefab_names] Dropping PlaceObject "
                               f"(prefab not found): {name}")
            continue

        if action == "PlaceHuman":
            name = cmd.get("humanName") or cmd.get("objectName", "")
            resolved = _resolve_prefab_name(name, prefabs, lookup)
            if resolved:
                cmd["humanName"] = resolved
                result.append(cmd)
            else:
                logger.warning(f"[fix_prefab_names] Dropping PlaceHuman (prefab not found): {name}")
            continue

        if action == "CreateObjectByName":
            name = cmd.get("objectName", "")
            if _normalize_name(name) in _primitive_names:
                result.append(cmd)
                continue
            # Non-primitive CreateObjectByName — try to resolve as prefab
            resolved = _resolve_prefab_name(name, prefabs, lookup)
            if resolved:
                cmd["action"] = "PlaceObject"
                cmd["objectName"] = resolved
                cmd.pop("scaleX", None)
                cmd.pop("scaleY", None)
                cmd.pop("scaleZ", None)
                result.append(cmd)
            else:
                # Structural elements (walls/ceiling) are primitives: keep them
                sx = abs(cmd.get("scaleX") or 1.0)
                sz = abs(cmd.get("scaleZ") or 1.0)
                if sx > 2.0 or sz > 2.0:
                    # Structural element with unrecognized name — make it a Cube
                    cmd["objectName"] = "Cube"
                    result.append(cmd)
                else:
                    logger.warning(f"[fix_prefab_names] Dropping CreateObjectByName "
                                   f"(not a prefab or primitive): {name}")
            continue

        # All other commands pass through unchanged
        result.append(cmd)

    return result


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------

def _extract_json_array(text: str) -> List[Dict]:
    """Extract a list of command dicts from a raw LLM response.

    Tries, in order: a markdown code fence, a direct parse (a list, or a dict
    with a commands/actions/result/output list), a ``<tool_call>`` wrapper,
    merging every top-level ``[...]`` array in the text, and finally the
    outermost ``{...}`` object. Raises ValueError if nothing works.
    """
    text = text.strip()

    # Try code block first
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != -1 and end != -1 and end > start:
            content = text[start + 3:end].strip()
            if content.startswith("json"):
                content = content[4:].strip()
            text = content

    def _try_parse_json(s: str):
        try:
            return json.loads(s)
        except Exception:
            return None

    # Direct JSON parse
    parsed = _try_parse_json(text)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("commands", "actions", "result", "output"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value

    # Some models wrap JSON in XML-like tool tags
    tag_match = re.search(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL)
    if tag_match:
        inner = tag_match.group(1).strip()
        parsed = _try_parse_json(inner)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("commands", "actions", "result", "output"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return value

    # Find and MERGE all top-level JSON arrays (the LLM sometimes emits several)
    merged: List[Dict] = []
    depth = 0
    arr_start = None
    for i, ch in enumerate(text):
        if ch == "[" and depth == 0:
            arr_start = i
            depth = 1
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and arr_start is not None:
                try:
                    chunk = json.loads(text[arr_start:i + 1])
                    if isinstance(chunk, list):
                        merged.extend(chunk)
                except Exception:
                    pass
                arr_start = None
    if merged:
        return merged

    # Try to locate JSON object that might contain a list
    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
        obj_text = text[obj_start:obj_end + 1]
        parsed = _try_parse_json(obj_text)
        if isinstance(parsed, dict):
            for key in ("commands", "actions", "result", "output"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return value

    raise ValueError("No JSON array found in Groq response.")


def _repair_incomplete_json(json_str: str) -> str:
    """Best-effort repair of truncated JSON.

    Removes trailing commas, then appends any missing ``}`` and ``]`` (all
    braces first, then all brackets). Brackets inside string literals are
    ignored when counting.
    """
    if not json_str:
        return json_str

    result = json_str.strip()

    # Remove trailing commas before closing brackets/braces
    result = re.sub(r',\s*([}\]])', r'\1', result)

    # Count brackets and braces properly (accounting for strings)
    open_brackets = 0
    close_brackets = 0
    open_braces = 0
    close_braces = 0
    in_string = False
    escape_next = False

    for char in result:
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue

        if char == '[':
            open_brackets += 1
        elif char == ']':
            close_brackets += 1
        elif char == '{':
            open_braces += 1
        elif char == '}':
            close_braces += 1

    # Close braces first (inner objects), then brackets (arrays)
    missing_braces = open_braces - close_braces
    missing_brackets = open_brackets - close_brackets

    if missing_braces > 0:
        result += '}' * missing_braces
    if missing_brackets > 0:
        result += ']' * missing_brackets

    return result


# ---------------------------------------------------------------------------
# Command validation and geometric post-processing
# ---------------------------------------------------------------------------

def _validate_commands(commands: List[Dict]) -> None:
    """Check commands against the Unity bridge schema.

    Checks that each action is allowed, required fields are present, no
    unknown fields appear, and numeric and boolean fields have the right
    types. Raises ValueError on the first problem.
    """
    allowed_actions = {
        "PlaceObject",
        "PlaceHuman",
        "CreateObjectByName",
        "SetTimeOfDay",
        "SetFogEnabled",
        "SetFogDensity",
        "SetLightIntensity",
        "SetLightColor",
        "SetAmbientColor",
        "SetGravity",
        "SetTimeScale",
        "SetHumanAnimationController",
        "PlayHumanAnimation",
        "PlayHumanAnimationFromResources",
        "SetHumanAnimatorFloat",
        "SetHumanAnimatorBool",
        "SetHumanAnimatorTrigger",
        "MoveObject",
        "DeleteObject",
        "HighlightObject",
        "ClearHighlight",
        "ClearScene",
    }

    allowed_meta = {"id", "timestamp", "executed", "done"}
    required_fields = {
        "PlaceObject": {"action", "objectName", "x", "y", "z"},
        "PlaceHuman": {"action", "humanName", "x", "y", "z"},
        "CreateObjectByName": {"action", "objectName", "x", "y", "z"},
        "SetTimeOfDay": {"action", "hour"},
        "SetFogEnabled": {"action", "enabled"},
        "SetFogDensity": {"action", "density"},
        "SetLightIntensity": {"action", "scale"},
        "SetLightColor": {"action", "r", "g", "b"},
        "SetAmbientColor": {"action", "r", "g", "b"},
        "SetGravity": {"action", "gravityY"},
        "SetTimeScale": {"action", "scale"},
        "SetHumanAnimationController": {"action", "controllerPath"},
        "PlayHumanAnimation": {"action", "stateName"},
        "PlayHumanAnimationFromResources": {"action", "controllerPath", "stateName"},
        "SetHumanAnimatorFloat": {"action", "paramName", "value"},
        "SetHumanAnimatorBool": {"action", "paramName", "boolValue"},
        "SetHumanAnimatorTrigger": {"action", "paramName"},
        "MoveObject": {"action", "objectName", "x", "y", "z"},
        "DeleteObject": {"action", "objectName"},
        "HighlightObject": {"action", "objectName", "r", "g", "b"},
        "ClearHighlight": {"action", "objectName"},
        "ClearScene": {"action"},
    }

    allowed_fields_by_action = {
        "PlaceObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "PlaceHuman": {"action", "humanName", "x", "y", "z", "rotationY"},
        "CreateObjectByName": {"action", "objectName", "x", "y", "z", "rotationY",
                               "scaleX", "scaleY", "scaleZ"},
        "SetTimeOfDay": {"action", "hour"},
        "SetFogEnabled": {"action", "enabled"},
        "SetFogDensity": {"action", "density"},
        "SetLightIntensity": {"action", "scale"},
        "SetLightColor": {"action", "r", "g", "b"},
        "SetAmbientColor": {"action", "r", "g", "b"},
        "SetGravity": {"action", "gravityY"},
        "SetTimeScale": {"action", "scale"},
        "SetHumanAnimationController": {"action", "controllerPath"},
        "PlayHumanAnimation": {"action", "stateName", "crossFadeTime"},
        "PlayHumanAnimationFromResources": {"action", "controllerPath", "stateName",
                                            "crossFadeTime"},
        "SetHumanAnimatorFloat": {"action", "paramName", "value"},
        "SetHumanAnimatorBool": {"action", "paramName", "boolValue"},
        "SetHumanAnimatorTrigger": {"action", "paramName"},
        "MoveObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "DeleteObject": {"action", "objectName"},
        "HighlightObject":{"action", "objectName", "r", "g", "b", "duration"},
        "ClearHighlight": {"action", "objectName"},
        "ClearScene": {"action"},
    }

    def _is_number(v):
        return isinstance(v, (int, float))

    for i, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            raise ValueError(f"Command #{i+1} is not an object.")
        action = cmd.get("action")
        if action not in allowed_actions:
            raise ValueError(f"Command #{i+1} has invalid action: {action}")

        # Check required fields
        missing = required_fields[action] - set(cmd.keys())
        if missing:
            raise ValueError(f"Command #{i+1} missing required fields: {sorted(missing)}")

        # Check for unknown fields
        allowed = allowed_fields_by_action[action] | allowed_meta
        unknown = set(cmd.keys()) - allowed
        if unknown:
            raise ValueError(f"Command #{i+1} has unknown fields: {sorted(unknown)}")

        # Basic type checks for common fields
        if "x" in cmd and not _is_number(cmd["x"]):
            raise ValueError(f"Command #{i+1} x must be number")
        if "y" in cmd and not _is_number(cmd["y"]):
            raise ValueError(f"Command #{i+1} y must be number")
        if "z" in cmd and not _is_number(cmd["z"]):
            raise ValueError(f"Command #{i+1} z must be number")
        if "rotationY" in cmd and not _is_number(cmd["rotationY"]):
            raise ValueError(f"Command #{i+1} rotationY must be number")
        if "scaleX" in cmd and not _is_number(cmd["scaleX"]):
            raise ValueError(f"Command #{i+1} scaleX must be number")
        if "scaleY" in cmd and not _is_number(cmd["scaleY"]):
            raise ValueError(f"Command #{i+1} scaleY must be number")
        if "scaleZ" in cmd and not _is_number(cmd["scaleZ"]):
            raise ValueError(f"Command #{i+1} scaleZ must be number")
        if "hour" in cmd and not _is_number(cmd["hour"]):
            raise ValueError(f"Command #{i+1} hour must be number")
        if "density" in cmd and not _is_number(cmd["density"]):
            raise ValueError(f"Command #{i+1} density must be number")
        if "scale" in cmd and not _is_number(cmd["scale"]):
            raise ValueError(f"Command #{i+1} scale must be number")
        if "r" in cmd and not _is_number(cmd["r"]):
            raise ValueError(f"Command #{i+1} r must be number")
        if "g" in cmd and not _is_number(cmd["g"]):
            raise ValueError(f"Command #{i+1} g must be number")
        if "b" in cmd and not _is_number(cmd["b"]):
            raise ValueError(f"Command #{i+1} b must be number")
        if "gravityY" in cmd and not _is_number(cmd["gravityY"]):
            raise ValueError(f"Command #{i+1} gravityY must be number")
        if "crossFadeTime" in cmd and not _is_number(cmd["crossFadeTime"]):
            raise ValueError(f"Command #{i+1} crossFadeTime must be number")
        if "enabled" in cmd and not isinstance(cmd["enabled"], bool):
            raise ValueError(f"Command #{i+1} enabled must be boolean")
        if "boolValue" in cmd and not isinstance(cmd["boolValue"], bool):
            raise ValueError(f"Command #{i+1} boolValue must be boolean")


def _clamp_positions(commands: List[Dict], cfg: dict = None) -> List[Dict]:
    """Clamp placement positions (in place) to the room bounds in ``cfg``.

    Structural elements (walls, ceiling) have a large scaleX/scaleZ and are
    skipped. y is only floored at 0.
    """
    cfg = cfg or {}
    X_MIN = float(cfg.get("x_min", -2.8))
    X_MAX = float(cfg.get("x_max", 2.8))
    Z_MIN = float(cfg.get("z_min", 0.1))
    Z_MAX = float(cfg.get("z_max", 5.9))
    Y_MIN = 0.0
    place_actions = {"PlaceObject", "CreateObjectByName", "PlaceHuman"}
    for cmd in commands:
        if cmd.get("action") in place_actions:
            # Skip walls/ceiling: structural elements have at least one large scale dimension
            sx = abs(cmd.get("scaleX") or 1.0)
            sz = abs(cmd.get("scaleZ") or 1.0)
            if sx > 2.0 or sz > 2.0:
                continue
            if "x" in cmd:
                cmd["x"] = max(X_MIN, min(X_MAX, cmd["x"]))
            if "z" in cmd:
                cmd["z"] = max(Z_MIN, min(Z_MAX, cmd["z"]))
            if "y" in cmd:
                cmd["y"] = max(Y_MIN, cmd["y"])
    return commands


def _filter_loop_structural(commands: List[Dict]) -> List[Dict]:
    """Drop CreateObjectByName commands that look like walls/ceiling (large scaleX/scaleZ).

    Used in reflection-loop iterations: the room shell is fixed after the
    initial generation and must not be rebuilt.
    """
    result = []
    for cmd in commands:
        if cmd.get("action") == "CreateObjectByName":
            sx = abs(cmd.get("scaleX") or 1.0)
            sz = abs(cmd.get("scaleZ") or 1.0)
            if sx > 2.0 or sz > 2.0:
                continue  # discard — structural element
        result.append(cmd)
    return result


def _dedup_structural(commands: List[Dict]) -> List[Dict]:
    """Remove structural CreateObjectByName commands that duplicate an earlier one.

    Two walls/ceilings count as duplicates when they are within 0.5 m on every
    axis. The first occurrence is kept.
    """
    seen: List[Dict] = []
    result = []
    for cmd in commands:
        if cmd.get("action") != "CreateObjectByName":
            result.append(cmd)
            continue
        sx = abs(cmd.get("scaleX") or 1.0)
        sz = abs(cmd.get("scaleZ") or 1.0)
        if sx <= 2.0 and sz <= 2.0:
            result.append(cmd)
            continue
        # Structural element — check for near-duplicate
        cx, cy, cz = cmd.get("x", 0), cmd.get("y", 0), cmd.get("z", 0)
        is_dup = False
        for prev in seen:
            if (abs(prev["x"] - cx) < 0.5 and
                    abs(prev["y"] - cy) < 0.5 and
                    abs(prev["z"] - cz) < 0.5):
                is_dup = True
                break
        if not is_dup:
            seen.append({"x": cx, "y": cy, "z": cz})
            result.append(cmd)
    return result


# ---------------------------------------------------------------------------
# unity_commands.json I/O
# ---------------------------------------------------------------------------

def _write_commands_safe(path: Path, commands: List[Dict], retries: int = 5,
                         delay: float = 0.5) -> None:
    """Atomically write ``commands`` as JSON to ``path``.

    Writes a temp file, then replaces the target. On PermissionError (for
    example, Unity holding a lock on the file) it retries up to ``retries``
    times.
    """
    data = json.dumps(commands, indent=2)
    for attempt in range(retries):
        try:
            tmp = path.parent / (path.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise


def _load_existing_commands(path: Path) -> List[Dict]:
    """Return the command list stored at ``path``, or [] if it is missing, empty or invalid."""
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _stamp_commands(commands: List[Dict]) -> None:
    """Add id/timestamp/executed fields to commands in place (existing values are kept)."""
    for i, cmd in enumerate(commands):
        if isinstance(cmd, dict):
            cmd.setdefault("id", f"cmd_{int(time.time() * 1000)}_{i}")
            cmd.setdefault("timestamp", time.time())
            cmd.setdefault("executed", False)


# ---------------------------------------------------------------------------
# Groq helpers: plan generation, LLM context, command generation
# ---------------------------------------------------------------------------

def _generate_plan(description: str, client, agent_dir: Path, cfg: dict = None) -> Dict:
    """Generate a structured room layout plan with Groq and save it to api_files/plan.json.

    Used when scene synthesis is disabled or failed. After 3 failed attempts,
    a generic 4-step plan built from ``cfg`` is written instead.
    Returns the plan dict.
    """
    cfg = cfg or {}
    plan_path = agent_dir / "api_files" / "plan.json"

    system = (
        "You are a room layout planner. Given a room description, generate a structured "
        "JSON plan describing the intended layout, placement goals, and verifiable conditions. "
        "Return ONLY valid JSON with this structure: "
        '{"room_type": "...", "description": "...", "layout_goals": {"interior_bounds": "...", '
        '"minimum_objects": N, "focal_point": "..."}, '
        '"steps": [{"step": N, "action": "...", "objects": [...], "target_area": "...", "condition": "..."}]}'
    )

    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Room description:\n{description}"},
                ],
                temperature=0.3,
                max_completion_tokens=1024,
            )
            content = (completion.choices[0].message.content or "").strip()
            if "```" in content:
                start = content.find("```")
                end = content.rfind("```")
                inner = content[start + 3:end].strip()
                content = inner[4:].strip() if inner.startswith("json") else inner
            start_idx = content.find("{")
            end_idx = content.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                plan = json.loads(content[start_idx:end_idx])
                plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
                logger.info(f"Plan generated and saved to {plan_path}")
                return plan
        except Exception as e:
            logger.warning(f"Plan generation attempt {attempt + 1}/3 failed: {e}")
            time.sleep(1.0)

    # Fallback: write a minimal plan derived from the description and config
    scene_type = cfg.get("scene_type", "room")
    min_obj = int(cfg.get("min_objects", 10))
    x_min = cfg.get("x_min", "-2.8")
    x_max = cfg.get("x_max", "2.8")
    z_min = cfg.get("z_min", "0.1")
    z_max = cfg.get("z_max", "5.9")
    plan = {
        "room_type": scene_type,
        "description": description,
        "layout_goals": {
            "interior_bounds": f"x in [{x_min},{x_max}], z in [{z_min},{z_max}]",
            "minimum_objects": min_obj,
            "focal_point": "primary anchor object near room center",
        },
        "steps": [
            {"step": 1, "action": "Place primary anchor object", "objects": [],
             "target_area": "room center",
             "condition": "anchor visible near room center"},
            {"step": 2, "action": "Place supporting furniture and equipment", "objects": [],
             "target_area": "near anchor",
             "condition": "supporting objects adjacent to anchor"},
            {"step": 3, "action": "Add utility and supply objects", "objects": [],
             "target_area": "walls and corners",
             "condition": "supply objects near walls"},
            {"step": 4, "action": "Add props and decor", "objects": [],
             "target_area": "surfaces and walls",
             "condition": "props on surfaces, decor on walls"},
        ],
    }
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    logger.warning(f"Fallback plan saved to {plan_path}")
    return plan


def _load_screenshots(xz_path: Path, xy_path: Path, yz_path: Path) -> Dict[str, Optional[str]]:
    """Load the three orthographic screenshots as base64 strings (None for missing files)."""
    view_images: Dict[str, Optional[str]] = {"xz": None, "xy": None, "yz": None}
    for label, path in [("xz", xz_path), ("xy", xy_path), ("yz", yz_path)]:
        if path.exists():
            try:
                with open(path, "rb") as f:
                    view_images[label] = base64.b64encode(f.read()).decode("utf-8")
            except Exception as e:
                logger.warning(f"Failed to read {label} screenshot: {e}")
    return view_images


def _build_placed_summary(all_placed: List[Dict]) -> List[Dict]:
    """Build a compact summary of placed objects (name, id, position) for LLM context."""
    _place_actions = {"PlaceObject", "CreateObjectByName", "PlaceHuman"}
    return [
        {
            "id": c.get("id", ""),
            "objectName": c.get("objectName") or c.get("humanName", "?"),
            "action": c.get("action"),
            "x": c.get("x"), "y": c.get("y"), "z": c.get("z"),
            "rotationY": c.get("rotationY", 0),
        }
        for c in all_placed if c.get("action") in _place_actions
    ]


def _call_groq_and_parse(client, messages, prefabs, is_loop: bool = False,
                         cfg: dict = None) -> Optional[List[Dict]]:
    """Call Groq (Llama-4-Scout) and return a sanitized, validated command list.

    Each of up to 3 attempts parses the response (with JSON repair as a
    fallback), then runs the sanitize pipeline: structural filtering (removed in
    loop mode, de-duplicated otherwise), unknown-field stripping, light-field
    fixes, prefab name resolution, position clamping, and validation.

    ``is_loop`` selects the reflection-loop settings (higher temperature,
    smaller token budget, no structural objects).

    Returns the command list, [] if the LLM deliberately returned an empty
    array, or None if every attempt failed.
    """
    commands = None
    last_error = None
    for attempt in range(3):
        completion = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=messages,
            temperature=0.1 if not is_loop else 0.2,
            max_completion_tokens=4096 if not is_loop else 2048,
        )

        content = completion.choices[0].message.content or ""
        label = "loop " if is_loop else ""
        logger.info(f"----- GROQ RAW RESPONSE ({label}attempt {attempt + 1}/3) -----")
        logger.info(content)
        logger.info("------------------------------------------------------")

        try:
            commands = _extract_json_array(content)
        except Exception:
            try:
                repaired = _repair_incomplete_json(content)
                commands = _extract_json_array(repaired)
            except Exception as e:
                last_error = e
                commands = None

        # Empty list is a VALID response — LLM is saying "no action needed".
        # Don't retry; return immediately.
        if commands == []:
            return []

        if commands:
            try:
                if is_loop:
                    commands = _filter_loop_structural(commands)
                else:
                    commands = _dedup_structural(commands)
                commands = _strip_unknown_fields(commands)
                commands = _fix_light_commands(commands)
                commands = _fix_prefab_names(commands, prefabs)
                commands = _clamp_positions(commands, cfg=cfg)
                _validate_commands(commands)
                return commands
            except Exception as e:
                last_error = e
                commands = None

        logger.warning(f"Parse/validate failed (attempt {attempt + 1}/3): {last_error}")
        time.sleep(1.0)

    logger.error(f"Could not generate valid commands after all attempts: {last_error}")
    return None


# ---------------------------------------------------------------------------
# Phase 1: Static environment generation + reflection correction
# ---------------------------------------------------------------------------

def phase1_generate_environment(
    client, description, prefabs, animators, plan, out_path, reflector,
    xz_path, xy_path, yz_path, interval, cfg: dict = None,
):
    """Build the static scene, then refine it with the reflection loop.

    Steps:
      1. Write a deterministic room shell (floor, walls with doorway, ceiling,
         lighting) from ``cfg``. The LLM never generates structural geometry.
      2. Ask Groq for the interior objects and append them to ``out_path``.
      3. Loop: on each screenshot update (or every ``2 * interval`` seconds),
         reflect on the current plan step, apply the reflector's
         MoveObject/DeleteObject suggestions directly, and, if the room is
         sparse and under the object cap, ask Groq for 3-5 more objects.
         Move to the next plan step when the reflector marks it complete or
         after ``phase1_max_iters`` iterations. Stop after the last step.

    Args:
        client: Groq client.
        description: Scene description (without config sections).
        prefabs / animators: Names available to the LLM.
        plan: Layout plan dict (``steps`` drives the reflection loop).
        out_path: Path of unity_commands.json.
        reflector: ReflectionModule instance.
        xz_path / xy_path / yz_path: Orthographic screenshot paths.
        interval: Base polling interval in seconds.
        cfg: Room config; also honors ``phase1_max_steps`` / ``phase1_max_iters``.

    Returns the list of all commands sent (room shell + objects + corrections),
    or only the room shell if the initial interior generation fails.
    """

    cfg = cfg or {}

    # --- Scene parameters from config (each has a default) ---
    scene_type      = cfg.get("scene_type", "room")
    room_w          = float(cfg.get("room_width",  4))
    room_d          = float(cfg.get("room_depth",  4))
    room_h          = float(cfg.get("room_height", 2.5))
    light_intensity = float(cfg.get("lighting_intensity", 1.5))
    amb_r           = float(cfg.get("ambient_r", 0.88))
    amb_g           = float(cfg.get("ambient_g", 0.90))
    amb_b           = float(cfg.get("ambient_b", 0.92))
    door_w          = float(cfg.get("doorway_width", 1.2))
    x_min           = float(cfg.get("x_min", -(room_w / 2) + 0.2))
    x_max           = float(cfg.get("x_max",  (room_w / 2) - 0.2))
    z_min           = float(cfg.get("z_min", 0.1))
    z_max           = float(cfg.get("z_max", room_d - 0.1))

    MAX_MEANINGFUL_OBJECTS = int(cfg.get("max_objects", 25))

    # Derived geometry
    half_w    = room_w / 2
    half_d    = room_d / 2
    half_h    = room_h / 2
    door_half = door_w / 2
    side_w    = (half_w - door_half)       # width of each front-wall panel
    side_cx   = -(door_half + side_w / 2)  # left panel center x

    # Optional "additions" lists from config, listed in the loop prompt
    add_furniture = (cfg.get("additions_furniture", "").split(",")
                     if cfg.get("additions_furniture") else [])
    add_equipment = (cfg.get("additions_equipment", "").split(",")
                     if cfg.get("additions_equipment") else [])
    add_props     = cfg.get("additions_props", "").split(",") if cfg.get("additions_props") else []
    add_decor     = cfg.get("additions_decor", "").split(",") if cfg.get("additions_decor") else []
    add_furniture = [s.strip() for s in add_furniture if s.strip()]
    add_equipment = [s.strip() for s in add_equipment if s.strip()]
    add_props     = [s.strip() for s in add_props     if s.strip()]
    add_decor     = [s.strip() for s in add_decor     if s.strip()]

    additions_lines = []
    if add_furniture:
        additions_lines.append(f"Extra furniture: {', '.join(add_furniture)}.")
    if add_equipment:
        additions_lines.append(f"Equipment: {', '.join(add_equipment)}.")
    if add_props:
        additions_lines.append(f"Props (on surfaces only): {', '.join(add_props)}.")
    if add_decor:
        additions_lines.append(f"Wall decor: {', '.join(add_decor)}.")
    additions_str = " ".join(additions_lines) if additions_lines else "See available_prefabs."

    # --- Local helpers (closures over the room bounds above) ---

    def _count_meaningful(placed: List[Dict]) -> int:
        """Count placed objects and characters, excluding structural Cubes."""
        count = 0
        for c in placed:
            action = c.get("action", "")
            if action == "CreateObjectByName":
                sx = abs(c.get("scaleX") or 1.0)
                sz = abs(c.get("scaleZ") or 1.0)
                if sx > 2.0 or sz > 2.0:
                    continue
            if action in ("PlaceObject", "CreateObjectByName", "PlaceHuman"):
                count += 1
        return count

    def _parse_alignment_commands(hints: str) -> List[Dict]:
        """Parse MoveObject/DeleteObject commands from the reflector's alignment_suggestions.

        Tolerates LLM formatting variants (commas, an optional "to", quotes,
        bullet prefixes). Move targets are clamped to the room bounds. A
        DeleteObject is skipped if the same object is also being moved.
        """
        cmds = []
        if not hints:
            return cmds

        # Number pattern; the regex below allows a comma or spaces between coordinates
        _num = r"[-\d.]+"
        move_pattern = re.compile(
            r"MoveObject\s+[\"']?([A-Za-z0-9_]+)[\"']?"   # object name (optional quotes)
            r"(?:\s+to)?"                                   # optional "to"
            r"\s+x\s*=\s*(" + _num + r"),?"               # x=
            r"\s+y\s*=\s*(" + _num + r"),?"               # y=
            r"\s+z\s*=\s*(" + _num + r"),?"               # z=
            r"(?:\s+rotationY\s*=\s*(" + _num + r"))?",   # rotationY= (optional)
            re.IGNORECASE,
        )
        delete_pattern = re.compile(
            r"DeleteObject\s+[\"']?([A-Za-z0-9_]+)[\"']?",
            re.IGNORECASE,
        )
        for m in move_pattern.finditer(hints):
            name = m.group(1).strip("\"'")
            cmd = {
                "action": "MoveObject", "objectName": name,
                "x": float(m.group(2)), "y": float(m.group(3)), "z": float(m.group(4)),
                "rotationY": float(m.group(5)) if m.group(5) else 0.0,
            }
            # Clamp to room bounds before sending to Unity
            cmd["x"] = max(x_min, min(x_max, cmd["x"]))
            cmd["z"] = max(z_min, min(z_max, cmd["z"]))
            cmd["y"] = max(0.0, cmd["y"])
            cmds.append(cmd)
            logger.info(f"  [parse_alignment] MoveObject {name} → x={cmd['x']} y={cmd['y']} "
                        f"z={cmd['z']} rot={cmd['rotationY']}")
        for m in delete_pattern.finditer(hints):
            name = m.group(1).strip("\"'")
            if not any(c.get("objectName") == name and c.get("action") == "MoveObject"
                       for c in cmds):
                cmds.append({"action": "DeleteObject", "objectName": name})
                logger.info(f"  [parse_alignment] DeleteObject {name}")
        if not cmds and hints:
            logger.warning(f"  [parse_alignment] No commands parsed from hints: {hints[:200]}")
        return cmds

    def _dedup_humans(new_cmds: List[Dict], existing: List[Dict]) -> List[Dict]:
        """Remove PlaceHuman commands for characters already in the scene."""
        existing_humans = set()
        for c in existing:
            if c.get("action") == "PlaceHuman":
                name = c.get("humanName") or c.get("objectName", "")
                if name:
                    existing_humans.add(name.lower())
        result = []
        for cmd in new_cmds:
            if cmd.get("action") == "PlaceHuman":
                name = (cmd.get("humanName") or cmd.get("objectName", "")).lower()
                if name in existing_humans:
                    logger.info(f"  [dedup] Skipping duplicate human: {name}")
                    continue
            result.append(cmd)
        return result

    logger.info("=" * 60)
    logger.info("  PHASE 1: Static Environment Generation")
    logger.info("=" * 60)

    # ----- Room shell (walls, floor, ceiling) built from config geometry -----
    # Always generated deterministically: the LLM is never trusted with
    # structural geometry. (Rows are column-aligned on purpose for readability.)
    room_shell: List[Dict] = [
        # Floor — thin slab, top surface at y=0, only 2 cm thick so no visible edge
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 0,       "y": -0.01,  "z": half_d, "rotationY": 0, "scaleX": room_w, "scaleY": 0.02,   "scaleZ": room_d},
        # Ceiling
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 0,       "y": room_h, "z": half_d, "rotationY": 0, "scaleX": room_w, "scaleY": 0.2,    "scaleZ": room_d},
        # Left wall
        {"action": "CreateObjectByName", "objectName": "Cube", "x": -half_w, "y": half_h, "z": half_d, "rotationY": 0, "scaleX": 0.2,    "scaleY": room_h, "scaleZ": room_d},
        # Right wall
        {"action": "CreateObjectByName", "objectName": "Cube", "x":  half_w, "y": half_h, "z": half_d, "rotationY": 0, "scaleX": 0.2,    "scaleY": room_h, "scaleZ": room_d},
        # Back wall
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 0,       "y": half_h, "z": room_d, "rotationY": 0, "scaleX": room_w, "scaleY": room_h, "scaleZ": 0.2},
        # Front wall LEFT half (doorway gap centered at x=0)
        {"action": "CreateObjectByName", "objectName": "Cube", "x": round(side_cx, 3),  "y": half_h, "z": 0, "rotationY": 0, "scaleX": round(side_w, 3), "scaleY": room_h, "scaleZ": 0.2},
        # Front wall RIGHT half
        {"action": "CreateObjectByName", "objectName": "Cube", "x": round(-side_cx, 3), "y": half_h, "z": 0, "rotationY": 0, "scaleX": round(side_w, 3), "scaleY": room_h, "scaleZ": 0.2},
        # Lighting from config
        {"action": "SetLightIntensity", "scale": light_intensity},
        {"action": "SetAmbientColor", "r": amb_r, "g": amb_g, "b": amb_b},
    ]
    _stamp_commands(room_shell)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_commands_safe(out_path, room_shell)
    logger.info(f"[Phase 1] Room shell written: {len(room_shell)} structural commands")

    # ----- LLM generates interior objects only (no walls/ceiling/floor) -----
    system = (
        f"You are a Unity scene builder placing interior objects inside a pre-built {scene_type}. "
        "The room shell (walls, floor, ceiling, doorway, lighting) is ALREADY placed — do NOT generate them. "
        "Return ONLY a valid JSON array of commands. No markdown, no code fences, no explanations. "
        f"Room bounds: interior x in [{x_min}, {x_max}], z in [{z_min}, {z_max}], y=0 for floor-standing objects. "

        "IMPORTANT: "
        "1. Do NOT output any CreateObjectByName commands — the room structure is already done. "
        "2. PlaceObject objectName: exact name from available_prefabs only. "
        "3. PlaceHuman humanName: exact name from available_prefabs only. "
        "4. Never invent names. If a name is not in available_prefabs, skip it. "
        "5. When the scene needs MULTIPLE instances of the same object type "
        "   (e.g. 4 beds, 3 chairs, 2 nurses), prefer DISTINCT prefab variants "
        "   if they exist (Bed_01, Bed_02, Bed_03, Bed_04 — NOT four Bed_01). "
        "   Same for Nurse_01..Nurse_06, Doctor_01..Doctor_06, Patient_01..Patient_06, "
        "   Chair_01..Chair_02, etc. Re-use the same name only if no further variant exists. "
        "6. Match the human count to the scene description — use as many humans as the "
        "   description explicitly calls for (e.g. 'two nurses' → 2 PlaceHuman). "
        "7. Do NOT output any PlayHumanAnimationFromResources or animation commands. "
        "   Characters should remain in their default idle pose. "
    )

    user = {
        "description": description,
        "available_prefabs": prefabs,
        "rules": {
            "bounds": f"Interior objects: x in [{x_min},{x_max}], z in [{z_min},{z_max}], y=0 for floor objects.",
            "no_invented_names": "Only use objectNames that appear in available_prefabs. Never invent names.",
            "no_structural": "Do NOT generate CreateObjectByName — walls/floor/ceiling already exist.",
            "no_animations": "Do NOT generate PlayHumanAnimationFromResources — characters use default idle.",
        },
        "schema_examples": [
            {"action": "PlaceObject", "objectName": "<exact prefab name>", "x": 0, "y": 0, "z": round(half_d, 2), "rotationY": 0},
        ],
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user)},
    ]
    commands = _call_groq_and_parse(client, messages, prefabs, is_loop=False, cfg=cfg)
    if not commands:
        logger.error("[Phase 1] Interior generation failed. Exiting.")
        return room_shell

    # Filter out structural and animation commands — the LLM should not generate these
    _blocked_actions = {"CreateObjectByName", "PlayHumanAnimationFromResources",
                        "PlayHumanAnimation", "SetHumanAnimationController"}
    commands = [c for c in commands if c.get("action") not in _blocked_actions]
    # NOTE: with an empty `existing` list this call removes nothing; it is kept as-is.
    commands = _dedup_humans(commands, [])

    _stamp_commands(commands)
    existing_file = _load_existing_commands(out_path)
    _write_commands_safe(out_path, existing_file + commands)
    logger.info(f"[Phase 1] Interior objects: wrote {len(commands)} commands")

    all_placed: List[Dict] = list(room_shell) + list(commands)

    # ----- Reflection + correction loop -----
    loop_system = (
        f"You are a scene designer adding detail to an existing {scene_type} in Unity. "
        "Return ONLY a valid JSON array. No markdown, no explanations. "

        "CORE RULES: "
        "1. PlaceObject: objectName MUST be an exact name from available_prefabs — never invent names. "
        "2. PlaceHuman: humanName must be exact. Do NOT add animation commands — characters stay in default idle. "
        "3. Do NOT use CreateObjectByName — the room shell is already placed. "
        "4. Do NOT use PlayHumanAnimationFromResources or any animation commands. "
        "5. Do NOT repeat any name already in objects_already_in_scene. "
        "6. Place 3-5 new objects per iteration, chosen to make the room MORE realistic, not just fuller. "
        "7. POSITION COLLISION: Before placing any object, check objects_already_in_scene coordinates. "
        "   If another object is within 0.6m on BOTH x AND z axes, choose a different position or skip it. "
        "8. Do NOT add humans if there are already 2+ humans in the scene. "

        f"AVAILABLE ADDITIONS (only use names from available_prefabs): {additions_str} "

        f"HARD OBJECT CAP: The room must not exceed {MAX_MEANINGFUL_OBJECTS} meaningful objects (excludes structural Cubes). "
        "If alignment_suggestions contains MoveObject or DeleteObject instructions, you MUST output those commands EXACTLY as written — "
        "copy the objectName, x, y, z, rotationY values directly. Do not reinterpret or skip them. "
        "MoveObject: {\"action\":\"MoveObject\",\"objectName\":\"<exact name>\",\"x\":...,\"y\":...,\"z\":...,\"rotationY\":...}. "
        "DeleteObject: {\"action\":\"DeleteObject\",\"objectName\":\"<exact name>\"}. "
        "For small objects: place them ON surfaces (y>0.7) near walls, not on the floor mid-room."
    )

    plan_steps = plan.get("steps", [])

    # Optional speed-up caps (set via --fast-phase1 / --phase1-max-steps / --phase1-max-iters)
    phase1_max_steps = int(cfg.get("phase1_max_steps", 0) or 0)
    if phase1_max_steps > 0 and len(plan_steps) > phase1_max_steps:
        logger.info(f"[Phase 1] FAST MODE: capping plan steps {len(plan_steps)} → "
                    f"{phase1_max_steps}")
        plan_steps = plan_steps[:phase1_max_steps]

    current_step_idx = 0
    step_iteration_count = 0
    MAX_STEP_ITERATIONS = int(cfg.get("phase1_max_iters", 0) or 4)
    last_mtime = 0.0
    last_loop_time = 0.0
    last_applied_hints = ""  # previous alignment_suggestions, to avoid re-applying them
    stale_count = 0  # consecutive iterations that returned the same suggestions

    # --- Wait for Unity to refresh the screenshots after the initial generation ---
    # Reflection needs screenshots of the CURRENT scene, not stale ones from a
    # previous run, so wait until a screenshot's mtime is newer than this point.
    SCREENSHOT_WAIT_TIMEOUT = 15  # seconds
    generation_time = time.time()
    logger.info(f"[Phase 1] Waiting up to {SCREENSHOT_WAIT_TIMEOUT}s for Unity to refresh "
                f"screenshots...")
    wait_start = time.time()
    screenshots_ready = False
    while time.time() - wait_start < SCREENSHOT_WAIT_TIMEOUT:
        fresh = [
            p for p in [xz_path, xy_path, yz_path]
            if p.exists() and p.stat().st_size > 0 and p.stat().st_mtime > generation_time
        ]
        if fresh:
            screenshots_ready = True
            logger.info(f"[Phase 1] Fresh screenshots detected ({len(fresh)}/3). "
                        f"Starting reflection loop.")
            break
        time.sleep(2.0)

    if not screenshots_ready:
        # Fallback: accept any existing screenshots even if stale
        if any(p.exists() and p.stat().st_size > 0 for p in [xz_path, xy_path, yz_path]):
            screenshots_ready = True
            logger.info("[Phase 1] Using existing screenshots (may be stale). "
                        "Starting reflection loop.")
        else:
            logger.warning("[Phase 1] No screenshots available after timeout. "
                           "Reflection will run in CORRECTION-ONLY mode (no new objects).")

    # ----- Main reflection loop -----
    while True:
        # Run an iteration when a screenshot changes or the timer expires
        latest_mtime = max(
            (p.stat().st_mtime if p.exists() else 0.0) for p in [xz_path, xy_path, yz_path]
        )
        time_since_last = time.time() - last_loop_time
        screenshot_updated = latest_mtime > last_mtime
        timer_due = time_since_last >= interval * 2

        if not screenshot_updated and not timer_due:
            time.sleep(interval)
            continue

        if screenshot_updated:
            last_mtime = latest_mtime
        last_loop_time = time.time()

        view_images = _load_screenshots(xz_path, xy_path, yz_path)
        has_screenshots = any(view_images.values())
        if not has_screenshots:
            logger.info("[Phase 1] No screenshots available — CORRECTION-ONLY mode.")

        placed_summary = _build_placed_summary(all_placed)
        alignment_hints = ""
        # Default to no additions; reflection must explicitly allow them
        suggest_additions = False

        # --- Reflection ---
        try:
            if plan_steps:
                current_plan_step = plan_steps[min(current_step_idx, len(plan_steps) - 1)]
            else:
                current_plan_step = {
                    "step": "room_layout_evaluation",
                    "action": f"Evaluate overall {scene_type} layout for realism and alignment",
                    "condition": f"Room looks like a realistic {scene_type}",
                }
            step_label = current_plan_step.get("action", f"step {current_step_idx + 1}")
            logger.info(f"[Phase 1 Reflection] Step {current_step_idx + 1}/{len(plan_steps)}: "
                        f"{step_label}")

            reflection_result = reflector.reflect_step(
                plan_step=current_plan_step,
                game_state={"placed_objects": placed_summary},
            )
            alignment_hints = reflection_result.alignment_suggestions

            # Only allow additions when screenshots exist AND the room is under the
            # object cap. Without screenshots the LLM would place objects blindly.
            meaningful_count = _count_meaningful(all_placed)
            if not has_screenshots:
                suggest_additions = False  # no screenshots, so no new objects
            elif meaningful_count >= MAX_MEANINGFUL_OBJECTS:
                suggest_additions = False  # at cap
                logger.info(f"  Object cap reached ({meaningful_count}/{MAX_MEANINGFUL_OBJECTS})"
                            f" — no additions")
            else:
                suggest_additions = reflection_result.suggest_additions

            logger.info(f"  step_complete={reflection_result.step_complete}  "
                        f"room_status={reflection_result.room_status}  "
                        f"suggest_additions={suggest_additions}  objects={meaningful_count}")
            if reflection_result.layout_comments:
                logger.info(f"  layout: {reflection_result.layout_comments}")
            if alignment_hints:
                logger.info(f"  suggestions: {alignment_hints}")

            step_iteration_count += 1
            should_advance = (
                reflection_result.step_complete == "yes"
                or step_iteration_count >= MAX_STEP_ITERATIONS
            )
            if should_advance:
                reason = ("complete" if reflection_result.step_complete == "yes"
                          else f"max iters ({MAX_STEP_ITERATIONS})")
                if plan_steps and current_step_idx < len(plan_steps) - 1:
                    new_idx = current_step_idx + 1  # always forward — never go back
                    logger.info(f"  Step {reason} — advancing {current_step_idx + 1} → "
                                f"{new_idx + 1}: {plan_steps[new_idx].get('action', '')}")
                    current_step_idx = new_idx
                    step_iteration_count = 0
                    reflector.reset_step_count()
                else:
                    logger.info(f"  Step {reason} — all {len(plan_steps)} plan steps complete. "
                                f"Stopping environment generation.")
                    break
        except Exception as e:
            logger.warning(f"[Phase 1 Reflection] skipped: {e}")
            alignment_hints = ""

        # --- Apply alignment suggestions directly (no LLM round-trip) ---
        # Skip them if they are identical to the last iteration's, so the same
        # MoveObject is not sent over and over.
        if alignment_hints and alignment_hints != last_applied_hints:
            direct_commands = _parse_alignment_commands(alignment_hints)
            if direct_commands:
                _stamp_commands(direct_commands)
                all_placed.extend(direct_commands)
                # Append (never overwrite) so Unity keeps the full command history
                existing_file = _load_existing_commands(out_path)
                _write_commands_safe(out_path, existing_file + direct_commands)
                logger.info(f"[Phase 1] Applied {len(direct_commands)} direct corrections "
                            f"(MoveObject/DeleteObject)")
            last_applied_hints = alignment_hints
            stale_count = 0
        elif alignment_hints == last_applied_hints and alignment_hints:
            stale_count += 1
            logger.info(f"[Phase 1] Same suggestions as last iteration — skipping "
                        f"(stale x{stale_count}).")
            if stale_count >= 2 and _count_meaningful(all_placed) >= MAX_MEANINGFUL_OBJECTS:
                logger.info("[Phase 1] Reflection stale + object cap reached — finishing early.")
                break

        # --- Fill via LLM (only when suggest_additions is True) ---
        if suggest_additions:
            existing_summary = [
                {"id": c.get("id", ""),
                 "objectName": c.get("objectName") or c.get("humanName", "?"),
                 "x": c.get("x"), "y": c.get("y"), "z": c.get("z")}
                for c in all_placed
                if c.get("action") in {"PlaceObject", "CreateObjectByName", "PlaceHuman"}
            ]

            loop_user = {
                "description": description,
                "objects_already_in_scene": existing_summary,
                "total_objects_placed": len(existing_summary),
                "available_prefabs": prefabs,
                "available_animators": animators,
                "rules": user["rules"],
                "schema_examples": user["schema_examples"],
                "alignment_suggestions": "",  # corrections already applied directly
                "loop_instruction": (
                    f"There are currently {len(existing_summary)} objects in the scene. "
                    f"Room can hold at most {MAX_MEANINGFUL_OBJECTS} meaningful objects. "
                    "MODE: FILL. The room is sparse and needs more objects. "
                    "Add 3-5 new objects. Do NOT repeat any objectName already in scene. "
                    "Do NOT add PlaceHuman if there are already 2+ humans."
                ),
            }

            loop_messages = [
                {"role": "system", "content": loop_system},
                {"role": "user", "content": json.dumps(loop_user)},
            ]
            if has_screenshots:
                image_content = [{
                    "type": "text",
                    "text": "Orthographic screenshots (XZ=top-down, XY=front, YZ=side):",
                }]
                for data in view_images.values():
                    if data:
                        image_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{data}"},
                        })
                loop_messages.append({"role": "user", "content": image_content})

            new_commands = _call_groq_and_parse(client, loop_messages, prefabs,
                                                is_loop=True, cfg=cfg)
            if new_commands:
                # Never place a character that is already in the scene
                new_commands = _dedup_humans(new_commands, all_placed)
                if new_commands:
                    _stamp_commands(new_commands)
                    all_placed.extend(new_commands)
                    existing_file = _load_existing_commands(out_path)
                    _write_commands_safe(out_path, existing_file + new_commands)
                    logger.info(f"[Phase 1] Appended {len(new_commands)} fill commands")
        else:
            logger.info("[Phase 1] CORRECTION-ONLY mode — skipping LLM fill call.")

        time.sleep(interval)

    return all_placed


# ---------------------------------------------------------------------------
# Interaction map generation (end of Phase 1)
# ---------------------------------------------------------------------------

def generate_interaction_map(
    client, all_placed: List[Dict], description: str, animators: List[str], out_path: Path,
    frontier_model: Optional[str] = None,
) -> Dict:
    """Generate the ambient interaction map (Layer 1) for the objects placed in Phase 1.

    The map says, per object and event type (touch, grab, ...), which
    animation and/or speech bubble to fire. It is saved to ``out_path``
    (interaction_map.json), where InteractionAnimationHandler reads it in
    Phase 2.

    frontier_model: if given, use this OpenAI model (e.g. "gpt-5.5") instead
    of Groq's Llama-4-Scout: better output, but slower and more expensive.

    Returns the map dict, or {} on failure or when no objects were placed.
    """

    placed_objects = []
    for cmd in all_placed:
        action = cmd.get("action", "")
        if action in ("PlaceObject", "PlaceHuman"):
            name = cmd.get("objectName") or cmd.get("humanName", "")
            if name:
                placed_objects.append({
                    "name": name,
                    "type": "character" if action == "PlaceHuman" else "object",
                })

    if not placed_objects:
        logger.warning("[InteractionMap] No placed objects — skipping generation.")
        return {}

    system = """\
You are an interaction design agent for a VR healthcare training simulation.
Given the list of objects placed in the scene, generate an ambient interaction map:
when the player touches/grabs/looks at any object, what should happen.

THE GOAL: every object in the scene should "respond" when the player touches it.
For non-character objects, the response is an informative speech bubble explaining
what the object is. For characters, the response is a brief animation + speech bubble
where the character itself speaks.

Schema for each entry. ALL FIELDS ARE OPTIONAL except for the response itself.
Animation-only, bubble-only, or both are all valid.

  "ObjectName_01": {
    "<event_type>": {
      "controller": "<full Resources path, e.g. AnimatorControllers/HumanM@Talking>",  // optional
      "state":      "<MUST equal the controller leaf, e.g. HumanM@Talking — keep the HumanM@/HumanF@ prefix>",
      "fade":       0.3,
      "idle_delay": 4.0,                   // seconds before auto-return to idle (0 = no auto-return)
      "target_character": "<NPC name>",     // optional — which character plays the animation; default = auto NPC
      "bubble_message":   "<short text>",   // optional — fires ShowSpeechBubble
      "bubble_on":        "<object name>",  // optional — defaults to the triggered object
      "bubble_duration":  4.0
    }
  }

Event types: hover_enter | touch | grab | release | activate

Rules:
- Cover EVERY placed object. No object should be missing from the map.
- For CHARACTERS (doctor/nurse/patient/emt/surgeon):
    - touch event: target_character = self, animation = HumanX@Talking, idle_delay = 4.0,
                   bubble_message = something the character might say (e.g. "Yes, doctor?",
                   "I'm prepping the saline", "Vitals are stable").
    - hover_enter: same shape as touch.
    - Use HumanF@ controllers for Nurse_*; HumanM@ otherwise.
- For NON-CHARACTER OBJECTS:
    - touch event: bubble_message = a 1-sentence educational description
                   (e.g. "Defibrillator — delivers an electric shock to restore heart rhythm.").
    - For movable equipment (Cart, Trolley, Wheelchair, Treadmill): also add grab + release with
      a Walk animation on a relevant character (target_character = nearest staff).
    - For monitors/screens: also add hover_enter with a relevant Doctor reading vitals
      ("BP 120/80, HR 72").
    - For small props (Forceps, Scalpel, Syringe, etc.): touch bubble describes the tool,
      grab triggers a brief Surgeon Talking ("Sharp — handle carefully").
    - For decor (Poster, Clock): touch with bubble only, no animation.
- stateName MUST be IDENTICAL to the controllerPathInResources leaf — never strip the
  HumanM@ / HumanF@ prefix.
- Only use controller names that appear verbatim in available_controllers.
- Bubble messages: short, informative, in-world. Max 80 characters.
- Omit walls/floor/ceiling/structural Cubes only.

Return ONLY a valid JSON object (no markdown, no explanation).
"""

    user_payload = {
        "scene_description": description[:1200] if frontier_model else description[:400],
        "placed_objects": placed_objects,
        "available_controllers": animators,
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": json.dumps(user_payload)},
    ]

    try:
        if frontier_model:
            logger.info(f"[InteractionMap] Using frontier model: {frontier_model}")
            raw = _call_frontier(messages, model=frontier_model, max_tokens=8000, temperature=0.2)
        else:
            resp = client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0.2,
                max_completion_tokens=6000,
            )
            raw = (resp.choices[0].message.content or "").strip()

        json_text = _strip_json_fences(raw)
        if not json_text or not json_text.startswith("{"):
            logger.warning("[InteractionMap] LLM returned no JSON object.")
            return {}

        interaction_map = json.loads(json_text)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(interaction_map, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        logger.info(
            "[InteractionMap] Generated map for %d objects → %s",
            len(interaction_map), out_path.name,
        )
        return interaction_map

    except Exception as exc:
        logger.error("[InteractionMap] Generation failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Story chain generation (end of Phase 1) — Layer 2: scripted interactions
# ---------------------------------------------------------------------------

def generate_story_chain(
    client,
    interactions_text: str,
    all_placed: List[Dict],
    description: str,
    animators: List[str],
    out_path: Path,
    frontier_model: Optional[str] = None,
) -> Dict:
    """Compile the natural-language [interactions] block into a linear story chain (Layer 2).

    Beats whose trigger object is not in the scene are dropped. The result is
    saved to ``out_path`` (story_chain.json), where StoryChainHandler reads it in
    Phase 2. If there is no [interactions] block, an empty chain is written.
    ``frontier_model`` works as in ``generate_interaction_map``.

    Output schema (story_chain.json):
        {
          "title": "<short title>",
          "synopsis": "<one-sentence summary>",
          "beats": [
            {
              "id": "B1",
              "trigger": {"event": "<hover_enter|grab|release|activate|touch>",
                          "object": "<exact objectName>"},
              "actions": [ {<AgentNetworkBridge command>}, ... ]
            },
            ...
          ]
        }

    Returns the dict (or {} on failure / no [interactions] block).
    """
    if not interactions_text:
        logger.info("[StoryChain] No [interactions] section in experience.txt — skipping.")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"title": "", "synopsis": "", "beats": []}, indent=2),
                            encoding="utf-8")
        return {}

    placed_objects = []
    for cmd in all_placed:
        action = cmd.get("action", "")
        if action in ("PlaceObject", "PlaceHuman"):
            name = cmd.get("objectName") or cmd.get("humanName", "")
            if name:
                placed_objects.append({
                    "name": name,
                    "type": "character" if action == "PlaceHuman" else "object",
                })

    if not placed_objects:
        logger.warning("[StoryChain] No placed objects — skipping.")
        return {}

    system = """\
You are a VR narrative designer. Compile the natural-language [interactions]
description into a LINEAR story chain that fires beat-by-beat as the player
satisfies each trigger.

Output a single JSON object with this exact shape (no markdown):
{
  "title": "<short title>",
  "synopsis": "<one sentence>",
  "beats": [
    {
      "id": "B1",
      "trigger": {"event": "<event_type>", "object": "<exact placed objectName>"},
      "actions": [ <command1>, <command2>, ... ]
    }
  ]
}

Trigger event types (pick one per beat):
  touch        ← STRONGLY PREFERRED. Fires when the player walks within proximity of the
                  object's collider. Reliable in both XR Device Simulator (keyboard/mouse)
                  and real VR. Use this for ALL beat triggers unless you have a specific
                  reason not to.
  grab         ← Only when the beat narratively requires the player to pick up an object
                  (e.g. picking up a tool to hand off). Note: grab requires functioning XR
                  controller grip input which may be unreliable in simulators.
  release      ← Only as the immediate follow-up to a grab beat (player puts the object
                  back down). Same simulator-reliability caveat as grab.
  hover_enter  ← AVOID. Requires precise XR controller ray aim which is unreliable in
                  the keyboard/mouse simulator. Use touch instead.
  activate     ← Rarely used; requires XR trigger button.

DEFAULT POLICY: use "touch" for every beat. Only deviate to grab/release when the user's
[interactions] description explicitly says the player must pick up / hand off an object.
Never use hover_enter unless the user's description says "look at" or "gaze at".

Available action commands (pick zero or more per beat):
  {"action":"PlayHumanAnimationFromResources","targetModel":"<characterName>",
   "controllerPathInResources":"AnimatorControllers/<name>","stateName":"<name>","crossFadeTime":0.3}
  {"action":"WalkCharacterTo","objectName":"<characterName>","x":..,"y":..,"z":..,"rotationY":..,
   "duration":2.5,"controllerPathInResources":"AnimatorControllers/<gender>@Walk01_Forward",
   "idleControllerPath":"AnimatorControllers/<gender>@Idles"}
  {"action":"MoveCharacter","objectName":"<characterName>","x":..,"y":..,"z":..,"rotationY":..}
  {"action":"ShowSpeechBubble","objectName":"<name>","message":"<text>","duration":4.0}
  {"action":"HighlightObject","objectName":"<name>","r":1.0,"g":0.9,"b":0.2,"duration":5.0}
  {"action":"ClearHighlight","objectName":"<name>"}
  {"action":"SetLightIntensity","intensity":2.5}
  {"action":"SetLightColor","r":1.0,"g":1.0,"b":1.0}
  {"action":"SetAmbientColor","r":..,"g":..,"b":..}
  {"action":"SetFogEnabled","enabled":true}
  {"action":"SetFogDensity","density":0.05}

Rules:
- Every trigger.object MUST be a name in placed_objects (verbatim, including the _NN suffix).
- Every targetModel / objectName for character commands MUST be a placed character (type=character).
- Use only controller names that appear in available_controllers.
- stateName MUST be IDENTICAL to the controllerPathInResources leaf — e.g. controller
  "AnimatorControllers/HumanM@Talking" requires stateName "HumanM@Talking" (with the
  "HumanM@" / "HumanF@" prefix preserved). Never strip the prefix.
- Match controller gender to the character: HumanF@... for Nurse_*, Female_*; HumanM@... for everyone else.
- For character motion, PREFER WalkCharacterTo over MoveCharacter — it auto-plays a walk
  animation and crossfades to idle on arrival, so the character doesn't teleport.
  Use MoveCharacter only when you need an instant snap (rare).
- WalkCharacterTo target coordinates MUST be inside the room: x in [-3.5, 3.5], z in [0.5, 7.5],
  y normally 0 (floor) — never put a character at z<=0 (outside the room).
- 3–7 beats total. Linear chain — no branching. Beat N+1 fires after Beat N's trigger has been satisfied.
- Each beat's actions should reinforce the narrative moment (talk, walk, highlight, light change).
- The beat that ends the story can include a finale (light change, all characters back to idle).
- Use ShowSpeechBubble sparingly (~2–4 beats) for diegetic dialogue.
- Pick a HighlightObject in beat N to telegraph what the player should interact with in beat N+1.
- HighlightObject duration for chain guidance MUST be 10.0 (player takes 10-15s to
  cross the room — 5s expires too fast). Beat N+1 (whose trigger is the highlighted
  object) MUST include a matching ClearHighlight so the glow ends when the player
  arrives. Never use 1-5s for chain guidance highlights.
"""

    user_payload = {
        "scene_description": description[:1200] if frontier_model else description[:400],
        "story_intent": interactions_text,
        "placed_objects": placed_objects,
        "available_controllers": animators,
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": json.dumps(user_payload)},
    ]

    try:
        if frontier_model:
            logger.info(f"[StoryChain] Using frontier model: {frontier_model}")
            raw = _call_frontier(messages, model=frontier_model, max_tokens=4000, temperature=0.3)
        else:
            resp = client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0.3,
                max_completion_tokens=2500,
            )
            raw = (resp.choices[0].message.content or "").strip()

        json_text = _strip_json_fences(raw)
        if not json_text or not json_text.startswith("{"):
            logger.warning("[StoryChain] LLM returned no JSON object.")
            return {}

        chain = json.loads(json_text)

        # Sanity-check beats against placed objects
        placed_names = {p["name"] for p in placed_objects}
        valid_beats = []
        for beat in chain.get("beats", []):
            trig_obj = (beat.get("trigger") or {}).get("object")
            if trig_obj and trig_obj not in placed_names:
                logger.warning(
                    "[StoryChain] Dropping beat %s — trigger object '%s' not in scene.",
                    beat.get("id", "?"), trig_obj,
                )
                continue
            valid_beats.append(beat)
        chain["beats"] = valid_beats

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(chain, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(
            "[StoryChain] Generated %d beats → %s (title='%s')",
            len(valid_beats), out_path.name, chain.get("title", ""),
        )
        return chain

    except Exception as exc:
        logger.error("[StoryChain] Generation failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Phase 2: User interaction loop
# ---------------------------------------------------------------------------

def _load_interaction_events(events_path: Path, min_timestamp: float = 0.0) -> List[Dict]:
    """Read unprocessed interaction events from the JSON file written by Unity.

    min_timestamp: Unix-seconds floor — events with `timestamp` strictly less than
    this are dropped. Set to Phase 2 start time to discard stale events from
    previous sessions or events fired during Phase 1.
    """
    if not events_path.exists():
        return []
    try:
        raw = events_path.read_text(encoding="utf-8").strip()
        if not raw or raw == "[]":
            return []
        events = json.loads(raw)
        return [
            e for e in events
            if not e.get("processed", False)
            and float(e.get("timestamp", 0)) >= min_timestamp
        ]
    except Exception as exc:
        logger.warning(f"[Phase 2] Failed to read interaction events: {exc}")
        return []


def _mark_events_processed(events_path: Path, event_ids: List[str]) -> None:
    """Set ``processed: true`` on the given event ids so they are not dispatched again."""
    if not events_path.exists() or not event_ids:
        return
    try:
        raw = events_path.read_text(encoding="utf-8").strip()
        if not raw or raw == "[]":
            return
        events = json.loads(raw)
        id_set = set(event_ids)
        for e in events:
            if e.get("id") in id_set:
                e["processed"] = True
        events_path.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[Phase 2] Failed to mark events processed: {exc}")


def phase2_interaction_loop(
    client, description, prefabs, animators, all_placed, out_path,
    reflector, xz_path, xy_path, yz_path, interval, max_iterations, cfg: dict = None,
):
    """Deterministic Phase 2: dispatch player events until stopped.

    Sends ``SetPhase 2`` to Unity, then polls interaction_events.json (next to
    ``out_path``) every 0.2 s. Each new event goes first to the story chain
    (Layer 2). Events the chain does not consume go to the ambient interaction
    map (Layer 1). No LLM runs at this stage: every response was compiled at
    the end of Phase 1 into story_chain.json and interaction_map.json.

    Only ``out_path`` and ``max_iterations`` (number of polls that had events;
    0 = run forever) are used. The other parameters are kept for signature
    compatibility with callers.
    """
    # Unused in deterministic Phase 2
    del client, description, prefabs, animators, all_placed, reflector
    del xz_path, xy_path, yz_path, interval, cfg

    logger.info("=" * 60)
    logger.info("  PHASE 2: User Interaction (deterministic)")
    logger.info("  Environment is ready. Monitoring for user activity...")
    logger.info("=" * 60)

    # Enable Phase 2 interaction system in Unity (PlayerTouchHighlighter + Player tag)
    import main as _unity  # Unity bridge (Agent/main.py); imported lazily
    _unity.add_command("SetPhase", phase=2)
    logger.info("[Phase 2] SetPhase 2 sent to Unity.")

    # Derive interaction_events.json path (next to unity_commands.json)
    events_path = out_path.parent / "interaction_events.json"

    # Wipe any events accumulated before Phase 2 started, then ALSO record a
    # timestamp floor — Unity's InteractionEventReporter keeps an in-memory
    # event list and will rewrite stale events to disk the next time anything
    # fires, so file-clearing alone isn't enough. Filter by timestamp instead.
    phase2_start_ts = time.time()
    try:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text("[]", encoding="utf-8")
        logger.info(f"[Phase 2] Cleared stale events at: {events_path} "
                    f"(min_timestamp={phase2_start_ts:.0f})")
    except Exception as exc:
        logger.warning(f"[Phase 2] Could not clear interaction_events.json: {exc}")

    iterations = 0
    logger.info(f"[Phase 2] Watching for events at: {events_path}")

    # Persistent interaction log — tracks every event and the animation chosen.
    # Accumulates across runs (capped at 200 entries).
    interaction_log = InteractionLog()

    # Layer 1 — ambient: per-object reactions from interaction_map.json.
    anim_handler = InteractionAnimationHandler(events_file=events_path,
                                               interaction_log=interaction_log)

    # Layer 2 — scripted story chain. Events it consumes are skipped by Layer 1
    # so a beat's targeted character animation is not overwritten by the
    # ambient Talking response on the same event.
    story_chain_path = out_path.parent.parent / "story_chain.json"
    if not story_chain_path.exists():
        story_chain_path = Path(__file__).parent / "api_files" / "story_chain.json"
    story_handler = StoryChainHandler(chain_file=story_chain_path)

    while True:
        if max_iterations and iterations >= max_iterations:
            logger.info("[Phase 2] Max iterations reached. Stopping.")
            break

        pending_events = _load_interaction_events(events_path, min_timestamp=phase2_start_ts)

        if pending_events:
            # Layer 2 first — scripted chain. Events it consumes are excluded
            # from Layer 1 to prevent the ambient response from overwriting
            # the beat's targeted character animation.
            consumed_ids = set(story_handler.process_events_list(pending_events))
            ambient_events = [e for e in pending_events if e.get("id") not in consumed_ids]

            n_anims = anim_handler.process_events_list(ambient_events)
            if n_anims:
                logger.info(f"[Phase 2] Ambient handler fired {n_anims} animation(s)")

            # Mark events as processed so they aren't re-dispatched.
            processed_ids = [e.get("id") for e in pending_events if e.get("id")]
            _mark_events_processed(events_path, processed_ids)
            iterations += 1

        # Tick idle returns even when no new events.
        anim_handler._fire_pending_idles()

        # Poll fast so touch events are picked up promptly.
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """Parse CLI arguments and run replay mode, or Phase 1 followed by Phase 2."""
    parser = argparse.ArgumentParser(
        description="Two-phase scene generator: build environment, then interact")
    parser.add_argument("--path", type=str, default="api_files/experience.txt",
                        help="Path to experience file")
    parser.add_argument("--out", type=str, default=None,
                        help="Output path for unity_commands.json")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Loop interval in seconds")
    parser.add_argument("--max-iterations", type=int, default=0,
                        help="Max Phase 2 iterations (0 = infinite)")
    parser.add_argument("--screenshot", type=str, default=None,
                        help="Screenshot path (overrides UNITY_SCREENSHOT_PATH)")
    parser.add_argument("--phase1-only", action="store_true",
                        help="Exit after Phase 1 (environment generation only)")
    parser.add_argument("--fast-phase1", action="store_true",
                        help="Speed up Phase 1 for testing Phase 2: "
                             "cap to first 3 plan steps × 1 iter each")
    parser.add_argument("--phase1-max-steps", type=int, default=0,
                        help="Override max number of plan steps to run in Phase 1 (0 = no cap)")
    parser.add_argument("--phase1-max-iters", type=int, default=0,
                        help="Override max reflection iterations per plan step (0 = default 4)")
    parser.add_argument("--frontier-model", type=str, default="gpt-5.5",
                        help="OpenAI frontier model used for scene_synthesis + interaction_map "
                             "+ story_chain (default 'gpt-5.5'; alternatives: 'gpt-5.5-pro', "
                             "'gpt-5.2', 'gpt-5.4-mini' for cheaper, 'gpt-4o' for legacy). "
                             "Set to '' to disable frontier LLM and use Llama-4-Scout for "
                             "everything.")
    parser.add_argument("--replay-trial", type=str, default=None,
                        help="Restore a saved trial directory (Phase 1 already done) and jump "
                             "straight to Phase 2. The directory should contain "
                             "unity_commands.json, interaction_map.json, story_chain.json, "
                             "and experience.txt — "
                             "as produced by run_experiment.py. Skips Phase 1 entirely.")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in environment variables.")

    agent_dir = Path(__file__).parent
    project_root = agent_dir.parent
    unity_project = project_root / "firefighter project"
    resources_dir = unity_project / "Assets" / "Resources"
    default_out = unity_project / "Assets" / "Scripts" / "unity_commands.json"

    prompt_path = Path(args.path)
    if not prompt_path.is_absolute():
        prompt_path = agent_dir / args.path
    out_path = Path(args.out) if args.out else default_out
    screenshot_env = os.getenv("UNITY_SCREENSHOT_PATH", "api_files/scene_screenshot.png")
    screenshot_path = Path(args.screenshot or screenshot_env)
    if not screenshot_path.is_absolute():
        screenshot_path = agent_dir / screenshot_path

    # ===== REPLAY MODE: skip Phase 1 entirely, jump straight to Phase 2 =====
    if args.replay_trial:
        trial_dir = Path(args.replay_trial)
        if not trial_dir.is_absolute():
            trial_dir = (Path.cwd() / trial_dir).resolve()
        logger.info("=" * 60)
        logger.info(f"  REPLAY MODE: restoring trial → {trial_dir}")
        logger.info("=" * 60)
        if not _replay_trial(trial_dir, agent_dir, out_path):
            logger.error("[Replay] Failed to restore trial. Aborting.")
            return

        # Reload experience.txt from the restored copy and parse
        replayed_exp_path = agent_dir / "api_files" / "experience.txt"
        raw_experience = replayed_exp_path.read_text(encoding="utf-8")
        cfg = _parse_experience_config(raw_experience)
        description = _clean_description(raw_experience)

        # Screenshot paths (unused by Phase 2; passed for signature compatibility)
        screenshot_base = screenshot_path.with_suffix("")
        xz_path = screenshot_base.parent / (screenshot_base.name + "_xz.png")
        xy_path = screenshot_base.parent / (screenshot_base.name + "_xy.png")
        yz_path = screenshot_base.parent / (screenshot_base.name + "_yz.png")

        try:
            phase2_interaction_loop(
                None, description, [], [], [], out_path,
                None, xz_path, xy_path, yz_path, args.interval, args.max_iterations, cfg=cfg,
            )
        except KeyboardInterrupt:
            logger.info("[Replay] Phase 2 interrupted by user (Ctrl+C).")
        finally:
            # Copy fresh runtime artifacts back to the trial dir so the next
            # evaluate_phase2.py run picks up the new agent.log + scene_state.
            for fname in ("agent.log", "scene_state.json"):
                src = agent_dir / "api_files" / fname
                if src.exists():
                    try:
                        shutil.copy2(src, trial_dir / fname)
                        logger.info(f"[Replay] Updated {fname} in trial dir.")
                    except Exception as exc:
                        logger.warning(f"[Replay] Could not copy {fname}: {exc}")
            # unity_commands.json is the live one (out_path) — copy it too.
            if out_path.exists():
                try:
                    shutil.copy2(out_path, trial_dir / "unity_commands.json")
                except Exception:
                    pass

            # Re-run evaluate_phase2 to refresh runtime metrics in the report.
            try:
                ep2_path = agent_dir / "evaluation" / "evaluate_phase2.py"
                spec = importlib.util.spec_from_file_location("evaluate_phase2", ep2_path)
                ep2_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(ep2_mod)
                exp_for_eval = trial_dir / "experience.txt"
                if not exp_for_eval.exists():
                    exp_for_eval = replayed_exp_path
                report = ep2_mod.evaluate(trial_dir, exp_for_eval)
                report_path = trial_dir / "phase2_eval_report.json"
                report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
                rt = report.get("runtime", {})
                logger.info(f"[Replay] Re-evaluated Phase 2 — "
                            f"composite={report.get('composite_score', 0)}, "
                            f"chain_progression={rt.get('chain_progression', 0):.2f} "
                            f"({rt.get('chain_beats_fired', 0)} beats fired)")
            except Exception as exc:
                logger.warning(f"[Replay] Re-evaluation failed: {exc}")
        return

    if not prompt_path.exists():
        raise FileNotFoundError(f"Experience file not found: {prompt_path}")

    raw_experience = prompt_path.read_text(encoding="utf-8").strip()
    if not raw_experience:
        raise ValueError("Experience file is empty.")

    # Parse any existing [room_config] section. If experience.txt is
    # narrative-only (no [room_config]) and a frontier model is configured,
    # scene_synthesis produces one below and appends it to the file.
    cfg = _parse_experience_config(raw_experience)
    description = _clean_description(raw_experience)
    use_frontier = bool(args.frontier_model)
    frontier_model = args.frontier_model if use_frontier else None

    # Phase 1 speed overrides (CLI flags take precedence over config)
    if args.fast_phase1:
        cfg["phase1_max_steps"] = args.phase1_max_steps or 3
        cfg["phase1_max_iters"] = args.phase1_max_iters or 1
    else:
        if args.phase1_max_steps:
            cfg["phase1_max_steps"] = args.phase1_max_steps
        if args.phase1_max_iters:
            cfg["phase1_max_iters"] = args.phase1_max_iters

    prefabs = _load_prefab_names(resources_dir)
    animators = _load_animator_names(resources_dir)

    # Clear all runtime files at startup
    for jf in [out_path,
               agent_dir / "api_files" / "reflection.json",
               agent_dir / "api_files" / "actions.json"]:
        try:
            jf.write_text("[]", encoding="utf-8")
            logger.info(f"[Init] Cleared {jf.name}")
        except Exception as e:
            logger.warning(f"[Init] Could not clear {jf.name}: {e}")

    for lf in [agent_dir / "api_files" / "agent.log",
               agent_dir / "api_files" / "reflection_log.log"]:
        try:
            lf.write_text("", encoding="utf-8")
            logger.info(f"[Init] Cleared log {lf.name}")
        except Exception as e:
            logger.warning(f"[Init] Could not clear log {lf.name}: {e}")

    # Do NOT delete screenshots at startup: Unity's ScreenshotLoopSender
    # overwrites them, and deleting them causes a long wait while they are re-created.
    screenshot_base = screenshot_path.with_suffix("")

    client = Groq(api_key=api_key)

    # ===== SCENE SYNTHESIS (frontier LLM) =====
    # If --frontier-model is set, run a single high-quality LLM call that
    # produces both the [room_config] cfg and plan.json from the narrative.
    # The cfg is appended back to api_files/experience.txt so Reflection
    # (which reads experience.txt directly) picks it up unchanged.
    plan: Optional[Dict] = None
    if use_frontier and not cfg:
        logger.info(f"[Frontier] Running scene_synthesis with {frontier_model} …")
        try:
            cfg, plan = scene_synthesis(raw_experience, prefabs, model=frontier_model)
            _write_room_config_to_experience(prompt_path, cfg)
            # Re-read so raw_experience reflects the appended [room_config].
            raw_experience = prompt_path.read_text(encoding="utf-8").strip()
            # Persist the synthesized plan to api_files/plan.json so the rest
            # of the pipeline (and the Reflection module) sees it.
            (agent_dir / "api_files" / "plan.json").write_text(
                json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info(f"[Frontier] Synthesis complete. "
                        f"Plan has {len(plan.get('steps', []))} steps.")
        except Exception as exc:
            logger.error(f"[Frontier] scene_synthesis failed: {exc} — "
                         f"falling back to Groq plan generation.")
            plan = None

    # Re-apply CLI overrides if synthesis just replaced cfg from scratch
    # (the earlier override block ran on the empty pre-synthesis cfg).
    if plan is not None:
        if args.fast_phase1:
            cfg["phase1_max_steps"] = args.phase1_max_steps or 3
            cfg["phase1_max_iters"] = args.phase1_max_iters or 1
        else:
            if args.phase1_max_steps:
                cfg["phase1_max_steps"] = args.phase1_max_steps
            if args.phase1_max_iters:
                cfg["phase1_max_iters"] = args.phase1_max_iters

    # Fallback: synthesis off OR synthesis failed — use Groq's plan generator.
    if plan is None:
        logger.info("Generating room layout plan...")
        plan = _generate_plan(description, client, agent_dir, cfg=cfg)

    # Screenshot paths
    xz_path = screenshot_base.parent / (screenshot_base.name + "_xz.png")
    xy_path = screenshot_base.parent / (screenshot_base.name + "_xy.png")
    yz_path = screenshot_base.parent / (screenshot_base.name + "_yz.png")

    reflector = ReflectionModule()

    # ===== PHASE 1: Static Environment Generation =====
    all_placed = phase1_generate_environment(
        client, description, prefabs, animators, plan, out_path, reflector,
        xz_path, xy_path, yz_path, args.interval, cfg=cfg,
    )

    if not all_placed:
        logger.error("Phase 1 produced no commands. Exiting.")
        return

    # ===== REQUIRED-STAFF FALLBACK =====
    # Phase 1 LLM occasionally truncates output and drops PlaceHuman commands.
    # Parse character names+positions from experience.txt and back-fill anything
    # missing so downstream interaction_map / story_chain references resolve.
    _ensure_required_staff(raw_experience, all_placed, out_path, cfg)

    # ===== INTERACTION MAP: Agent-generated per-object animation mapping =====
    interaction_map_path = agent_dir / "api_files" / "interaction_map.json"
    generate_interaction_map(client, all_placed, description, animators, interaction_map_path,
                             frontier_model=frontier_model)

    # ===== STORY CHAIN: Layer 2 — scripted, conditional interaction sequence =====
    story_chain_path = agent_dir / "api_files" / "story_chain.json"
    interactions_text = _parse_interactions_section(raw_experience)
    generate_story_chain(client, interactions_text, all_placed, description, animators,
                         story_chain_path, frontier_model=frontier_model)

    if args.phase1_only:
        logger.info("--phase1-only flag set. Skipping Phase 2.")
        logger.info(f"Total objects placed: {len(_build_placed_summary(all_placed))}")
        return

    # ===== PHASE 2: User Interaction =====
    phase2_interaction_loop(
        client, description, prefabs, animators, all_placed, out_path,
        reflector, xz_path, xy_path, yz_path, args.interval, args.max_iterations, cfg=cfg,
    )


if __name__ == "__main__":
    main()
