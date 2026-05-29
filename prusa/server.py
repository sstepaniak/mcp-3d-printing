"""MCP server for PrusaSlicer — exposes profile management and slicing tools over stdio.

Start with:  python -m prusa.server
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import Message

from prusa.tools.profile import ProfileManager, TuneResult
from prusa.tools.slicer import DiagnosticsResult, FixSuggestion, Slicer, SliceResult

# ─── server instance ──────────────────────────────────────────────────────────

mcp = FastMCP(
    name="PrusaSlicer MCP",
    instructions=(
        "Tools for slicing STL files, managing PrusaSlicer profiles, "
        "diagnosing print quality problems, and tuning slicer settings. "
        "Use list_profiles first to discover available profile names, "
        "then slice_file or tune_profile as appropriate."
    ),
)

# Initialised once at import time so errors surface immediately at startup.
_pm = ProfileManager()
_slicer = Slicer(profile_manager=_pm)


# ─── conversion helpers ───────────────────────────────────────────────────────

def _diagnostics_to_dict(dr: DiagnosticsResult) -> dict[str, Any]:
    """Flatten DiagnosticsResult to a plain dict for MCP transport.

    The internal _settings dict is exposed as 'settings' so suggest_fixes
    can reconstruct the full object and report accurate current_value fields.
    """
    return {
        "max_overhang_angle": dr.max_overhang_angle,
        "bridge_count": dr.bridge_count,
        "support_volume_mm3": dr.support_volume_mm3,
        "thin_wall_warnings": dr.thin_wall_warnings,
        "estimated_weak_layer_count": dr.estimated_weak_layer_count,
        "settings": dr._settings,
    }


def _dict_to_diagnostics(d: dict[str, Any]) -> DiagnosticsResult:
    return DiagnosticsResult(
        max_overhang_angle=float(d.get("max_overhang_angle", 0)),
        bridge_count=int(d.get("bridge_count", 0)),
        support_volume_mm3=float(d.get("support_volume_mm3", 0)),
        thin_wall_warnings=list(d.get("thin_wall_warnings", [])),
        estimated_weak_layer_count=int(d.get("estimated_weak_layer_count", 0)),
        _settings=dict(d.get("settings", {})),
    )


# ─── profile tools ────────────────────────────────────────────────────────────

@mcp.tool()
def list_profiles() -> list[dict]:
    """List every PrusaSlicer profile available on this machine.

    Scans the project config bundle (PrusaSlicer_config_bundle.ini) and the
    local ~/Library/Application Support/PrusaSlicer/ directories.

    Returns a list of dicts, each with:
      name         – the exact profile name as used in PrusaSlicer
      category     – one of 'print', 'filament', or 'printer'
      source_path  – absolute path to the source .ini file or bundle

    Use the returned name values as arguments to read_profile, compare_profiles,
    explain_profile, tune_profile, and slice_file.
    """
    return _pm.list_profiles()


@mcp.tool()
def read_profile(name: str) -> dict:
    """Read the full key-value settings for a named PrusaSlicer profile.

    Args:
        name: Exact profile name from list_profiles(), e.g.
              '0.20mm QUALITY @MK4 0.4 - Copy'

    Returns a dict mapping every setting key to its string value, e.g.
      {'layer_height': '0.2', 'fill_density': '15%', 'perimeters': '2', ...}

    Raises KeyError if the profile cannot be found.
    """
    return _pm.read_profile(name)


@mcp.tool()
def write_profile(name: str, settings: dict) -> str:
    """Save a settings dict as a .ini file under prusa/profiles/ and git-commit it.

    Args:
        name:     Profile name used for the filename and commit message.
        settings: Dict of setting key → value strings, e.g. the output of
                  read_profile() optionally modified or the proposed_changes
                  from tune_profile().

    Returns the absolute path of the written .ini file.

    Note: this commits to the prusa/profiles/ submodule.  The content is not
    pushed to any remote — the caller decides when to push.
    """
    path = _pm.write_profile(name, {str(k): str(v) for k, v in settings.items()})
    return str(path)


@mcp.tool()
def compare_profiles(name_a: str, name_b: str) -> dict:
    """Show only the settings that differ between two profiles, with both values.

    Args:
        name_a: First profile name (from list_profiles).
        name_b: Second profile name (from list_profiles).

    Returns a dict keyed by setting name, where each value is a dict:
      { '<name_a>': '<value_in_a>', '<name_b>': '<value_in_b>' }

    Only keys whose values differ are included.  Use this to understand
    what changed between a base profile and a variant, or to choose between
    two profiles for a specific print goal.
    """
    return _pm.compare_profiles(name_a, name_b)


@mcp.tool()
def explain_profile(name: str) -> str:
    """Return a plain-English grouped summary of a profile's settings.

    Args:
        name: Profile name (from list_profiles).

    Returns a human-readable string organised into sections:
      QUALITY   – layer height, ironing, gap fill
      SPEED     – perimeter, infill, travel speeds
      STRENGTH  – infill density, perimeter count, solid layers
      THERMAL   – temperatures, fan settings  (mainly filament profiles)
      SUPPORT   – support material settings
      SPECIAL   – brim, fuzzy skin, elephant foot compensation

    Unusual or aggressive settings are flagged with '!' and a brief reason.
    Use this to quickly understand what a profile is optimised for before
    deciding whether to use or tune it.
    """
    return _pm.explain_profile(name)


@mcp.tool()
def tune_profile(
    base_profile_name: str,
    goal_description: str,
    failure_description: str | None = None,
) -> dict:
    """Generate a tuned variant of an existing profile targeting a specific goal.

    Parses the goal and failure descriptions for recognised keywords and applies
    a built-in rules table to produce concrete setting changes.  Nothing is
    written to disk — call write_profile() with new_profile_ini_content if you
    want to save the result.

    Recognised goals in goal_description / failure_description:
      faster · stronger · better surface quality
      fix stringing · fix warping · fix layer separation
      fix elephant foot · fix under-extrusion · fix over-extrusion
      fix bridging · fix support adhesion

    Args:
        base_profile_name: Name of the profile to tune (from list_profiles).
                           Must be a print profile for most goals.
        goal_description:  Free-text description, e.g. 'faster stronger'.
        failure_description: Optional description of a current failure to fix
                             on top of the goal changes, e.g. 'fix stringing'.

    Returns a dict with:
      proposed_changes       – {setting: new_value} for every changed key
      explanations           – {setting: one-sentence reason}
      new_profile_ini_content – complete .ini text ready for write_profile()
      suggested_profile_name  – auto-generated name, e.g. '0.20mm QUALITY - faster'
    """
    result = _pm.tune_profile(base_profile_name, goal_description, failure_description)
    return dataclasses.asdict(result)


# ─── slicer tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def slice_file(
    stl_path: str,
    print_profile: str,
    filament_profile: str | None = None,
    printer_profile: str | None = None,
    overrides: dict | None = None,
) -> dict:
    """Slice an STL file with PrusaSlicer and return print statistics.

    Merges printer → print → filament profiles (later wins on conflicts) then
    runs the CLI.  Filament and printer profiles are auto-detected from
    PrusaSlicer.ini when not specified; if those vendor profiles aren't in the
    local bundle, slicing still succeeds with estimated filament weight.

    Args:
        stl_path:        Absolute or relative path to the .stl file.
        print_profile:   Print profile name from list_profiles(), e.g.
                         '0.20mm QUALITY @MK4 0.4 - Copy'.
        filament_profile: Filament profile name, e.g.
                          'Prusament PLA @PG - Benchy 29min'.
                          Optional — auto-detected from PrusaSlicer.ini if omitted.
        printer_profile:  Printer profile name, e.g. 'Benchy 29min settings - PLA'.
                          Optional — auto-detected from PrusaSlicer.ini if omitted.
        overrides:        Optional dict of setting overrides applied on top of all
                          profiles, e.g. {'layer_height': '0.15', 'fill_density': '20%'}.

    Returns a dict with:
      print_time_seconds  – estimated print time as integer seconds
      filament_used_mm    – filament length in millimetres
      filament_used_g     – filament weight in grams (0 if density unknown)
      estimated_cost      – cost in currency units from filament profile
      layer_count         – number of sliced layers
      output_gcode_path   – absolute path to the written .gcode file
    """
    result = _slicer.slice_file(
        stl_path=stl_path,
        print_profile=print_profile,
        filament_profile=filament_profile,
        printer_profile=printer_profile,
        overrides_dict=overrides,
    )
    return dataclasses.asdict(result)


@mcp.tool()
def parse_diagnostics(gcode_path: str) -> dict:
    """Analyse a sliced G-code file and return structural diagnostic data.

    Reads the G-code comment annotations written by PrusaSlicer to extract
    structural information about the slice.  Run this after slice_file to
    check the geometry before printing.

    Args:
        gcode_path: Absolute path to a .gcode file produced by slice_file.

    Returns a dict with:
      max_overhang_angle       – overhang threshold from profile config (degrees)
      bridge_count             – number of ;TYPE:Bridge infill sections found
      support_volume_mm3       – estimated support material volume (mm³)
      thin_wall_warnings       – list of 'Thin wall segment in layer N' strings
      estimated_weak_layer_count – number of layers containing overhang perimeters
      settings                 – embedded slicer config from the gcode (for suggest_fixes)

    Pass the returned dict directly to suggest_fixes to get actionable fix recommendations.
    """
    dr = _slicer.parse_diagnostics(gcode_path)
    return _diagnostics_to_dict(dr)


@mcp.tool()
def suggest_fixes(diagnostics: dict) -> list[dict]:
    """Map diagnostic warnings to specific setting changes with reasons.

    Accepts the dict returned by parse_diagnostics and applies a rule set to
    produce actionable recommendations for each detected problem.

    Args:
        diagnostics: The dict returned by parse_diagnostics.  Must contain at
                     minimum the keys: max_overhang_angle, bridge_count,
                     support_volume_mm3, thin_wall_warnings,
                     estimated_weak_layer_count.

    Returns a list of dicts, each with:
      setting            – the PrusaSlicer setting key to change
      current_value      – current value (from embedded gcode config, or 'unknown')
      recommended_value  – the suggested new value
      reason             – one sentence explaining why this change helps

    Apply the recommended changes via tune_profile (using the setting names as
    overrides) or directly via write_profile.
    """
    dr = _dict_to_diagnostics(diagnostics)
    fixes = _slicer.suggest_fixes(dr)
    return [dataclasses.asdict(f) for f in fixes]


# ─── resource ─────────────────────────────────────────────────────────────────

@mcp.resource("prusaslicer://profiles")
def profiles_resource() -> str:
    """Current list of all available PrusaSlicer profiles as JSON.

    Returns a JSON array of profile objects, each with name, category, and
    source_path.  Refreshed on every read — reflects any profiles added to
    the config bundle or AppSupport directories since the server started.
    """
    return json.dumps(_pm.list_profiles(), indent=2)


# ─── prompt templates ─────────────────────────────────────────────────────────

@mcp.prompt()
def slice_and_report(stl_path: str, material: str) -> list[Message]:
    """Prompt template: slice a file and produce a human-readable print report.

    Args:
        stl_path: Path to the STL file to slice.
        material: Material name hint, e.g. 'PLA', 'PETG', 'ASA'.
    """
    return [
        Message(
            role="user",
            content=(
                f"Please slice the file '{stl_path}' for printing in {material}.\n\n"
                "Steps:\n"
                "1. Call list_profiles to find a suitable print profile and a matching "
                f"{material} filament profile.\n"
                "2. Call slice_file with those profiles.\n"
                "3. Call parse_diagnostics on the resulting gcode.\n"
                "4. Report back:\n"
                "   - Estimated print time (hours and minutes)\n"
                "   - Filament used (mm and grams)\n"
                "   - Estimated cost\n"
                "   - Layer count\n"
                "   - Any diagnostic warnings or suggested fixes\n"
                "   - Whether the settings look appropriate for the material."
            ),
        )
    ]


@mcp.prompt()
def tune_from_failure(failure_description: str) -> list[Message]:
    """Prompt template: diagnose a print failure and return a tuned profile.

    Args:
        failure_description: Plain-English description of what went wrong,
                             e.g. 'lots of stringing between towers' or
                             'first layer keeps peeling up at the corners'.
    """
    return [
        Message(
            role="user",
            content=(
                f"I have a print failure: {failure_description}\n\n"
                "Please help me fix it:\n"
                "1. Call list_profiles to show available print profiles.\n"
                "2. Ask me which base profile I've been using (or pick the most "
                "relevant one if obvious from the failure description).\n"
                "3. Call tune_profile with the base profile and the failure description.\n"
                "4. Show me:\n"
                "   - Each proposed setting change and the one-sentence reason\n"
                "   - The suggested new profile name\n"
                "   - Whether any changes are in the filament/printer profile "
                "     rather than the print profile (so I know where to apply them)\n"
                "5. Ask if I want to save the tuned profile."
            ),
        )
    ]


@mcp.prompt()
def compare_and_choose(profile_a: str, profile_b: str, print_goal: str) -> list[Message]:
    """Prompt template: compare two profiles and recommend one for a specific goal.

    Args:
        profile_a:   First profile name.
        profile_b:   Second profile name.
        print_goal:  What the print needs to achieve, e.g. 'dimensional accuracy
                     for mechanical parts' or 'fastest possible decorative piece'.
    """
    return [
        Message(
            role="user",
            content=(
                f"I need to choose between two profiles for this goal: {print_goal}\n\n"
                f"Profile A: {profile_a}\n"
                f"Profile B: {profile_b}\n\n"
                "Please:\n"
                f"1. Call explain_profile for both '{profile_a}' and '{profile_b}'.\n"
                f"2. Call compare_profiles('{profile_a}', '{profile_b}') to see the diffs.\n"
                "3. Based on the goal, recommend one profile and explain:\n"
                "   - Which key settings make it better suited\n"
                "   - Any settings in the weaker profile that could be borrowed\n"
                "   - Whether tune_profile could close the gap"
            ),
        )
    ]


# ─── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
