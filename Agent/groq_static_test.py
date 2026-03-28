import os
import sys
import re
import json
import time
import argparse
import base64
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from dotenv import load_dotenv
from groq import Groq

# Allow importing from the agent package
sys.path.insert(0, str(Path(__file__).parent))
from agent.Reflection_module import ReflectionModule
from agent.logger import get_logger

logger = get_logger("Pipeline")


# Regex matching sub-part suffixes: _Part_01, _Body, _Coat, _Wheel_01, etc.
_SUBPART_RE = re.compile(
    r'_Part_\d+$'
    r'|_Body$|_Face$|_Coat$|_Hat$|_Pants$|_Sneakers$|_Hair$|_Mask$'
    r'|_Shoes$|_Slippers$|_Suit$|_Shirt$|_Beard$|_Glasses$|_Gloves$'
    r'|_Back_Wheel$|_Front_Wheel$|_Wheel_\d+$|_Door_Left$|_Door_Right$|_Shelf$',
    re.IGNORECASE,
)


def _load_prefab_names(resources_dir: Path) -> List[str]:
    if not resources_dir.exists():
        return []
    stems = {p.stem for p in resources_dir.rglob("*.prefab")}
    return sorted(s for s in stems if not _SUBPART_RE.search(s))


def _load_animator_names(resources_dir: Path) -> List[str]:
    """Return only the animation controllers that make sense in a clinical/hospital context."""
    if not resources_dir.exists():
        return []
    # Exclude movement animations irrelevant in a clinical room (running, sprinting, jumping, falling, turning)
    _exclude_keywords = {"run", "sprint", "jump", "fall", "turn", "backward", "backwardleft", "backwardright"}
    result = []
    for p in resources_dir.rglob("*.controller"):
        norm = p.stem.lower().replace("@", "").replace("[", "").replace("]", "").replace("_", "").replace(" ", "")
        if any(kw in norm for kw in _exclude_keywords):
            continue
        result.append(p.stem)
    return sorted(set(result))


def _normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())


def _build_prefab_lookup(prefabs: List[str]) -> Dict[str, str]:
    lookup = {}
    for p in prefabs:
        lookup[_normalize_name(p)] = p
    return lookup


def _resolve_prefab_name(name: Optional[str], prefabs: List[str], lookup: Dict[str, str]) -> Optional[str]:
    if not name:
        return None
    # If a character part is referenced (e.g., Doctor_01_Body), prefer the full character prefab (Doctor_01).
    full_name = _to_full_character_name(name)
    if full_name:
        norm_full = _normalize_name(full_name)
        if norm_full in lookup:
            return lookup[norm_full]
    norm = _normalize_name(name)
    if norm in lookup:
        return lookup[norm]

    # Substring match (handles suffixes like _01)
    # Exclude sub-parts from substring matching — they should never be resolved to.
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
    if not name:
        return None
    # Map character parts to full character prefab: Doctor_01_Body -> Doctor_01
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



def _strip_unknown_fields(commands: List[Dict]) -> List[Dict]:
    """Remove any fields the LLM added to commands that the validator will reject.
    E.g. x/y/z/objectName appended to PlayHumanAnimationFromResources."""
    allowed_fields_by_action = {
        "PlaceObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "PlaceHuman": {"action", "humanName", "x", "y", "z", "rotationY"},
        "CreateObjectByName": {"action", "objectName", "x", "y", "z", "rotationY", "scaleX", "scaleY", "scaleZ"},
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
        "PlayHumanAnimationFromResources": {"action", "controllerPath", "stateName", "crossFadeTime"},
        "SetHumanAnimatorFloat": {"action", "paramName", "value"},
        "SetHumanAnimatorBool": {"action", "paramName", "boolValue"},
        "SetHumanAnimatorTrigger": {"action", "paramName"},
        "MoveObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "DeleteObject": {"action", "objectName"},
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
    """Normalise LLM field-name variations for SetLightIntensity so validation passes."""
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
    lookup = _build_prefab_lookup(prefabs)
    _primitive_names = {"cube", "sphere", "capsule", "cylinder", "plane", "quad"}
    result = []

    for cmd in commands:
        action = cmd.get("action")

        if action == "PlaceObject":
            name = cmd.get("objectName", "")
            # Check if it's actually a character first
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
                logger.warning(f"[fix_prefab_names] Dropping PlaceObject (character not found): {name}")
                continue
            resolved = _resolve_prefab_name(name, prefabs, lookup)
            if resolved:
                cmd["objectName"] = resolved
                result.append(cmd)
            else:
                logger.warning(f"[fix_prefab_names] Dropping PlaceObject (prefab not found): {name}")
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
                # Check if structural (walls/ceiling) — keep as-is since those ARE primitives
                sx = abs(cmd.get("scaleX") or 1.0)
                sz = abs(cmd.get("scaleZ") or 1.0)
                if sx > 2.0 or sz > 2.0:
                    # Structural element with unrecognized name — make it a Cube
                    cmd["objectName"] = "Cube"
                    result.append(cmd)
                else:
                    logger.warning(f"[fix_prefab_names] Dropping CreateObjectByName (not a prefab or primitive): {name}")
            continue

        # All other commands pass through unchanged
        result.append(cmd)

    return result


def _extract_json_array(text: str) -> List[Dict]:
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

    # Try to find and MERGE all JSON arrays in the text (LLM sometimes outputs multiple arrays)
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
    """Try to repair incomplete JSON by adding missing closing brackets and braces."""
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

    # Add missing closing brackets and braces in the correct order
    # Close braces first (inner objects), then brackets (arrays)
    missing_braces = open_braces - close_braces
    missing_brackets = open_brackets - close_brackets

    if missing_braces > 0:
        result += '}' * missing_braces
    if missing_brackets > 0:
        result += ']' * missing_brackets

    return result


def _validate_commands(commands: List[Dict]) -> None:
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
    }

    allowed_fields_by_action = {
        "PlaceObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "PlaceHuman": {"action", "humanName", "x", "y", "z", "rotationY"},
        "CreateObjectByName": {"action", "objectName", "x", "y", "z", "rotationY", "scaleX", "scaleY", "scaleZ"},
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
        "PlayHumanAnimationFromResources": {"action", "controllerPath", "stateName", "crossFadeTime"},
        "SetHumanAnimatorFloat": {"action", "paramName", "value"},
        "SetHumanAnimatorBool": {"action", "paramName", "boolValue"},
        "SetHumanAnimatorTrigger": {"action", "paramName"},
        "MoveObject": {"action", "objectName", "x", "y", "z", "rotationY"},
        "DeleteObject": {"action", "objectName"},
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



def _clamp_positions(commands: List[Dict]) -> List[Dict]:
    """Clamp placed-object positions to stay inside room bounds.
    Structural elements (walls, ceiling) have large scaleX/Z and are skipped."""
    X_MIN, X_MAX = -2.8, 2.8
    Z_MIN, Z_MAX = 0.1, 5.9
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
    """In loop iterations, drop any CreateObjectByName commands that look like walls/ceiling
    (large scaleX or scaleZ). The room shell is fixed after initial generation."""
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
    """Remove duplicate structural CreateObjectByName commands (walls/ceiling) that land
    at approximately the same position. Keeps only the first occurrence."""
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


def _write_commands_safe(path: Path, commands: List[Dict], retries: int = 5, delay: float = 0.5) -> None:
    """Write JSON to a temp file then replace the target to avoid Unity file-lock errors."""
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


def _generate_plan(description: str, client, agent_dir: Path) -> Dict:
    """Generate a structured room layout plan and save to plan.json for the reflection module."""
    plan_path = agent_dir / "api_files" / "plan.json"

    system = (
        "You are a hospital room layout planner. Given a room description, generate a structured "
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

    # Fallback: write a minimal but useful plan
    plan = {
        "room_type": "CT Examination Room",
        "description": description,
        "layout_goals": {
            "interior_bounds": "x in [-3,3], z in [1,6]",
            "minimum_objects": 10,
            "focal_point": "CT scanner near (0,0,2)",
        },
        "steps": [
            {"step": 1, "action": "CT scanner placed as room focal point", "objects": ["Tomograph"], "target_area": "near (0,0,2)", "condition": "scanner visible near room center"},
            {"step": 2, "action": "Exam table aligned with scanner bore axis", "objects": ["Exam Table"], "target_area": "adjacent to scanner on z-axis", "condition": "table adjacent to scanner"},
            {"step": 3, "action": "Control station on right side", "objects": ["Monitor", "Chair", "Utility Cart"], "target_area": "x > 0, near scanner", "condition": "monitor and chair visible on right side"},
            {"step": 4, "action": "Clinical support items placed", "objects": ["IV Drip", "Waste Bucket", "Sanitizer"], "target_area": "near table and entry", "condition": "IV drip, bucket, and sanitizer visible"},
            {"step": 5, "action": "Room exterior with walls, ceiling, doorway generated", "objects": ["Walls", "Ceiling", "Light"], "target_area": "perimeter of room", "condition": "4 walls, ceiling, and doorway gap visible"},
        ],
    }
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    logger.warning(f"Fallback plan saved to {plan_path}")
    return plan


def _load_screenshots(xz_path: Path, xy_path: Path, yz_path: Path) -> Dict[str, Optional[str]]:
    """Load orthographic view screenshots as base64 strings."""
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
    """Build compact summary of placed objects for LLM context."""
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


def _call_groq_and_parse(client, messages, prefabs, is_loop: bool = False) -> Optional[List[Dict]]:
    """Call Groq API with retry, parse/validate the resulting command array."""
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

        if commands:
            try:
                if is_loop:
                    commands = _filter_loop_structural(commands)
                else:
                    commands = _dedup_structural(commands)
                commands = _strip_unknown_fields(commands)
                commands = _fix_light_commands(commands)
                commands = _fix_prefab_names(commands, prefabs)
                commands = _clamp_positions(commands)
                _validate_commands(commands)
                return commands
            except Exception as e:
                last_error = e
                commands = None

        logger.warning(f"Parse/validate failed (attempt {attempt + 1}/3): {last_error}")
        time.sleep(1.0)

    logger.error(f"Could not generate valid commands after all attempts: {last_error}")
    return None


def _stamp_commands(commands: List[Dict]) -> None:
    """Add id/timestamp/executed fields to commands in-place."""
    for i, cmd in enumerate(commands):
        if isinstance(cmd, dict):
            cmd.setdefault("id", f"cmd_{int(time.time() * 1000)}_{i}")
            cmd.setdefault("timestamp", time.time())
            cmd.setdefault("executed", False)


# ---------------------------------------------------------------------------
# Phase 1: Static environment generation + reflection correction
# ---------------------------------------------------------------------------

def phase1_generate_environment(
    client, description, prefabs, animators, plan, out_path, reflector,
    xz_path, xy_path, yz_path, interval,
):
    """Generate the static scene and run reflection corrections until all plan
    steps are confirmed complete. Returns the list of all placed commands."""

    # --- Helper functions (defined early so they can be used throughout) ---

    MAX_MEANINGFUL_OBJECTS = 25  # excludes structural Cubes (walls/ceiling/floor)

    def _count_meaningful(placed: List[Dict]) -> int:
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
        """Parse structured MoveObject/DeleteObject from reflection alignment_suggestions."""
        cmds = []
        if not hints:
            return cmds
        move_pattern = re.compile(
            r"MoveObject\s+(\S+)\s+x=([-\d.]+)\s+y=([-\d.]+)\s+z=([-\d.]+)(?:\s+rotationY=([-\d.]+))?",
            re.IGNORECASE,
        )
        delete_pattern = re.compile(r"DeleteObject\s+(\S+)", re.IGNORECASE)
        for m in move_pattern.finditer(hints):
            cmds.append({
                "action": "MoveObject", "objectName": m.group(1),
                "x": float(m.group(2)), "y": float(m.group(3)), "z": float(m.group(4)),
                "rotationY": float(m.group(5)) if m.group(5) else 0.0,
            })
        for m in delete_pattern.finditer(hints):
            name = m.group(1)
            if not any(c["objectName"] == name for c in cmds):
                cmds.append({"action": "DeleteObject", "objectName": name})
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

    # ----- Hardcoded room shell (walls, floor, ceiling) -----
    # These MUST be exact — LLM cannot be trusted with structural geometry.
    # Room: 6m x 6m, x in [-3, 3], z in [0, 6], y in [0, 3].
    room_shell: List[Dict] = [
        # Floor
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 0, "y": 0,   "z": 3, "rotationY": 0, "scaleX": 6, "scaleY": 0.2, "scaleZ": 6},
        # Ceiling
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 0, "y": 3,   "z": 3, "rotationY": 0, "scaleX": 6, "scaleY": 0.2, "scaleZ": 6},
        # Left wall (x = -3)
        {"action": "CreateObjectByName", "objectName": "Cube", "x": -3, "y": 1.5, "z": 3, "rotationY": 0, "scaleX": 0.2, "scaleY": 3, "scaleZ": 6},
        # Right wall (x = 3)
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 3,  "y": 1.5, "z": 3, "rotationY": 0, "scaleX": 0.2, "scaleY": 3, "scaleZ": 6},
        # Back wall (z = 6)
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 0,  "y": 1.5, "z": 6, "rotationY": 0, "scaleX": 6, "scaleY": 3, "scaleZ": 0.2},
        # Front wall LEFT half (doorway gap at center, x = -0.6 to +0.6)
        {"action": "CreateObjectByName", "objectName": "Cube", "x": -1.8, "y": 1.5, "z": 0, "rotationY": 0, "scaleX": 2.4, "scaleY": 3, "scaleZ": 0.2},
        # Front wall RIGHT half
        {"action": "CreateObjectByName", "objectName": "Cube", "x": 1.8,  "y": 1.5, "z": 0, "rotationY": 0, "scaleX": 2.4, "scaleY": 3, "scaleZ": 0.2},
        # Bright hospital lighting
        {"action": "SetLightIntensity", "scale": 1.5},
        {"action": "SetAmbientColor", "r": 0.88, "g": 0.90, "b": 0.92},
    ]
    _stamp_commands(room_shell)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_commands_safe(out_path, room_shell)
    logger.info(f"[Phase 1] Room shell written: {len(room_shell)} structural commands")

    # ----- LLM generates interior objects only (no walls/ceiling/floor) -----
    system = (
        "You are a Unity scene builder placing interior objects inside a pre-built CT examination room. "
        "The room shell (walls, floor, ceiling, doorway, lighting) is ALREADY placed — do NOT generate them. "
        "Return ONLY a valid JSON array of commands. No markdown, no code fences, no explanations. "
        "Room bounds: interior x in [-2.8, 2.8], z in [0.2, 5.9], y=0 for floor-standing objects. "

        "IMPORTANT: "
        "1. Do NOT output any CreateObjectByName commands — the room structure is already done. "
        "2. PlaceObject objectName: exact name from available_prefabs only. "
        "3. PlaceHuman humanName: exact name from available_prefabs only. "
        "4. Never invent names. If a name is not in available_prefabs, skip it. "
        "5. Place at most 1-2 humans (e.g. Patient + Doctor or Nurse). "
        "6. Do NOT output any PlayHumanAnimationFromResources or animation commands. "
        "   Characters should remain in their default idle pose. "
    )

    user = {
        "description": description,
        "available_prefabs": prefabs,
        "rules": {
            "bounds": "Interior objects: x in [-2.8,2.8], z in [0.2,5.9], y=0 for floor objects.",
            "no_invented_names": "Only use objectNames that appear in available_prefabs. Never invent names.",
            "no_structural": "Do NOT generate CreateObjectByName — walls/floor/ceiling already exist.",
            "no_animations": "Do NOT generate PlayHumanAnimationFromResources — characters use default idle.",
        },
        "schema_examples": [
            {"action": "PlaceObject", "objectName": "Tomograpf_01", "x": 0, "y": 0, "z": 3, "rotationY": 0},
            {"action": "PlaceHuman", "humanName": "Doctor_01", "x": -1.5, "y": 0, "z": 2.2, "rotationY": 0},
        ],
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user)},
    ]
    commands = _call_groq_and_parse(client, messages, prefabs, is_loop=False)
    if not commands:
        logger.error("[Phase 1] Interior generation failed. Exiting.")
        return room_shell

    # Filter out structural and animation commands — LLM should not generate these
    _blocked_actions = {"CreateObjectByName", "PlayHumanAnimationFromResources",
                        "PlayHumanAnimation", "SetHumanAnimationController"}
    commands = [c for c in commands if c.get("action") not in _blocked_actions]
    commands = _dedup_humans(commands, [])  # also limit humans from the start

    _stamp_commands(commands)
    existing_file = _load_existing_commands(out_path)
    _write_commands_safe(out_path, existing_file + commands)
    logger.info(f"[Phase 1] Interior objects: wrote {len(commands)} commands")

    all_placed: List[Dict] = list(room_shell) + list(commands)

    # ----- Reflection + correction loop -----
    loop_system = (
        "You are a clinical room designer adding detail to an existing CT examination room in Unity. "
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

        "AVAILABLE ADDITIONS (only use names from available_prefabs): "
        "Extra furniture: Trolley_01, Basin_Stand_01, Sink_01, Towel_Dispenser_01, Hand_Dryer_01. "
        "Equipment: Screen_02. "
        "Props (on surfaces only): Box_02-04, Folder_02-05, Syringe_01, Thermometer_01. "
        "Wall decor: Poster_03, Poster_04, Poster_05. "

        "EDITS from alignment_suggestions: "
        "MoveObject: {\"action\":\"MoveObject\",\"objectName\":\"<exact name from scene>\",\"x\":...,\"y\":...,\"z\":...,\"rotationY\":...}. "
        "DeleteObject: {\"action\":\"DeleteObject\",\"objectName\":\"<exact name from scene>\"}. "
        "Use these to fix misplaced objects or remove clutter identified in alignment_suggestions."
    )

    plan_steps = plan.get("steps", [])
    current_step_idx = 0
    step_iteration_count = 0
    MAX_STEP_ITERATIONS = 4
    last_mtime = 0.0
    last_loop_time = 0.0
    last_applied_hints = ""  # track previous alignment_suggestions to avoid repeating identical MoveObjects
    stale_count = 0  # how many consecutive iterations returned the same suggestions

    # --- FIX 1: Wait for screenshots to be refreshed after initial generation ---
    # We need screenshots that reflect the CURRENT scene (after initial commands),
    # not stale ones from a previous run. Wait until mtime is newer than generation start.
    SCREENSHOT_WAIT_TIMEOUT = 15  # seconds
    generation_time = time.time()
    logger.info(f"[Phase 1] Waiting up to {SCREENSHOT_WAIT_TIMEOUT}s for Unity to refresh screenshots...")
    wait_start = time.time()
    screenshots_ready = False
    while time.time() - wait_start < SCREENSHOT_WAIT_TIMEOUT:
        fresh = [
            p for p in [xz_path, xy_path, yz_path]
            if p.exists() and p.stat().st_size > 0 and p.stat().st_mtime > generation_time
        ]
        if fresh:
            screenshots_ready = True
            logger.info(f"[Phase 1] Fresh screenshots detected ({len(fresh)}/3). Starting reflection loop.")
            break
        time.sleep(2.0)

    if not screenshots_ready:
        # Fall back: accept any existing screenshots even if stale
        if any(p.exists() and p.stat().st_size > 0 for p in [xz_path, xy_path, yz_path]):
            screenshots_ready = True
            logger.info("[Phase 1] Using existing screenshots (may be stale). Starting reflection loop.")
        else:
            logger.warning("[Phase 1] No screenshots available after timeout. "
                           "Reflection will run in CORRECTION-ONLY mode (no new objects).")

    while True:
        # Wait for screenshot update or timer
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
        # FIX 3: Default to no additions when blind
        suggest_additions = False

        # --- Reflection ---
        try:
            if plan_steps:
                current_plan_step = plan_steps[min(current_step_idx, len(plan_steps) - 1)]
            else:
                current_plan_step = {
                    "step": "room_layout_evaluation",
                    "action": "Evaluate overall CT room layout for realism and alignment",
                    "condition": "Room looks like a realistic CT examination room",
                }
            step_label = current_plan_step.get("action", f"step {current_step_idx + 1}")
            logger.info(f"[Phase 1 Reflection] Step {current_step_idx + 1}/{len(plan_steps)}: {step_label}")

            reflection_result = reflector.reflect_step(
                plan_step=current_plan_step,
                game_state={"placed_objects": placed_summary},
            )
            alignment_hints = reflection_result.alignment_suggestions

            # FIX 3 + 6: Only allow additions if screenshots confirm sparse AND under cap
            meaningful_count = _count_meaningful(all_placed)
            if not has_screenshots:
                suggest_additions = False  # blind = no new objects
            elif meaningful_count >= MAX_MEANINGFUL_OBJECTS:
                suggest_additions = False  # at cap
                logger.info(f"  Object cap reached ({meaningful_count}/{MAX_MEANINGFUL_OBJECTS}) — no additions")
            else:
                suggest_additions = reflection_result.suggest_additions

            logger.info(f"  step_complete={reflection_result.step_complete}  room_status={reflection_result.room_status}  suggest_additions={suggest_additions}  objects={meaningful_count}")
            if reflection_result.layout_comments:
                logger.info(f"  layout: {reflection_result.layout_comments}")
            if alignment_hints:
                logger.info(f"  suggestions: {alignment_hints}")

            step_iteration_count += 1
            should_advance = (
                reflection_result.step_complete == "yes"
                or step_iteration_count >= MAX_STEP_ITERATIONS
            )
            if should_advance and plan_steps and current_step_idx < len(plan_steps) - 1:
                reason = "complete" if reflection_result.step_complete == "yes" else f"max iters ({MAX_STEP_ITERATIONS})"
                current_step_idx += 1
                step_iteration_count = 0
                reflector.reset_step_count()
                logger.info(f"  Step {reason} — advancing to step {current_step_idx + 1}: {plan_steps[current_step_idx].get('action', '')}")
            elif should_advance and current_step_idx >= len(plan_steps) - 1:
                logger.info("[Phase 1] All plan steps complete — environment generation finished.")
                break
        except Exception as e:
            logger.warning(f"[Phase 1 Reflection] skipped: {e}")
            alignment_hints = ""

        # --- FIX 4: Execute alignment suggestions directly (bypass LLM) ---
        # Skip if identical to last iteration (avoids spamming the same MoveObject 15 times)
        if alignment_hints and alignment_hints != last_applied_hints:
            direct_commands = _parse_alignment_commands(alignment_hints)
            if direct_commands:
                _stamp_commands(direct_commands)
                all_placed.extend(direct_commands)
                existing_file = _load_existing_commands(out_path)
                _write_commands_safe(out_path, existing_file + direct_commands)  # FIX 2: keep ALL
                logger.info(f"[Phase 1] Applied {len(direct_commands)} direct corrections (MoveObject/DeleteObject)")
            last_applied_hints = alignment_hints
            stale_count = 0
        elif alignment_hints == last_applied_hints and alignment_hints:
            stale_count += 1
            logger.info(f"[Phase 1] Same suggestions as last iteration — skipping (stale x{stale_count}).")
            if stale_count >= 2 and _count_meaningful(all_placed) >= MAX_MEANINGFUL_OBJECTS:
                logger.info("[Phase 1] Reflection stale + object cap reached — finishing early.")
                break

        # --- Correction / fill via LLM (only if suggest_additions=True) ---
        if suggest_additions:
            existing_summary = [
                {"id": c.get("id", ""), "objectName": c.get("objectName") or c.get("humanName", "?"),
                 "x": c.get("x"), "y": c.get("y"), "z": c.get("z")}
                for c in all_placed if c.get("action") in {"PlaceObject", "CreateObjectByName", "PlaceHuman"}
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
                image_content = [{"type": "text", "text": "Orthographic screenshots (XZ=top-down, XY=front, YZ=side):"}]
                for lbl, data in view_images.items():
                    if data:
                        image_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
                loop_messages.append({"role": "user", "content": image_content})

            new_commands = _call_groq_and_parse(client, loop_messages, prefabs, is_loop=True)
            if new_commands:
                new_commands = _dedup_humans(new_commands, all_placed)  # FIX 5
                if new_commands:
                    _stamp_commands(new_commands)
                    all_placed.extend(new_commands)
                    existing_file = _load_existing_commands(out_path)
                    _write_commands_safe(out_path, existing_file + new_commands)  # FIX 2: keep ALL
                    logger.info(f"[Phase 1] Appended {len(new_commands)} fill commands")
        else:
            logger.info("[Phase 1] CORRECTION-ONLY mode — skipping LLM fill call.")

        time.sleep(interval)

    return all_placed


# ---------------------------------------------------------------------------
# Phase 2: User interaction loop
# ---------------------------------------------------------------------------

def phase2_interaction_loop(
    client, description, prefabs, animators, all_placed, out_path,
    reflector, xz_path, xy_path, yz_path, interval, max_iterations,
):
    """After the static environment is stable, enter an interaction loop that
    responds to user actions (screenshot changes) and maintains the scene."""

    logger.info("=" * 60)
    logger.info("  PHASE 2: User Interaction")
    logger.info("  Environment is ready. Monitoring for user activity...")
    logger.info("=" * 60)

    interaction_system = (
        "You are a VR CT examination room assistant. The room environment has been fully set up. "
        "Return ONLY a valid JSON array. No markdown, no explanations. "
        "You observe the scene through screenshots and respond to the user's current situation. "

        "CORE RULES: "
        "1. Do NOT re-create the room or add furniture — the room is complete. "
        "2. Respond to what you see: if the user is near equipment, ensure it is interactive. "
        "3. You may use MoveObject/DeleteObject to tidy up if something looks wrong. "
        "4. You may adjust lighting or environment (SetTimeOfDay, SetLightIntensity, etc.). "
        "5. You may trigger character animations if appropriate. "
        "6. If nothing needs changing, return an empty array []. "

        "AVAILABLE ACTIONS: "
        "MoveObject: {\"action\":\"MoveObject\",\"objectName\":\"...\",\"x\":...,\"y\":...,\"z\":...,\"rotationY\":...} "
        "DeleteObject: {\"action\":\"DeleteObject\",\"objectName\":\"...\"} "
        "SetTimeOfDay, SetLightIntensity, SetLightColor, SetAmbientColor, SetFogEnabled, SetFogDensity "
        "PlayHumanAnimationFromResources: trigger character animations "
    )

    last_mtime = 0.0
    iterations = 0

    while True:
        if max_iterations and iterations >= max_iterations:
            logger.info("[Phase 2] Max iterations reached. Stopping.")
            break

        # Wait for screenshot update (user activity triggers Unity to save new screenshots)
        latest_mtime = max(
            (p.stat().st_mtime if p.exists() else 0.0) for p in [xz_path, xy_path, yz_path]
        )
        if latest_mtime <= last_mtime:
            time.sleep(interval)
            continue

        last_mtime = latest_mtime

        view_images = _load_screenshots(xz_path, xy_path, yz_path)
        if not any(view_images.values()):
            time.sleep(interval)
            continue

        placed_summary = _build_placed_summary(all_placed)

        interaction_user = {
            "objects_in_scene": [
                {"objectName": c["objectName"], "x": c.get("x"), "y": c.get("y"), "z": c.get("z")}
                for c in placed_summary
            ],
            "total_objects": len(placed_summary),
            "instruction": (
                "Observe the screenshots. If the user needs assistance or something looks wrong, "
                "generate commands. Otherwise return []."
            ),
        }

        messages = [
            {"role": "system", "content": interaction_system},
            {"role": "user", "content": json.dumps(interaction_user)},
        ]

        # Attach screenshots
        image_content = [{"type": "text", "text": "Current scene (XZ=top-down, XY=front, YZ=side):"}]
        for lbl, data in view_images.items():
            if data:
                image_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
        messages.append({"role": "user", "content": image_content})

        new_commands = _call_groq_and_parse(client, messages, prefabs, is_loop=True)
        if new_commands:
            _stamp_commands(new_commands)
            all_placed.extend(new_commands)
            existing = _load_existing_commands(out_path)
            _write_commands_safe(out_path, existing + new_commands)  # keep ALL (including executed)
            logger.info(f"[Phase 2] Generated {len(new_commands)} interaction commands")
        else:
            logger.debug("[Phase 2] No action needed.")

        iterations += 1
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Two-phase scene generator: build environment, then interact")
    parser.add_argument("--path", type=str, default="api_files/experience.txt", help="Path to experience file")
    parser.add_argument("--out", type=str, default=None, help="Output path for unity_commands.json")
    parser.add_argument("--interval", type=float, default=3.0, help="Loop interval in seconds")
    parser.add_argument("--max-iterations", type=int, default=0, help="Max Phase 2 iterations (0 = infinite)")
    parser.add_argument("--screenshot", type=str, default=None, help="Screenshot path (overrides UNITY_SCREENSHOT_PATH)")
    parser.add_argument("--phase1-only", action="store_true", help="Exit after Phase 1 (environment generation only)")
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

    if not prompt_path.exists():
        raise FileNotFoundError(f"Experience file not found: {prompt_path}")

    description = prompt_path.read_text(encoding="utf-8").strip()
    if not description:
        raise ValueError("Experience file is empty.")

    prefabs = _load_prefab_names(resources_dir)
    animators = _load_animator_names(resources_dir)

    # Clear all runtime files at startup
    for jf in [out_path, agent_dir / "api_files" / "reflection.json", agent_dir / "api_files" / "actions.json"]:
        try:
            jf.write_text("[]", encoding="utf-8")
            logger.info(f"[Init] Cleared {jf.name}")
        except Exception as e:
            logger.warning(f"[Init] Could not clear {jf.name}: {e}")

    # NOTE: Do NOT delete screenshots at init — Unity ScreenshotLoopSender will
    # overwrite them automatically, and deleting causes a long wait for re-creation.
    screenshot_base = screenshot_path.with_suffix("")

    client = Groq(api_key=api_key)

    # Generate plan.json
    logger.info("Generating room layout plan...")
    plan = _generate_plan(description, client, agent_dir)

    # Screenshot paths
    xz_path = screenshot_base.parent / (screenshot_base.name + "_xz.png")
    xy_path = screenshot_base.parent / (screenshot_base.name + "_xy.png")
    yz_path = screenshot_base.parent / (screenshot_base.name + "_yz.png")

    reflector = ReflectionModule()

    # ===== PHASE 1: Static Environment Generation =====
    all_placed = phase1_generate_environment(
        client, description, prefabs, animators, plan, out_path, reflector,
        xz_path, xy_path, yz_path, args.interval,
    )

    if not all_placed:
        logger.error("Phase 1 produced no commands. Exiting.")
        return

    if args.phase1_only:
        logger.info("--phase1-only flag set. Skipping Phase 2.")
        logger.info(f"Total objects placed: {len(_build_placed_summary(all_placed))}")
        return

    # ===== PHASE 2: User Interaction =====
    phase2_interaction_loop(
        client, description, prefabs, animators, all_placed, out_path,
        reflector, xz_path, xy_path, yz_path, args.interval, args.max_iterations,
    )


if __name__ == "__main__":
    main()
