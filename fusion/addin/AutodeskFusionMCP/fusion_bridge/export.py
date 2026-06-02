"""Export tools for Autodesk Fusion 360: STL, STEP, 3MF, DXF.

All functions follow the same contract as the other fusion_bridge modules:
  - Accept an ``arguments`` dict (already extracted from the MCP envelope).
  - Run on the Fusion main thread (guaranteed by the dispatch layer; no extra
    threading plumbing needed here).
  - Return ``{"content": [{"type": "text", "text": ...}], "isError": bool}``.
"""

import json
import os
import tempfile
import traceback

import adsk.core
import adsk.fusion

from .dispatch import get_app, log


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _ok(payload: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "isError": False,
    }


def _fail(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _get_design() -> "adsk.fusion.Design":
    """Return the active Fusion design, or raise a clear RuntimeError."""
    app = get_app()
    product = app.activeProduct
    if product is None:
        raise RuntimeError(
            "No active product open. Open or create a Fusion 360 design first."
        )
    design = adsk.fusion.Design.cast(product)
    if design is None:
        raise RuntimeError(
            "Active product is not a Fusion 360 design "
            f"(got: {type(product).__name__})."
        )
    return design


def _output_path(suffix: str, user_path: str | None) -> str:
    """Resolve an output path, creating a named temp file if needed."""
    if user_path:
        return user_path
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _body_names(root_comp) -> list[str]:
    """Sorted list of BRepBody names in the root component."""
    return sorted(b.name for b in root_comp.bRepBodies)


def _component_names(root_comp) -> list[str]:
    """Sorted list of all component names reachable from root."""
    names = {root_comp.name}
    for occ in root_comp.allOccurrences:
        names.add(occ.component.name)
    return sorted(names)


def _sketch_names(root_comp) -> list[str]:
    """Sorted list of all sketch names across the design."""
    names = [s.name for s in root_comp.sketches]
    for occ in root_comp.allOccurrences:
        names.extend(s.name for s in occ.component.sketches)
    return sorted(names)


# ---------------------------------------------------------------------------
# export_stl
# ---------------------------------------------------------------------------


def export_stl(arguments: dict) -> dict:
    """Export a body or component as binary STL.

    Resolution order for the export entity:
      1. If *component_name* is given: match a BRepBody name, then a
         component (occurrence) name.
      2. If *component_name* is None: use the first solid visible body in
         the root component; fall back to the first visible root-level
         occurrence; raise if neither exists.

    The *units* parameter is recorded in the response but does not override
    Fusion's unit system — the exported STL reflects the design's current
    unit setting.
    """
    component_name = arguments.get("component_name")
    output_path = arguments.get("output_path")
    units = arguments.get("units", "mm")

    try:
        design = _get_design()
        root_comp = design.rootComponent
        export_mgr = design.exportManager

        entity = None

        if component_name:
            # 1a. Match a body in the root component
            for body in root_comp.bRepBodies:
                if body.name == component_name:
                    entity = body
                    break

            # 1b. Match an occurrence by component name
            if entity is None:
                for occ in root_comp.allOccurrences:
                    if occ.component.name == component_name:
                        entity = occ
                        break

            if entity is None:
                available = sorted(
                    set(_body_names(root_comp) + _component_names(root_comp))
                )
                raise ValueError(
                    f"Body or component '{component_name}' not found. "
                    f"Available names: {available}"
                )
        else:
            # 2a. First solid visible body in root component
            for body in root_comp.bRepBodies:
                if body.isSolid and body.isVisible:
                    entity = body
                    break

            # 2b. First visible root-level occurrence
            if entity is None:
                for occ in root_comp.occurrences:
                    if occ.isVisible:
                        entity = occ
                        break

            if entity is None:
                raise RuntimeError(
                    "No solid bodies or visible occurrences found in the design. "
                    "Use component_name to target a specific body or component."
                )

        path = _output_path(".stl", output_path)
        options = export_mgr.createSTLExportOptions(entity, path)
        options.meshRefinement = (
            adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
        )
        options.isBinaryFormat = True
        options.sendToPrintUtility = False

        if not export_mgr.execute(options):
            raise RuntimeError("STL export returned False — Fusion reported failure.")

        size = os.path.getsize(path)
        log(f"[MCP] export_stl -> {path} ({size} bytes, units={units})")
        return _ok({"file_path": path, "file_size_bytes": size, "units": units})

    except Exception as exc:
        tb = traceback.format_exc()
        log(f"[MCP] export_stl failed: {exc}")
        return _fail(f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{tb}")


# ---------------------------------------------------------------------------
# export_step
# ---------------------------------------------------------------------------


def export_step(arguments: dict) -> dict:
    """Export a component (or the whole design) as a STEP file.

    If *component_name* is None the root component (entire design) is
    exported.  Otherwise the named component is located through
    ``allOccurrences`` and passed to ``createSTEPExportOptions``.
    """
    component_name = arguments.get("component_name")
    output_path = arguments.get("output_path")

    try:
        design = _get_design()
        root_comp = design.rootComponent
        export_mgr = design.exportManager

        path = _output_path(".step", output_path)

        if component_name:
            component = None
            if root_comp.name == component_name:
                component = root_comp
            else:
                for occ in root_comp.allOccurrences:
                    if occ.component.name == component_name:
                        component = occ.component
                        break
            if component is None:
                available = _component_names(root_comp)
                raise ValueError(
                    f"Component '{component_name}' not found. "
                    f"Available components: {available}"
                )
            options = export_mgr.createSTEPExportOptions(path, component)
        else:
            options = export_mgr.createSTEPExportOptions(path)

        if not export_mgr.execute(options):
            raise RuntimeError("STEP export returned False — Fusion reported failure.")

        size = os.path.getsize(path)
        log(f"[MCP] export_step -> {path} ({size} bytes)")
        return _ok({"file_path": path, "file_size_bytes": size})

    except Exception as exc:
        tb = traceback.format_exc()
        log(f"[MCP] export_step failed: {exc}")
        return _fail(f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{tb}")


# ---------------------------------------------------------------------------
# export_3mf
# ---------------------------------------------------------------------------


def export_3mf(arguments: dict) -> dict:
    """Export the entire design as a 3MF file.

    3MF is always rooted at the design's root component — there is no
    component-scoping option for this format.
    """
    output_path = arguments.get("output_path")

    try:
        design = _get_design()
        root_comp = design.rootComponent
        export_mgr = design.exportManager

        path = _output_path(".3mf", output_path)
        options = export_mgr.create3MFExportOptions(root_comp, path)

        if not export_mgr.execute(options):
            raise RuntimeError("3MF export returned False — Fusion reported failure.")

        size = os.path.getsize(path)
        log(f"[MCP] export_3mf -> {path} ({size} bytes)")
        return _ok({"file_path": path, "file_size_bytes": size})

    except Exception as exc:
        tb = traceback.format_exc()
        log(f"[MCP] export_3mf failed: {exc}")
        return _fail(f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{tb}")


# ---------------------------------------------------------------------------
# export_dxf
# ---------------------------------------------------------------------------


def export_dxf(arguments: dict) -> dict:
    """Export a named sketch as a DXF file.

    The sketch is searched first in the root component, then recursively
    through all sub-component occurrences.  ``sketch_name`` is required.
    """
    sketch_name = arguments.get("sketch_name")
    output_path = arguments.get("output_path")

    if not sketch_name:
        return _fail("Error: ValueError: 'sketch_name' is required")

    try:
        design = _get_design()
        root_comp = design.rootComponent

        # Search root component sketches first
        target_sketch = None
        for sketch in root_comp.sketches:
            if sketch.name == sketch_name:
                target_sketch = sketch
                break

        # Then recursively search sub-components
        if target_sketch is None:
            for occ in root_comp.allOccurrences:
                for sketch in occ.component.sketches:
                    if sketch.name == sketch_name:
                        target_sketch = sketch
                        break
                if target_sketch is not None:
                    break

        if target_sketch is None:
            available = _sketch_names(root_comp)
            raise ValueError(
                f"Sketch '{sketch_name}' not found. "
                f"Available sketches: {available}"
            )

        path = _output_path(".dxf", output_path)

        if not target_sketch.saveAsDXF(path):
            raise RuntimeError("DXF export returned False — Fusion reported failure.")

        size = os.path.getsize(path)
        log(f"[MCP] export_dxf -> {path} ({size} bytes)")
        return _ok({"file_path": path, "file_size_bytes": size})

    except Exception as exc:
        tb = traceback.format_exc()
        log(f"[MCP] export_dxf failed: {exc}")
        return _fail(f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{tb}")
