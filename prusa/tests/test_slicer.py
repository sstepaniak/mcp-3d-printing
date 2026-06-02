"""Tests for prusa/tools/slicer.py — SliceResult, DiagnosticsResult, suggest_fixes.

All CLI subprocess calls are mocked; no real PrusaSlicer binary is needed.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prusa.tools.slicer import (
    DiagnosticsResult,
    FixSuggestion,
    Slicer,
    SliceResult,
    _parse_time,
    _parse_gcode_comments,
    _count_type_sections,
    _layers_containing_type,
    _estimate_support_volume_mm3,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slicer() -> tuple[Slicer, MagicMock, MagicMock]:
    """Return (Slicer, mock_cli, mock_pm)."""
    mock_cli = MagicMock()
    mock_pm = MagicMock()
    mock_pm.read_profile.return_value = {
        "layer_height": "0.20",
        "fill_density": "15%",
        "retract_length": "0.8",
        "retract_speed": "40",
    }
    slicer = Slicer(cli=mock_cli, profile_manager=mock_pm)
    return slicer, mock_cli, mock_pm


_MINIMAL_GCODE = textwrap.dedent("""\
    ; layer_height = 0.20
    ; fill_density = 15%
    ; support_material_threshold = 0
    ; filament used [mm] = 1500.0
    ; filament used [g] = 5.5
    ; total filament cost = 0.11
    ; estimated printing time (normal mode) = 1h 23m 45s
    ;LAYER_CHANGE
    G1 X10 Y10 E1.0
    ;LAYER_CHANGE
    G1 X20 Y20 E2.0
""")

_BRIDGE_GCODE = textwrap.dedent("""\
    ; support_material_threshold = 0
    ; filament used [mm] = 2000.0
    ; filament used [g] = 7.2
    ; total filament cost = 0.15
    ; estimated printing time (normal mode) = 2h 0m 0s
    ;LAYER_CHANGE
    ;TYPE:Bridge infill
    G1 X10 Y10 E1.0
    ;TYPE:Bridge infill
    G1 X20 Y20 E2.0
    ;TYPE:Bridge infill
    G1 X30 Y30 E3.0
    ;TYPE:Bridge infill
    G1 X40 Y40 E4.0
    ;LAYER_CHANGE
""")


def _write_gcode(tmp_path: Path, content: str, name: str = "test.gcode") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# SliceResult fields
# ---------------------------------------------------------------------------

class TestSliceResult:
    def test_all_fields_present(self, tmp_path):
        gcode = _write_gcode(tmp_path, _MINIMAL_GCODE)
        slicer, mock_cli, mock_pm = _make_slicer()
        mock_pm.read_profile.return_value = {}
        mock_cli.run.return_value = ""

        with patch("prusa.tools.slicer._prusa_ini_defaults", return_value={}), \
             patch("tempfile.NamedTemporaryFile"), \
             patch("pathlib.Path.unlink"):
            # _parse_slice_result directly
            result = slicer._parse_slice_result(gcode)

        assert isinstance(result, SliceResult)
        assert result.print_time_seconds == 5025          # 1h 23m 45s
        assert result.filament_used_mm == 1500.0
        assert result.filament_used_g == pytest.approx(5.5)
        assert result.estimated_cost == pytest.approx(0.11)
        assert result.layer_count == 2
        assert result.output_gcode_path == str(gcode)

    def test_layer_count_zero_for_empty_gcode(self, tmp_path):
        gcode = _write_gcode(tmp_path, "; nothing here\n")
        slicer, _, _ = _make_slicer()
        result = slicer._parse_slice_result(gcode)
        assert result.layer_count == 0


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------

class TestParseTime:
    def test_hours_minutes_seconds(self):
        assert _parse_time("1h 23m 45s") == 5025

    def test_minutes_only(self):
        assert _parse_time("15m") == 900

    def test_seconds_only(self):
        assert _parse_time("90s") == 90

    def test_zero(self):
        assert _parse_time("0s") == 0

    def test_hours_and_minutes(self):
        assert _parse_time("2h 0m") == 7200


# ---------------------------------------------------------------------------
# DiagnosticsResult parsing
# ---------------------------------------------------------------------------

class TestDiagnosticsResult:
    def test_bridge_count_parsed(self, tmp_path):
        gcode = _write_gcode(tmp_path, _BRIDGE_GCODE)
        slicer, _, _ = _make_slicer()
        result = slicer.parse_diagnostics(gcode)
        assert isinstance(result, DiagnosticsResult)
        assert result.bridge_count == 4

    def test_no_bridges_in_minimal_gcode(self, tmp_path):
        gcode = _write_gcode(tmp_path, _MINIMAL_GCODE)
        slicer, _, _ = _make_slicer()
        result = slicer.parse_diagnostics(gcode)
        assert result.bridge_count == 0

    def test_thin_wall_warnings_populated(self, tmp_path):
        gcode_text = _MINIMAL_GCODE + textwrap.dedent("""\
            ;LAYER_CHANGE
            ;TYPE:Thin wall
            G1 X5 Y5 E0.5
        """)
        gcode = _write_gcode(tmp_path, gcode_text)
        slicer, _, _ = _make_slicer()
        result = slicer.parse_diagnostics(gcode)
        assert len(result.thin_wall_warnings) > 0
        assert any("Thin wall" in w for w in result.thin_wall_warnings)

    def test_overhang_angle_from_settings(self, tmp_path):
        # Use a gcode with only the threshold comment (no conflicting override)
        gcode_text = textwrap.dedent("""\
            ; support_material_threshold = 55
            ; filament used [mm] = 1500.0
            ; filament used [g] = 5.5
            ; total filament cost = 0.11
            ; estimated printing time (normal mode) = 1h 0m 0s
        """)
        gcode = _write_gcode(tmp_path, gcode_text)
        slicer, _, _ = _make_slicer()
        result = slicer.parse_diagnostics(gcode)
        assert result.max_overhang_angle == 55.0

    def test_settings_dict_populated(self, tmp_path):
        gcode = _write_gcode(tmp_path, _MINIMAL_GCODE)
        slicer, _, _ = _make_slicer()
        result = slicer.parse_diagnostics(gcode)
        assert "layer_height" in result._settings
        assert result._settings["layer_height"] == "0.20"


# ---------------------------------------------------------------------------
# suggest_fixes — known failure modes map to settings changes
# ---------------------------------------------------------------------------

class TestSuggestFixes:
    def _diag(self, **kwargs) -> DiagnosticsResult:
        defaults = dict(
            max_overhang_angle=0.0,
            bridge_count=0,
            support_volume_mm3=0.0,
            thin_wall_warnings=[],
            estimated_weak_layer_count=0,
        )
        defaults.update(kwargs)
        return DiagnosticsResult(**defaults)

    def test_many_bridges_suggests_bridge_flow_ratio(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag(bridge_count=5)
        fixes = slicer.suggest_fixes(diag)
        settings = {f.setting for f in fixes}
        assert "bridge_flow_ratio" in settings

    def test_many_bridges_suggests_bridge_speed(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag(bridge_count=5)
        fixes = slicer.suggest_fixes(diag)
        settings = {f.setting for f in fixes}
        assert "bridge_speed" in settings

    def test_steep_overhang_suggests_supports(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag(max_overhang_angle=60.0)
        fixes = slicer.suggest_fixes(diag)
        settings = {f.setting for f in fixes}
        assert "support_material" in settings

    def test_thin_walls_suggests_thin_walls_setting(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag(thin_wall_warnings=["Thin wall in layer 3"])
        fixes = slicer.suggest_fixes(diag)
        settings = {f.setting for f in fixes}
        assert "thin_walls" in settings

    def test_high_weak_layer_count_suggests_perimeter_speed(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag(estimated_weak_layer_count=10)
        fixes = slicer.suggest_fixes(diag)
        settings = {f.setting for f in fixes}
        assert "external_perimeter_speed" in settings

    def test_large_support_volume_suggests_snug_style(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag(support_volume_mm3=600.0)
        fixes = slicer.suggest_fixes(diag)
        settings = {f.setting for f in fixes}
        assert "support_material_style" in settings

    def test_clean_diagnostics_return_no_fixes(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag()
        fixes = slicer.suggest_fixes(diag)
        assert fixes == []

    def test_fix_suggestion_has_all_fields(self):
        slicer, _, _ = _make_slicer()
        diag = self._diag(bridge_count=5)
        fixes = slicer.suggest_fixes(diag)
        for fix in fixes:
            assert isinstance(fix, FixSuggestion)
            assert fix.setting
            assert fix.recommended_value
            assert fix.reason
