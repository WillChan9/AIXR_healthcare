@echo off
REM ============================================================
REM  Experiment Runner - Phase 1 + Phase 2 evaluation (Windows)
REM  Wraps evaluation\run_experiment.py. Edit the settings below,
REM  then double-click to run. Uses whichever "python" is on PATH.
REM ============================================================

REM ---- Settings (edit here) ---------------------------------

REM Scenarios to run (comma-separated, e.g. S01,S03). Leave empty for all (S01-S15).
set SCENARIOS=S02, S04, S05, S06, S07, S08, S09, S10

REM Number of trials per scenario
set TRIALS=3

REM Results folder (relative to Agent/ or absolute path)
set RESULTS_DIR=evaluation/results

REM Skip LLM judge? (set to YES to skip)
set SKIP_JUDGE=NO

REM LLM judge provider: groq or openai
REM   groq    - cheap, uses Llama-4-Scout (less accurate evaluator)
REM   openai  - frontier-quality judge, uses JUDGE_MODEL below
set JUDGE_PROVIDER=openai

REM LLM judge model (only used when JUDGE_PROVIDER=openai)
REM   gpt-5.5     - newest frontier (recommended for high-quality judging)
REM   gpt-5.2     - stable mid-tier
REM   gpt-5.4-mini - cheap and fast
REM   gpt-4o      - legacy fallback
set JUDGE_MODEL=gpt-5.4-mini

REM Timeout per trial in seconds.
REM   - gpt-5.5 / gpt-5.5-pro are reasoning models: single calls can take
REM     1-3 minutes. With 4 frontier calls per trial, expect 7-10 min total,
REM     so use 900 (15 min) for safety.
REM   - gpt-5.4-mini / gpt-5.2 are much faster: 300-500 is enough.
set TIMEOUT=300

REM Frontier LLM for scene synthesis + interaction map + story chain
REM   gpt-5.5        - newest (2026-04-23), best quality
REM   gpt-5.5-pro    - top-tier reasoning, more expensive
REM   gpt-5.2        - stable mid-tier (2025-12-11)
REM   gpt-5.4-mini   - cheap and fast
REM   gpt-4o         - legacy fallback
REM   (empty)        - omit --frontier-model, so groq_static_test.py uses
REM                    its own default (currently gpt-5.5)
set FRONTIER_MODEL=gpt-5.4-mini

REM Run Phase 2 (interactive event loop)? YES requires you to interact with
REM the scene in Unity (touch / grab via simulator or VR). NO = batch-only:
REM Phase 1 + static Phase 2 evaluation.
REM Tip: use INCLUDE_PHASE2=YES with TRIALS=1 and a single SCENARIOS entry,
REM otherwise batch trials hang in Phase 2 waiting for player input.
set INCLUDE_PHASE2=NO

REM Phase 2 event-loop iteration cap (only applies when INCLUDE_PHASE2=YES).
REM   0      - no cap; only TIMEOUT stops the loop
REM   50-100 - the loop stops itself after that many event batches
REM            (useful for unattended Phase 2 runs)
set PHASE2_MAX_ITERATIONS=0

REM ---- End of settings --------------------------------------

REM Run from Agent\ (the parent of this script's folder).
cd /d "%~dp0.."

set CMD=python evaluation/run_experiment.py

REM Quote SCENARIOS so spaces after commas don't break argparse (e.g. "S02, S03").
if not "%SCENARIOS%"=="" set CMD=%CMD% --scenarios "%SCENARIOS%"
if not "%TRIALS%"=="" set CMD=%CMD% --trials %TRIALS%
if not "%RESULTS_DIR%"=="" set CMD=%CMD% --results-dir "%RESULTS_DIR%"
if /i "%SKIP_JUDGE%"=="YES" set CMD=%CMD% --skip-judge
if not "%JUDGE_PROVIDER%"=="" set CMD=%CMD% --judge-provider %JUDGE_PROVIDER%
if not "%JUDGE_MODEL%"=="" set CMD=%CMD% --judge-model %JUDGE_MODEL%
if not "%TIMEOUT%"=="" set CMD=%CMD% --timeout %TIMEOUT%
if not "%FRONTIER_MODEL%"=="" set CMD=%CMD% --frontier-model %FRONTIER_MODEL%
if /i "%INCLUDE_PHASE2%"=="YES" set CMD=%CMD% --include-phase2
if not "%PHASE2_MAX_ITERATIONS%"=="" if not "%PHASE2_MAX_ITERATIONS%"=="0" set CMD=%CMD% --phase2-max-iterations %PHASE2_MAX_ITERATIONS%

echo ============================================================
echo  Scenarios:        %SCENARIOS%
echo  Trials:           %TRIALS%
echo  Results dir:      %RESULTS_DIR%
echo  Skip judge:       %SKIP_JUDGE%
echo  Judge provider:   %JUDGE_PROVIDER%
echo  Judge model:      %JUDGE_MODEL%
echo  Timeout:          %TIMEOUT%s
echo  Frontier model:   %FRONTIER_MODEL%
echo  Include Phase 2:  %INCLUDE_PHASE2%
echo  Phase 2 max iter: %PHASE2_MAX_ITERATIONS%
echo ============================================================
echo.
echo Running: %CMD%
echo.

%CMD%

echo.
echo Done!
pause
