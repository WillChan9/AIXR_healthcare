"""
LLM-as-Judge evaluation for semantic quality of generated scenes.

Calls an LLM (OpenAI GPT-4o or Groq) to rate:
  1. Scene-Type Coherence (1-5): Are the objects appropriate for the scene type?
  2. Spatial Semantics (1-5): Are objects placed in logical locations?

Usage:
    python llm_judge.py --results-dir path/to/run_output --experience path/to/experience.txt
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return None
    return json.loads(text)


def _parse_experience_config(txt: str) -> dict:
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


def _build_scene_summary(scene_state: List[Dict]) -> str:
    """Build a concise text summary of the scene for the LLM judge."""
    objects = [obj for obj in scene_state if obj.get("objectName", "") != "Cube"]
    if not objects:
        return "The scene is empty (no objects placed)."

    lines = []
    for obj in objects:
        name = obj.get("objectName", "Unknown")
        x, y, z = obj.get("x", 0), obj.get("y", 0), obj.get("z", 0)
        lines.append(f"  - {name} at position ({x:.2f}, {y:.2f}, {z:.2f})")
    return f"Objects in scene ({len(objects)} total):\n" + "\n".join(lines)


def _call_groq(messages: list, model: str = "meta-llama/llama-4-scout-17b-16e-instruct") -> str:
    """Call Groq API."""
    from groq import Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_completion_tokens=512,
    )
    return completion.choices[0].message.content.strip()


def _call_openai(messages: list, model: str = "gpt-4o") -> str:
    """Call OpenAI API."""
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=512,
    )
    return completion.choices[0].message.content.strip()


def _call_llm(messages: list, provider: str = "groq") -> str:
    if provider == "openai":
        return _call_openai(messages)
    else:
        return _call_groq(messages)


def _parse_rating(response: str) -> Dict[str, Any]:
    """Parse structured rating from LLM response."""
    result = {"raw_response": response}
    try:
        # Try JSON parse first
        data = json.loads(response)
        if isinstance(data, dict):
            result.update(data)
            return result
    except json.JSONDecodeError:
        pass

    # Fallback: regex extraction
    import re
    for key in ("coherence_score", "spatial_score", "overall_score"):
        m = re.search(rf'"{key}"\s*:\s*(\d+)', response)
        if m:
            result[key] = int(m.group(1))
        else:
            # Try alternative patterns
            alt_key = key.replace("_score", "")
            m2 = re.search(rf'{alt_key}\s*[:=]\s*(\d+)', response, re.IGNORECASE)
            if m2:
                result[key] = int(m2.group(1))

    return result


def judge_scene(
    scene_state: List[Dict],
    scene_type: str,
    description: str,
    room_config: dict,
    provider: str = "groq",
) -> Dict[str, Any]:
    """Ask LLM to rate scene quality on coherence and spatial semantics."""

    scene_summary = _build_scene_summary(scene_state)
    room_w = room_config.get("room_width", "?")
    room_d = room_config.get("room_depth", "?")

    system_prompt = (
        "You are an expert evaluator for 3D scene generation in medical/healthcare VR training environments. "
        "You will be given a scene description, the intended scene type, room dimensions, and a list of placed objects with positions. "
        "Rate the scene on two criteria, each on a 1-5 scale:\n\n"
        "1. **coherence_score** (Scene-Type Coherence): How appropriate are the placed objects for this scene type? "
        "   1=completely wrong objects, 3=mostly appropriate, 5=perfect object selection.\n\n"
        "2. **spatial_score** (Spatial Semantics): Are objects placed in logical locations? "
        "   Consider: wall-mounted items should be high (y>1.5), floor items at y=0, small props on surfaces (y>0.7), "
        "   functional groupings (desk items together, bed-side equipment near beds), walkway clearance. "
        "   1=random/illogical placement, 3=mostly logical, 5=professionally arranged.\n\n"
        "Return ONLY a JSON object: "
        '{"coherence_score": N, "spatial_score": N, "coherence_reasoning": "...", "spatial_reasoning": "..."}'
    )

    user_prompt = (
        f"Scene Type: {scene_type}\n"
        f"Room Dimensions: {room_w}m x {room_d}m\n\n"
        f"Original Description:\n{description[:500]}\n\n"
        f"Placed Objects:\n{scene_summary}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    response = _call_llm(messages, provider=provider)
    return _parse_rating(response)


def run_judge(
    results_dir: Path,
    experience_path: Path,
    provider: str = "groq",
) -> Dict[str, Any]:
    """Run LLM judge on a single experiment run."""

    scene_state = _load_json(results_dir / "scene_state.json") or []
    exp_text = experience_path.read_text(encoding="utf-8")
    cfg = _parse_experience_config(exp_text)
    description = exp_text[:exp_text.find("[room_config]")].strip() if "[room_config]" in exp_text else exp_text.strip()
    scene_type = cfg.get("scene_type", "Unknown")

    ratings = judge_scene(scene_state, scene_type, description, cfg, provider=provider)
    ratings["scene_type"] = scene_type
    ratings["results_dir"] = str(results_dir)

    return ratings


def main():
    parser = argparse.ArgumentParser(description="LLM-as-Judge scene evaluation")
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--experience", type=Path, required=True)
    parser.add_argument("--provider", choices=["groq", "openai"], default="groq")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    ratings = run_judge(args.results_dir, args.experience, provider=args.provider)

    print(f"\n{'=' * 50}")
    print(f"  LLM Judge: {ratings.get('scene_type', '?')}")
    print(f"{'=' * 50}")
    print(f"  Coherence Score: {ratings.get('coherence_score', '?')} / 5")
    print(f"  Spatial Score:   {ratings.get('spatial_score', '?')} / 5")
    if "coherence_reasoning" in ratings:
        print(f"  Coherence: {ratings['coherence_reasoning']}")
    if "spatial_reasoning" in ratings:
        print(f"  Spatial:   {ratings['spatial_reasoning']}")
    print()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(ratings, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved to: {args.output}")


if __name__ == "__main__":
    main()
