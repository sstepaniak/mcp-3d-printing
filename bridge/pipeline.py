"""End-to-end design-to-print pipeline.

Orchestrates:
  1. Fusion 360 STL export  (HTTP → Fusion MCP add-in at port 8765)
  2. PrusaSlicer profile selection, slicing, and diagnostics  (local Python)
  3. Optional printer push  (OctoPrint / Moonraker)

Use as a library (import and await) or via bridge.server as MCP tools.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from prusa.tools.printer import push_to_printer
from prusa.tools.profile import ProfileManager, TuneResult
from prusa.tools.slicer import DiagnosticsResult, FixSuggestion, Slicer, SliceResult


# ---------------------------------------------------------------------------
# Fusion 360 MCP HTTP client
# ---------------------------------------------------------------------------

FUSION_MCP_URL = "http://127.0.0.1:8765/mcp"


async def _call_fusion_tool(
    tool_name: str,
    arguments: dict,
    timeout: float = 60.0,
) -> dict:
    """POST a tools/call request to the Fusion MCP server and return the result.

    The server always streams tool results as SSE; we parse the first
    ``data: {...}`` line and return the JSON-RPC ``result`` object.

    Raises
    ------
    httpx.ConnectError
        Fusion 360 is not running or the add-in is not active.
    RuntimeError
        The tool executed but reported isError=True, or the response
        contained no parseable result line.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                FUSION_MCP_URL,
                json=payload,
                headers={"Accept": "application/json, text/event-stream"},
                timeout=timeout,
            )
            resp.raise_for_status()
    except httpx.ConnectError as exc:
        raise httpx.ConnectError(
            f"Cannot reach Fusion 360 MCP add-in at {FUSION_MCP_URL}. "
            "Ensure Fusion 360 is open and the AutodeskFusionMCP add-in is running "
            "(Shift+S → Add-Ins → AutodeskFusionMCP → Run)."
        ) from exc

    # Parse the SSE stream: each tool-call result arrives as "data: {json}"
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        result = event.get("result", {})
        if result.get("isError"):
            text = (result.get("content") or [{}])[0].get("text", "Unknown error")
            raise RuntimeError(
                f"Fusion tool '{tool_name}' returned an error:\n{text}"
            )
        return result

    raise RuntimeError(
        f"Fusion MCP server returned no parseable result for tool '{tool_name}'. "
        f"Raw response:\n{resp.text[:500]}"
    )


def _first_text(result: dict) -> str:
    """Extract the first text item from an MCP result content list."""
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item["text"]
    return ""


# ---------------------------------------------------------------------------
# Profile selection scoring
# ---------------------------------------------------------------------------

def _score_filament_profile(name: str, settings: dict, material: str) -> float:
    """Score a filament profile against the requested material.

    Rules (applied cumulatively):
      +100  ``filament_type`` setting exactly matches *material* (case-insensitive)
      - 50  ``filament_type`` is set but does NOT match (clear type mismatch)
      + 20  *material* string appears anywhere in the profile name

    A profile with a matching type always wins over one with a mismatched type
    regardless of name similarity, so the name bonus only breaks ties.
    """
    score = 0.0
    mat_upper = material.strip().upper()
    filament_type = settings.get("filament_type", "").strip().upper()

    if filament_type == mat_upper:
        score += 100.0
    elif filament_type:                    # set but wrong — penalise
        score -= 50.0

    if mat_upper in name.upper():
        score += 20.0

    return score


# Goal → substrings that appear in PrusaSlicer "print" profile names that
# indicate the profile is a good fit for that goal.
_GOAL_PRINT_KEYWORDS: dict[str, list[str]] = {
    "quality":              ["QUALITY", "DETAIL", "0.10MM", "0.15MM"],
    "faster":               ["SPEED", "DRAFT", "FAST", "0.30MM"],
    "stronger":             ["QUALITY", "STRUCTURAL"],
    "better_surface_quality": ["QUALITY", "DETAIL", "0.10MM", "0.05MM"],
    "fix_stringing":        ["QUALITY"],
    "fix_warping":          ["QUALITY"],
    "fix_bridging":         ["QUALITY"],
}


def _score_print_profile(name: str, print_goal: str) -> float:
    """Score a print profile by keyword affinity to *print_goal*."""
    score = 0.0
    key = print_goal.lower().replace(" ", "_")
    for kw in _GOAL_PRINT_KEYWORDS.get(key, []):
        if kw in name.upper():
            score += 10.0
    return score


def _select_profiles(
    pm: ProfileManager,
    material: str,
    print_goal: str,
) -> tuple[str, str | None]:
    """Return (print_profile_name, filament_profile_name) for a given material+goal.

    Filament: scored by ``filament_type`` match; type mismatch is penalised by −50.
    Print:    scored by keyword affinity; falls back to the first available profile.

    Raises RuntimeError if no print profiles are found.
    """
    all_profiles = pm.list_profiles()
    print_profiles  = [p for p in all_profiles if p["category"] == "print"]
    filament_profiles = [p for p in all_profiles if p["category"] == "filament"]

    # ── filament profile ──────────────────────────────────────────────────────
    best_filament: str | None = None
    best_filament_score = float("-inf")
    for fp in filament_profiles:
        try:
            settings = pm.read_profile(fp["name"])
        except KeyError:
            continue
        score = _score_filament_profile(fp["name"], settings, material)
        if score > best_filament_score:
            best_filament_score = score
            best_filament = fp["name"]

    # ── print profile ─────────────────────────────────────────────────────────
    if not print_profiles:
        raise RuntimeError(
            "No print profiles found. Check PrusaSlicer installation and the "
            "config bundle path in prusa/tools/profile.py."
        )
    best_print = max(
        print_profiles,
        key=lambda p: _score_print_profile(p["name"], print_goal),
    )["name"]

    return best_print, best_filament


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Full result of a design_to_print run."""
    stl_path: str
    profile_used: str
    slice_result: SliceResult | None
    diagnostics: DiagnosticsResult | None
    warnings: list[str] = field(default_factory=list)
    gcode_path: str | None = None
    printer_job_id: str | None = None


@dataclass
class TuneAndResliceResult:
    """Result of a tune_and_reslice run, carrying both baseline and tuned slices."""
    stl_path: str
    old_profile: str
    new_profile: str
    old_slice: SliceResult
    new_slice: SliceResult
    changes: dict[str, str]
    explanations: dict[str, str]


# ---------------------------------------------------------------------------
# Warning collection
# ---------------------------------------------------------------------------

def _collect_warnings(
    diagnostics: DiagnosticsResult,
    fixes: list[FixSuggestion],
) -> list[str]:
    """Merge thin-wall annotations and fix suggestions into a unified warning list."""
    warnings: list[str] = list(diagnostics.thin_wall_warnings)
    for fix in fixes:
        warnings.append(
            f"{fix.setting}: current={fix.current_value!r}, "
            f"suggested={fix.recommended_value!r} — {fix.reason}"
        )
    return warnings


# ---------------------------------------------------------------------------
# Public pipeline functions
# ---------------------------------------------------------------------------

async def design_to_print(
    component_name: str | None = None,
    material: str = "PLA",
    print_goal: str = "quality",
    start_print: bool = False,
) -> PipelineResult:
    """Export an STL from Fusion 360, slice it, parse diagnostics, optionally print.

    Steps
    -----
    1. Call ``export_stl`` on the Fusion 360 MCP add-in (HTTP POST to port 8765).
    2. Score all PrusaSlicer filament profiles against *material*; penalise profiles
       whose ``filament_type`` setting does not match.  Score all print profiles
       against *print_goal* by keyword affinity.  Select the best of each.
    3. Slice the exported STL using the selected profiles (blocking subprocess,
       run in a thread to avoid stalling the event loop).
    4. Parse diagnostics and collect warnings from thin-wall annotations and the
       fix-suggestion rule engine.
    5. If *start_print* is True **and** the warnings list is empty, push the G-code
       to the printer (requires PRINTER_HOST and PRINTER_API_KEY env vars).

    Parameters
    ----------
    component_name:
        Body or component name in the active Fusion design.  If None, the first
        solid visible body is exported.
    material:
        Filament material, e.g. ``'PLA'``, ``'PETG'``, ``'ABS'``.
    print_goal:
        Slicing intent that guides print-profile selection:
        ``'quality'``, ``'faster'``, ``'stronger'``, ``'better_surface_quality'``,
        or any failure-mode keyword recognised by ``tune_profile``.
    start_print:
        Push G-code to the printer after a clean slice.  Ignored when warnings
        are present — always inspect before printing.

    Returns
    -------
    PipelineResult
        ``stl_path``, ``profile_used``, ``slice_result``, ``diagnostics``,
        ``warnings`` (empty list = clean), ``gcode_path``, ``printer_job_id``.
    """
    pm = ProfileManager()
    slicer = Slicer(profile_manager=pm)

    # ── 1. Export STL from Fusion 360 ────────────────────────────────────────
    fusion_args: dict[str, Any] = {"units": "mm"}
    if component_name:
        fusion_args["component_name"] = component_name

    fusion_result = await _call_fusion_tool("export_stl", fusion_args)
    stl_info = json.loads(_first_text(fusion_result))
    stl_path = stl_info["file_path"]

    # ── 2. Select best profiles ───────────────────────────────────────────────
    print_profile, filament_profile = await asyncio.to_thread(
        _select_profiles, pm, material, print_goal
    )

    # ── 3. Slice ──────────────────────────────────────────────────────────────
    slice_result: SliceResult = await asyncio.to_thread(
        slicer.slice_file,
        stl_path,
        print_profile,
        filament_profile,   # filament_profile positional arg
        None,               # printer_profile — auto-detect from PrusaSlicer.ini
    )
    gcode_path = slice_result.output_gcode_path

    # ── 4. Diagnostics and warnings ───────────────────────────────────────────
    diagnostics: DiagnosticsResult = await asyncio.to_thread(
        slicer.parse_diagnostics, gcode_path
    )
    fixes: list[FixSuggestion] = await asyncio.to_thread(
        slicer.suggest_fixes, diagnostics
    )
    warnings = _collect_warnings(diagnostics, fixes)

    # ── 5. Optional print push ────────────────────────────────────────────────
    printer_job_id: str | None = None
    if start_print and not warnings:
        printer_result = await asyncio.to_thread(push_to_printer, gcode_path, True)
        printer_job_id = printer_result.job_id

    return PipelineResult(
        stl_path=stl_path,
        profile_used=print_profile,
        slice_result=slice_result,
        diagnostics=diagnostics,
        warnings=warnings,
        gcode_path=gcode_path,
        printer_job_id=printer_job_id,
    )


async def tune_and_reslice(
    stl_path: str,
    current_profile_name: str,
    failure_description: str,
) -> TuneAndResliceResult:
    """Tune a print profile for a failure, save it, re-slice, return both results.

    The comparison lets the caller see exactly how the tune changes print time,
    filament use, and any other statistics before committing to the fix.

    Steps
    -----
    1. Slice with *current_profile_name* → ``old_slice`` (baseline).
    2. Run ``tune_profile`` with *failure_description* to generate proposed changes.
    3. Merge base settings with the proposed changes and write the new profile to
       ``prusa/profiles/`` via ``ProfileManager.write_profile`` (git-committed).
    4. Re-slice with the same base profile + ``overrides_dict`` of proposed changes
       (the written file serves as a git record; the slicer uses the override path
       because ``read_profile`` does not look in ``prusa/profiles/``).
    5. Return ``TuneAndResliceResult`` with both slices, the profile names, and the
       full change/explanation dicts.

    Parameters
    ----------
    stl_path:
        Path to the STL to re-slice — typically the ``stl_path`` from a prior
        ``design_to_print`` call.
    current_profile_name:
        The profile name used in the failing or unsatisfactory slice.
    failure_description:
        Plain-English description of the failure, e.g.
        ``'lots of stringing between towers'``.
    """
    pm = ProfileManager()
    slicer = Slicer(profile_manager=pm)

    # ── 1. Baseline slice ─────────────────────────────────────────────────────
    old_slice: SliceResult = await asyncio.to_thread(
        slicer.slice_file,
        stl_path,
        current_profile_name,
    )

    # ── 2. Tune ───────────────────────────────────────────────────────────────
    tune_result: TuneResult = await asyncio.to_thread(
        pm.tune_profile,
        current_profile_name,   # base_profile_name
        "",                     # goal_description — failure drives everything
        failure_description,    # failure_description
    )

    # ── 3. Write the tuned profile (for git history) ──────────────────────────
    base_settings = await asyncio.to_thread(pm.read_profile, current_profile_name)
    merged_settings = {**base_settings, **tune_result.proposed_changes}
    new_profile_name = tune_result.suggested_profile_name
    await asyncio.to_thread(pm.write_profile, new_profile_name, merged_settings)

    # ── 4. Re-slice via overrides (write_profile saves to prusa/profiles/ which
    #       read_profile does not search, so we apply changes as overrides instead)
    new_slice: SliceResult = await asyncio.to_thread(
        slicer.slice_file,
        stl_path,
        current_profile_name,               # same base
        None,                               # filament — auto-detect
        None,                               # printer — auto-detect
        tune_result.proposed_changes,       # overrides_dict
    )

    return TuneAndResliceResult(
        stl_path=stl_path,
        old_profile=current_profile_name,
        new_profile=new_profile_name,
        old_slice=old_slice,
        new_slice=new_slice,
        changes=tune_result.proposed_changes,
        explanations=tune_result.explanations,
    )
