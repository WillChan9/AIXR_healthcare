# AIXR Healthcare

LLM-driven generation of interactive VR healthcare training scenes in Unity.

You describe a scene in plain English: a room, the staff in it, and how objects should
respond when touched. A Python agent turns that description into a furnished, staffed
Unity scene. It then keeps running while a trainee explores in VR, reacting to their
interactions with character animations and a scripted story.

```
experience.txt ──► Python agent (Agent/) ──► unity_commands.json ──► Unity (AgentNetworkBridge)
                         ▲                                                   │
                         └──── interaction_events.json / scene_state.json ◄──┘
```

## How it works

The pipeline runs in two phases. Both are driven by `Agent/groq_static_test.py`.

**Phase 1: Environment generation**

1. **Scene synthesis.** A frontier LLM (OpenAI, `--frontier-model`) reads the scene
   description and produces a room configuration and a step-by-step placement plan.
2. **Placement with reflection.** For each plan step, a Groq-hosted Llama model writes
   Unity commands (place prefab, place character, lighting, and so on). Unity executes
   them and reports the resulting `scene_state.json` along with orthographic screenshots.
   A `ReflectionModule` critiques the result and iterates until the step is complete.
3. **Interaction design.** The LLM builds an *interaction map* (how each object responds
   to the player) and a *story chain* (an ordered list of scripted beats).

**Phase 2: Interaction loop**

Unity records player events (hover, grab, release, activate, touch) to
`interaction_events.json`. The agent polls that file and responds on two layers:

- **Ambient layer** (`interaction_animations.py`): an immediate animation response
  for each object, driven by the interaction map generated in Phase 1.
- **Story layer** (`story_chain.py`): advances the scripted narrative when the player
  triggers the next beat.

Python and Unity communicate only through JSON files in
`firefighter project/Assets/Scripts/`, so no network setup is needed.

## Repository layout

```
Agent/                          Python agent
├── groq_static_test.py         Main entry point (Phase 1 + Phase 2)
├── main.py                     Library for writing Unity commands to unity_commands.json
├── interaction_animations.py   Phase 2 ambient (rule-based) animation responses
├── interaction_log.py          Persistent log of player interactions
├── story_chain.py              Phase 2 scripted story beats
├── watch_interactions.py       Live console viewer for player events
├── agent/
│   ├── Reflection_module.py    Phase 1 critique-and-retry loop
│   ├── animation_planner.py    Experimental LLM animation planner (not yet wired into the pipeline)
│   └── logger.py               Shared logging setup
├── api_files/experience.txt    The scene description to generate (edit this)
├── evaluation/                 Experiment harness, scenarios, and published results
└── examples/                   Small scripts showing the main.py command API

firefighter project/            Unity project (Unity 2022.3.42f1, URP, XR Interaction Toolkit)
├── Assets/Scripts/             Runtime C# (command bridge, scene builder, interaction reporting)
├── Assets/Editor/              Editor helpers that wire the bridge into the scene
├── Assets/Resources/           Prefabs and animator controllers the agent can spawn
└── Assets/Scenes/SampleScene.unity
```

> The Unity folder is named `firefighter project` for historical reasons. The project's
> focus is now healthcare training.

## Requirements

- **Unity 2022.3.42f1** (install through Unity Hub, with Android Build Support if you are
  targeting Quest)
- **Meta Quest + Quest Link** for VR, or the XR Device Simulator included in the project
  for desktop testing
- **Python 3.10+**
- API keys:
  - `GROQ_API_KEY` (required)
  - `OPENAI_API_KEY` (needed for frontier-model scene synthesis and the LLM judge; pass
    `--frontier-model ""` to run on Groq only)

## Quick start

1. **Install the Python dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r Agent/requirements.txt
   ```

2. **Add your API keys.** Create a `.env` file at the repository root:

   ```env
   GROQ_API_KEY=your_groq_key
   OPENAI_API_KEY=your_openai_key
   ```

3. **Start Unity.** Open `firefighter project/` in Unity Hub, load
   `Assets/Scenes/SampleScene.unity`, and press **Play**. The scene's `AI_Manager` object
   carries `AgentNetworkBridge`, which polls `unity_commands.json`.

4. **Describe a scene.** Edit `Agent/api_files/experience.txt`, or copy one of the
   scenarios from `Agent/evaluation/scenarios/`.

5. **Run the agent**

   ```bash
   python Agent/groq_static_test.py                  # Phase 1, then the Phase 2 loop
   python Agent/groq_static_test.py --phase1-only    # build the scene and exit
   python Agent/groq_static_test.py --fast-phase1    # short Phase 1 (3 steps x 1 iteration)
   ```

   Run `python Agent/groq_static_test.py --help` to see every option, including
   `--phase1-max-steps`, `--phase1-max-iters`, `--frontier-model`, and `--replay-trial`.

6. **Watch interactions** (optional, in a second terminal):

   ```bash
   python Agent/watch_interactions.py
   ```

Run-time output (logs, the generated plan, screenshots, the reflection history) is written
to `Agent/api_files/`. Git ignores it.

## Scene description format

`experience.txt` is free text describing the room, followed by an optional
`[interactions]` section of plain-English rules:

```
Surgery Prep Room — Precise surface placement and instrument layout

A surgery preparation room with strict focus on sterile fields ...
A surgeon and a scrub nurse are present, going through final pre-op checks.

[interactions]
Touching any small instrument (scalpel, forceps, ...) makes the scrub nurse ...
```

See `Agent/evaluation/scenarios/` for 15 complete examples.

## Evaluation

`Agent/evaluation/` contains the harness used for the project's experiments: batch runs over
the scenarios, automatic scene metrics, Phase 2 interaction metrics, LLM-as-judge scoring,
and aggregation into tables and charts. See
[`Agent/evaluation/README.md`](Agent/evaluation/README.md) for instructions, and
`experiment_design.md` / `phase1_experiment_writeup.md` for the methodology.

## Unity-side details

For the supported command actions, their JSON fields, and how to add new spawnable
prefabs, see [`firefighter project/Assets/Scripts/readme.md`](firefighter%20project/Assets/Scripts/readme.md).

## Third-party assets

The Unity project includes third-party content: Unity's XR Interaction Toolkit samples,
the Meta XR SDK, the *Kevin Iglesias* human animations, the *ithappy* Medical Pack, and
a *Tables and Chairs* pack. Each is subject to its own license.
