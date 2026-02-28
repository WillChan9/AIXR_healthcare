import os
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from dotenv import load_dotenv
from groq import Groq


def _load_prefab_names(resources_dir: Path) -> List[str]:
    if not resources_dir.exists():
        return []
    return sorted({p.stem for p in resources_dir.rglob("*.prefab")})


def _load_animator_names(resources_dir: Path) -> List[str]:
    if not resources_dir.exists():
        return []
    return sorted({p.stem for p in resources_dir.rglob("*.controller")})


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
    norm = _normalize_name(name)
    if norm in lookup:
        return lookup[norm]

    # Substring match (handles suffixes like _01)
    candidates: List[Tuple[str, str]] = [(p, _normalize_name(p)) for p in prefabs]
    matches = [p for p, n in candidates if norm in n or n in norm]
    if matches:
        # Choose closest length match
        matches.sort(key=lambda p: (abs(len(_normalize_name(p)) - len(norm)), len(p)))
        return matches[0]
    return None


def _fallback_primitive(name_hint: Optional[str]) -> str:
    norm = _normalize_name(name_hint or "")
    if "monitor" in norm or "screen" in norm:
        return "Quad"
    if "clock" in norm:
        return "Cylinder"
    if "sign" in norm:
        return "Plane"
    if "drip" in norm or "iv" in norm:
        return "Cylinder"
    if "cart" in norm:
        return "Cube"
    if "bucket" in norm:
        return "Cylinder"
    if "sanitizer" in norm:
        return "Cylinder"
    if "tomograph" in norm or "scanner" in norm:
        return "Cube"
    return "Cube"


def _fix_prefab_names(commands: List[Dict], prefabs: List[str]) -> List[Dict]:
    lookup = _build_prefab_lookup(prefabs)
    for cmd in commands:
        action = cmd.get("action")
        if action == "PlaceObject":
            resolved = _resolve_prefab_name(cmd.get("objectName"), prefabs, lookup)
            if resolved:
                cmd["objectName"] = resolved
            else:
                cmd["action"] = "CreateObjectByName"
                cmd["objectName"] = _fallback_primitive(cmd.get("objectName"))
        elif action == "PlaceHuman":
            name = cmd.get("humanName") or cmd.get("objectName")
            resolved = _resolve_prefab_name(name, prefabs, lookup)
            if resolved:
                cmd["humanName"] = resolved
            else:
                cmd["action"] = "CreateObjectByName"
                cmd.pop("humanName", None)
                cmd["objectName"] = "Capsule"
    return commands


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

    # Try to locate JSON array
    start_idx = text.find("[")
    end_idx = text.rfind("]")
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        raise ValueError("No JSON array found in Groq response.")

    array_text = text[start_idx:end_idx + 1]
    return json.loads(array_text)


def _repair_incomplete_json(json_str: str) -> str:
    """Try to repair incomplete JSON by adding missing closing brackets and braces."""
    if not json_str:
        return json_str

    result = json_str.strip()

    # Remove trailing commas before closing brackets/braces
    import re
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


def main():
    parser = argparse.ArgumentParser(description="Groq -> Unity command generator (one-shot)")
    parser.add_argument("--path", type=str, default="api_files/experience.txt", help="Path to experience file (default: Agent/context/experience.txt)")
    parser.add_argument("--out", type=str, default=None, help="Output path for unity_commands.json")
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

    if not prompt_path.exists():
        raise FileNotFoundError(f"Experience file not found: {prompt_path}")

    description = prompt_path.read_text(encoding="utf-8").strip()
    if not description:
        raise ValueError("Experience file is empty.")

    prefabs = _load_prefab_names(resources_dir)
    animators = _load_animator_names(resources_dir)
    prefabs_preview = prefabs[:200]
    animators_preview = animators[:200]

    system = (
        "You are a Unity environment planner. Return ONLY a JSON array of commands. "
        "Do not wrap in code fences. Each command must match the schema exactly. "
        "Use ONLY the exact prefab names provided in available_prefabs. Do not invent names. "
        "If no prefab matches, use "
        "CreateObjectByName with primitives: Cube, Sphere, Capsule, Cylinder, Plane, Quad."
    )

    user = {
        "description": description,
        "available_prefabs": prefabs_preview,
        "available_animators": animators_preview,
        "rules": {
            "keep_compact": "Keep objects within x in [-3,3], z in [1,6].",
            "center_object": "Place primary equipment (e.g., Tomograph/CT scanner) near (0,0,2).",
            "y": 0
        },
        "schema_examples": [
            {"action": "PlaceObject", "objectName": "PrefabName", "x": 0, "y": 0, "z": 2, "rotationY": 0},
            {"action": "CreateObjectByName", "objectName": "Cube", "x": 1, "y": 0, "z": 2, "rotationY": 0},
            {"action": "SetHumanAnimationController", "controllerPath": "ControllerName"}
        ]
    }

    client = Groq(api_key=api_key)
    commands = None
    last_error = None

    for attempt in range(3):
        completion = client.chat.completions.create(
            model="meta-llama/llama-4-maverick-17b-128e-instruct",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)}
            ],
            temperature=0.6,
            max_completion_tokens=2048,
        )

        content = completion.choices[0].message.content or ""
        print(f"----- GROQ RAW RESPONSE (attempt {attempt + 1}/3) -----")
        print(content)
        print("------------------------------------------------------")

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
                commands = _fix_prefab_names(commands, prefabs)
                _validate_commands(commands)
                break
            except Exception as e:
                last_error = e
                commands = None

    if not commands:
        raise ValueError(f"Failed to parse valid commands after 3 attempts: {last_error}")

    # Add ids/timestamps/executed if missing
    for i, cmd in enumerate(commands):
        if isinstance(cmd, dict):
            cmd.setdefault("id", f"cmd_{int(time.time() * 1000)}_{i}")
            cmd.setdefault("timestamp", time.time())
            cmd.setdefault("executed", False)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(commands, indent=2), encoding="utf-8")
    print(f"Wrote {len(commands)} commands to {out_path}")


if __name__ == "__main__":
    main()
