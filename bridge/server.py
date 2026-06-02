"""MCP server entry point for the bridge pipeline.

Exposes ``design_to_print`` and ``tune_and_reslice`` as MCP tools so any
MCP client (Claude Desktop, Cursor, custom agent) can drive the full
design-to-print workflow with a single tool call.

Start with:
    cd /Users/admin/mcp-3d-printing
    .venv/bin/python -m bridge.server

Or add to mcp_servers config:
    {
        "3d-print-pipeline": {
            "command": "/Users/admin/mcp-3d-printing/.venv/bin/python",
            "args": ["-m", "bridge.server"],
            "cwd": "/Users/admin/mcp-3d-printing"
        }
    }
"""

from __future__ import annotations

import dataclasses
from typing import Any

from mcp.server.fastmcp import FastMCP

from bridge.pipeline import (
    PipelineResult,
    TuneAndResliceResult,
    design_to_print,
    tune_and_reslice,
)
from prusa.tools.slicer import DiagnosticsResult, SliceResult


# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="3D Print Pipeline",
    instructions=(
        "End-to-end pipeline bridging Fusion 360 CAD and PrusaSlicer. "
        "Use design_to_print to export a Fusion component, auto-select the best "
        "profile for your material, slice, run diagnostics, and optionally start "
        "the print — all in one call. "
        "Use tune_and_reslice after a failed print to generate a tuned profile, "
        "re-slice, and compare the results side-by-side."
    ),
)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _slice_to_dict(s: SliceResult) -> dict[str, Any]:
    return dataclasses.asdict(s)


def _diagnostics_to_dict(d: DiagnosticsResult | None) -> dict[str, Any] | None:
    if d is None:
        return None
    # Exclude the internal _settings field — it's only needed by suggest_fixes
    return {
        "max_overhang_angle":       d.max_overhang_angle,
        "bridge_count":             d.bridge_count,
        "support_volume_mm3":       d.support_volume_mm3,
        "thin_wall_warnings":       d.thin_wall_warnings,
        "estimated_weak_layer_count": d.estimated_weak_layer_count,
    }


def _pipeline_to_dict(r: PipelineResult) -> dict[str, Any]:
    return {
        "stl_path":      r.stl_path,
        "profile_used":  r.profile_used,
        "slice_result":  _slice_to_dict(r.slice_result) if r.slice_result else None,
        "diagnostics":   _diagnostics_to_dict(r.diagnostics),
        "warnings":      r.warnings,
        "gcode_path":    r.gcode_path,
        "printer_job_id": r.printer_job_id,
    }


def _tune_reslice_to_dict(r: TuneAndResliceResult) -> dict[str, Any]:
    return {
        "stl_path":    r.stl_path,
        "old_profile": r.old_profile,
        "new_profile": r.new_profile,
        "old_slice":   _slice_to_dict(r.old_slice),
        "new_slice":   _slice_to_dict(r.new_slice),
        "changes":     r.changes,
        "explanations": r.explanations,
        "time_delta_seconds": (
            r.new_slice.print_time_seconds - r.old_slice.print_time_seconds
        ),
        "filament_delta_g": round(
            r.new_slice.filament_used_g - r.old_slice.filament_used_g, 3
        ),
    }


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def design_to_print_tool(
    component_name: str | None = None,
    material: str = "PLA",
    print_goal: str = "quality",
    start_print: bool = False,
) -> dict:
    """Export a Fusion 360 component as STL, slice with the best profile for the
    given material, run diagnostics, and optionally start the print — all in one call.

    Requires the Fusion 360 MCP add-in to be running at http://127.0.0.1:8765/mcp
    (Shift+S → Add-Ins → AutodeskFusionMCP → Run).

    Args:
        component_name: Body or component name in the active Fusion 360 design.
                        If omitted, the first solid visible body is exported.
        material:       Filament material, e.g. 'PLA', 'PETG', 'ABS', 'ASA'.
                        Profiles whose filament_type does not match receive a -50
                        penalty in the selection scoring.
        print_goal:     Slicing intent that guides print-profile selection:
                        'quality' (default), 'faster', 'stronger',
                        'better_surface_quality'.
        start_print:    If True and diagnostics are clean (no warnings), push the
                        G-code to the printer.  Requires PRINTER_HOST and
                        PRINTER_API_KEY environment variables.

    Returns a dict with:
        stl_path        – absolute path to the exported STL
        profile_used    – name of the print profile selected
        slice_result    – print_time_seconds, filament_used_mm/g, layer_count,
                          estimated_cost, output_gcode_path
        diagnostics     – max_overhang_angle, bridge_count, support_volume_mm3,
                          thin_wall_warnings, estimated_weak_layer_count
        warnings        – list of actionable warning strings; empty = clean slice
        gcode_path      – absolute path to the .gcode file
        printer_job_id  – job ID if the print was started, otherwise null
    """
    result = await design_to_print(
        component_name=component_name,
        material=material,
        print_goal=print_goal,
        start_print=start_print,
    )
    return _pipeline_to_dict(result)


@mcp.tool()
async def tune_and_reslice_tool(
    stl_path: str,
    current_profile_name: str,
    failure_description: str,
) -> dict:
    """Tune a print profile to fix a failure, save it, re-slice, and return both
    the original and tuned SliceResult for side-by-side comparison.

    The tuned profile is written to prusa/profiles/ and git-committed so the
    change is tracked.  The re-slice uses the same STL with the proposed setting
    changes applied as overrides on top of the original profile.

    Args:
        stl_path:             Path to the STL to re-slice.  Use the stl_path
                              returned by design_to_print_tool.
        current_profile_name: Print profile name used in the failing slice.
                              Must be a name from list_profiles (PrusaSlicer server).
        failure_description:  Plain-English description of the failure, e.g.
                              'lots of stringing between towers' or
                              'first layer keeps peeling at the corners'.

    Returns a dict with:
        stl_path         – the input STL path
        old_profile      – the original profile name
        new_profile      – the saved tuned profile name
        old_slice        – SliceResult with the original profile (baseline)
        new_slice        – SliceResult with the tuned settings applied
        changes          – {setting: new_value} for every changed key
        explanations     – {setting: one-sentence reason} for each change
        time_delta_seconds  – new minus old print time in seconds (negative = faster)
        filament_delta_g    – new minus old filament weight in grams
    """
    result = await tune_and_reslice(
        stl_path=stl_path,
        current_profile_name=current_profile_name,
        failure_description=failure_description,
    )
    return _tune_reslice_to_dict(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
