"""Integration tests for bridge/pipeline.py.

All external I/O is mocked — no Fusion 360, PrusaSlicer binary, or printer
is required.  Tests exercise the full async pipeline logic including:
  - Fusion MCP HTTP interaction
  - Profile selection scoring
  - Slice/diagnostic result wiring
  - print-start gating on warnings
  - Error propagation on connectivity failures

Run with:
    python -m pytest bridge/tests/test_pipeline.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import httpx
import pytest

from bridge.pipeline import (
    PipelineResult,
    TuneAndResliceResult,
    design_to_print,
    tune_and_reslice,
)
from prusa.tools.printer import PrinterResult
from prusa.tools.profile import TuneResult
from prusa.tools.slicer import DiagnosticsResult, FixSuggestion, SliceResult


# ---------------------------------------------------------------------------
# Shared test fixtures / helpers
# ---------------------------------------------------------------------------

STL_PATH = "/tmp/pipeline_test.stl"
GCODE_PATH = "/tmp/pipeline_test.gcode"
PRINT_PROFILE = "0.20mm QUALITY @MK4 0.4"
FILAMENT_PROFILE = "Prusament PLA @PG - Benchy 29min"
BASE_PROFILE = "0.20mm QUALITY @MK4 0.4"


def _fusion_ok_response(stl_path: str = STL_PATH) -> dict:
    """Simulate a successful Fusion export_stl MCP tool result."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"file_path": stl_path, "file_size_bytes": 2048}),
            }
        ],
        "isError": False,
    }


def _slice_result(
    gcode: str = GCODE_PATH,
    seconds: int = 3_600,
    grams: float = 6.3,
) -> SliceResult:
    return SliceResult(
        print_time_seconds=seconds,
        filament_used_mm=2_100.0,
        filament_used_g=grams,
        estimated_cost=0.13,
        layer_count=200,
        output_gcode_path=gcode,
    )


def _clean_diagnostics() -> DiagnosticsResult:
    return DiagnosticsResult(
        max_overhang_angle=0.0,
        bridge_count=0,
        support_volume_mm3=0.0,
        thin_wall_warnings=[],
        estimated_weak_layer_count=0,
    )


def _printer_result(job_id: str = "pipeline_test.gcode") -> PrinterResult:
    return PrinterResult(
        printer_type="octoprint",
        upload_url=f"http://octopi.local/api/files/local/{job_id}",
        job_id=job_id,
        filename=job_id,
        started=True,
    )


def _setup_slicer_mock(mocker, *, slice_ret=None, diag_ret=None, fixes_ret=None):
    """Patch bridge.pipeline.Slicer and return the mock instance."""
    if slice_ret is None:
        slice_ret = _slice_result()
    if diag_ret is None:
        diag_ret = _clean_diagnostics()
    if fixes_ret is None:
        fixes_ret = []

    mock_cls = mocker.patch("bridge.pipeline.Slicer")
    mock_inst = mock_cls.return_value
    mock_inst.slice_file.return_value = slice_ret
    mock_inst.parse_diagnostics.return_value = diag_ret
    mock_inst.suggest_fixes.return_value = fixes_ret
    return mock_inst


def _setup_fusion_mock(mocker, stl_path: str = STL_PATH):
    """Patch _call_fusion_tool as an async function returning a valid STL response."""
    resp = _fusion_ok_response(stl_path)

    async def _fake(tool_name, arguments, timeout=60.0):
        return resp

    mocker.patch("bridge.pipeline._call_fusion_tool", side_effect=_fake)


# ---------------------------------------------------------------------------
# test_design_to_print_happy_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_design_to_print_happy_path(mocker):
    """PipelineResult is fully populated and the printer is NOT contacted."""
    # Arrange
    _setup_fusion_mock(mocker)
    mocker.patch(
        "bridge.pipeline._select_profiles",
        return_value=(PRINT_PROFILE, FILAMENT_PROFILE),
    )
    sr = _slice_result()
    mock_slicer = _setup_slicer_mock(mocker, slice_ret=sr, fixes_ret=[])
    mocker.patch("bridge.pipeline.ProfileManager")
    mock_push = mocker.patch("bridge.pipeline.push_to_printer")

    # Act
    result = await design_to_print(
        component_name="Bracket",
        material="PLA",
        print_goal="quality",
        start_print=False,
    )

    # Assert — result fields
    assert isinstance(result, PipelineResult)
    assert result.stl_path == STL_PATH
    assert result.profile_used == PRINT_PROFILE
    assert result.slice_result is sr
    assert result.gcode_path == GCODE_PATH
    assert result.warnings == []
    assert result.printer_job_id is None

    # Assert — slicer was called with the STL and correct profile
    mock_slicer.slice_file.assert_called_once()
    call_args = mock_slicer.slice_file.call_args
    assert call_args.args[0] == STL_PATH        # stl_path positional
    assert call_args.args[1] == PRINT_PROFILE   # print_profile positional

    # Assert — printer was never touched
    mock_push.assert_not_called()


# ---------------------------------------------------------------------------
# test_design_to_print_no_warnings_starts_print
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_design_to_print_no_warnings_starts_print(mocker):
    """When start_print=True and diagnostics are clean, push_to_printer is called."""
    # Arrange
    _setup_fusion_mock(mocker)
    mocker.patch(
        "bridge.pipeline._select_profiles",
        return_value=(PRINT_PROFILE, FILAMENT_PROFILE),
    )
    sr = _slice_result()
    _setup_slicer_mock(mocker, slice_ret=sr, fixes_ret=[])
    mocker.patch("bridge.pipeline.ProfileManager")

    expected_job_id = "pipeline_test.gcode"
    mock_push = mocker.patch(
        "bridge.pipeline.push_to_printer",
        return_value=_printer_result(expected_job_id),
    )

    # Act
    result = await design_to_print(
        component_name=None,
        material="PLA",
        print_goal="quality",
        start_print=True,      # <── start requested
    )

    # Assert — printer was called
    mock_push.assert_called_once_with(GCODE_PATH, True)
    assert result.printer_job_id == expected_job_id
    assert result.warnings == []


# ---------------------------------------------------------------------------
# test_design_to_print_warnings_blocks_print
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_design_to_print_warnings_blocks_print(mocker):
    """When warnings are present, push_to_printer must NOT be called even with start_print=True."""
    # Arrange
    _setup_fusion_mock(mocker)
    mocker.patch(
        "bridge.pipeline._select_profiles",
        return_value=(PRINT_PROFILE, FILAMENT_PROFILE),
    )
    sr = _slice_result()
    warning_fix = FixSuggestion(
        setting="support_material",
        current_value="0",
        recommended_value="1",
        reason="Overhangs exceed 50° — supports required.",
    )
    # Diagnostics with thin-wall annotation + a fix suggestion
    diag = DiagnosticsResult(
        max_overhang_angle=55.0,
        bridge_count=0,
        support_volume_mm3=0.0,
        thin_wall_warnings=["Thin wall segment present in layer 7"],
        estimated_weak_layer_count=0,
    )
    _setup_slicer_mock(mocker, slice_ret=sr, diag_ret=diag, fixes_ret=[warning_fix])
    mocker.patch("bridge.pipeline.ProfileManager")
    mock_push = mocker.patch("bridge.pipeline.push_to_printer")

    # Act
    result = await design_to_print(
        component_name=None,
        material="PLA",
        print_goal="quality",
        start_print=True,      # <── start requested, but warnings should block it
    )

    # Assert — printer never contacted despite start_print=True
    mock_push.assert_not_called()
    assert result.printer_job_id is None

    # Assert — warnings list is non-empty and contains both sources
    assert len(result.warnings) >= 2
    assert any("Thin wall" in w for w in result.warnings)
    assert any("support_material" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# test_tune_and_reslice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tune_and_reslice(mocker):
    """tune_and_reslice returns both SliceResults, saves the tuned profile, and
    reports non-empty proposed_changes."""
    # Arrange
    old_sr = _slice_result(seconds=3_600, grams=6.3)
    new_sr = _slice_result(seconds=3_200, grams=5.9)    # faster after tune

    mock_slicer_cls = mocker.patch("bridge.pipeline.Slicer")
    mock_slicer = mock_slicer_cls.return_value
    # First call → old slice; second call → new slice with overrides
    mock_slicer.slice_file.side_effect = [old_sr, new_sr]

    tune_result = TuneResult(
        proposed_changes={
            "retract_length": "1.3",
            "retract_speed":  "50",
            "travel_speed":   "180",
        },
        explanations={
            "retract_length": "Longer retraction pulls melt back, reducing ooze.",
            "retract_speed":  "Faster retraction shortens ooze window.",
            "travel_speed":   "Faster travel limits ooze distance.",
        },
        new_profile_ini_content=(
            "retract_length = 1.3\nretract_speed = 50\ntravel_speed = 180\n"
        ),
        suggested_profile_name=f"{BASE_PROFILE} - fix stringing",
    )

    mock_pm_cls = mocker.patch("bridge.pipeline.ProfileManager")
    mock_pm = mock_pm_cls.return_value
    mock_pm.tune_profile.return_value = tune_result
    mock_pm.read_profile.return_value = {
        "layer_height": "0.2",
        "fill_density": "15%",
        "retract_length": "0.8",
        "retract_speed": "40",
    }
    mock_pm.write_profile.return_value = Path(
        f"/tmp/{BASE_PROFILE} - fix stringing.ini"
    )

    # Act
    result = await tune_and_reslice(
        stl_path=STL_PATH,
        current_profile_name=BASE_PROFILE,
        failure_description="lots of stringing between towers",
    )

    # Assert — both SliceResults are present
    assert isinstance(result, TuneAndResliceResult)
    assert result.old_slice is old_sr
    assert result.new_slice is new_sr

    # Assert — profile names
    assert result.old_profile == BASE_PROFILE
    assert result.new_profile == f"{BASE_PROFILE} - fix stringing"

    # Assert — changes dict is non-empty and contains expected keys
    assert len(result.changes) > 0
    assert "retract_length" in result.changes
    assert result.changes["retract_length"] == "1.3"

    # Assert — explanations match changes
    assert set(result.explanations.keys()) == set(result.changes.keys())

    # Assert — write_profile was called to persist the tuned profile
    mock_pm.write_profile.assert_called_once()
    write_call = mock_pm.write_profile.call_args
    saved_name = write_call.args[0]
    saved_settings = write_call.args[1]
    assert saved_name == f"{BASE_PROFILE} - fix stringing"
    # Merged settings contain both base keys and proposed changes
    assert "layer_height" in saved_settings
    assert saved_settings["retract_length"] == "1.3"

    # Assert — second slice used same base profile + overrides (not new profile name)
    second_slice_call = mock_slicer.slice_file.call_args_list[1]
    assert second_slice_call.args[1] == BASE_PROFILE           # base profile unchanged
    assert second_slice_call.args[4] == tune_result.proposed_changes  # overrides applied


# ---------------------------------------------------------------------------
# test_fusion_unreachable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fusion_unreachable(mocker):
    """A ConnectError with a human-readable Fusion-specific message is raised
    when the add-in HTTP endpoint cannot be reached."""
    # Arrange — the real _call_fusion_tool wraps httpx.ConnectError with
    # a Fusion-specific message.  Simulate the inner client failing.
    async def _failing_post(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    # Patch the AsyncClient context-manager so .post raises ConnectError
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    mocker.patch("httpx.AsyncClient", return_value=mock_client)

    # ProfileManager / Slicer never reached — mock to avoid file-system access
    mocker.patch("bridge.pipeline.ProfileManager")
    mocker.patch("bridge.pipeline.Slicer")

    # Act + Assert
    with pytest.raises(httpx.ConnectError) as exc_info:
        await design_to_print(component_name="Part", material="PLA")

    msg = str(exc_info.value)
    assert "Fusion" in msg or "8765" in msg or "add-in" in msg, (
        f"Error message should mention Fusion/port/add-in, got: {msg!r}"
    )
