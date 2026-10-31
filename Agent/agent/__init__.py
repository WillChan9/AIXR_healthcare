"""Agent package: helper modules for the LLM scene-generation pipeline.

Contents:
    Reflection_module   ReflectionModule -- Groq-based multi-threaded reflection
                        loop that critiques and corrects the scene layout during
                        Phase 1 scene generation.
    animation_planner   AnimationPlannerThread -- background LLM thread that picks
                        contextual animations for player interactions in Phase 2.
    logger              get_logger() -- shared logging setup (console + combined
                        api_files/agent.log, plus optional per-module log files).

The pipeline entry point is Agent/groq_static_test.py.
"""
