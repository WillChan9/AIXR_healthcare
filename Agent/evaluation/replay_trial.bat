@echo off
REM ============================================================
REM  Replay Trial - Restore a saved Phase 1 trial and play Phase 2
REM  in Unity (XR Device Simulator). Runtime metrics are collected
REM  after you finish playing.
REM
REM  Workflow:
REM    1. Make sure Unity Editor is in Play mode.
REM    2. Edit TRIAL_DIR below to point at the trial you want to replay.
REM    3. Double-click this bat. It will:
REM       a) Send ClearScene to Unity
REM       b) Replay all saved commands to rebuild the scene
REM       c) Enter Phase 2 interactive loop
REM    4. Use WASD + mouse + G in the simulator to trigger story chain.
REM    5. Press Ctrl+C in this window when done playing.
REM    6. Phase 2 metrics auto-update in phase2_eval_report.json.
REM ============================================================

REM ---- Settings (edit here) ---------------------------------

REM Path to the trial directory (relative to Agent/ or absolute)
set TRIAL_DIR=evaluation/results/S01/trial_1

REM Phase 2 max iterations (0 = no cap; small number = auto-stop after N events)
set PHASE2_MAX_ITERATIONS=0

REM ---- End of settings --------------------------------------

cd /d "%~dp0.."

set CMD=python groq_static_test.py --replay-trial "%TRIAL_DIR%" --interval 1.0
if not "%PHASE2_MAX_ITERATIONS%"=="" if not "%PHASE2_MAX_ITERATIONS%"=="0" set CMD=%CMD% --max-iterations %PHASE2_MAX_ITERATIONS%

echo ============================================================
echo  Replaying trial:    %TRIAL_DIR%
echo  Phase 2 max iter:   %PHASE2_MAX_ITERATIONS%
echo ============================================================
echo.
echo  Make sure Unity is in Play mode!
echo  Press Ctrl+C in this window when done playing to save metrics.
echo.
echo Running: %CMD%
echo.

%CMD%

echo.
echo Done!
pause
