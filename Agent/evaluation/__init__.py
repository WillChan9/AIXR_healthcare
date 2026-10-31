"""Evaluation harness for the VR healthcare scene-generation pipeline.

Modules:
    run_experiment     run scenarios x trials through the pipeline and evaluate them
    evaluate_scene     automated Phase 1 (static scene) metrics
    evaluate_phase2    automated Phase 2 (interaction map + story chain) metrics
    llm_judge          LLM-as-judge scoring for Phase 1 and Phase 2
    aggregate_results  cross-trial aggregation, summary tables, CSV and charts

See README.md in this directory for usage.
"""
