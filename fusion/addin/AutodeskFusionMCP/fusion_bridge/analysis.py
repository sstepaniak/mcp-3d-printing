"""Analysis tools for Autodesk Fusion 360: body measurements, interference, component tree.

All functions follow the same contract as the other fusion_bridge modules:
  - Accept an ``arguments`` dict (already extracted from the MCP envelope).
  - Run on the Fusion main thread (guaranteed by the dispatch layer).
  - Return ``{"content": [{"type": "text", "text": ...}], "isError": bool}``.

Unit conversions applied throughout (Fusion stores geometry in cm):
  - lengths:  × 10   → mm
  - areas:    × 100  → mm²
  - volumes:  × 1000 → mm³
"""

import json
import traceback

import adsk.core
import adsk.fusion

from .dispatch import get_app, log

# Fusion internal unit (cm) → output unit (mm) scale factors
_CM_TO_MM = 10.0
_CM2_TO_MM2 = 100.0
_CM3_TO_MM3 = 1000.0


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
    app = get_app()
    product = app.activeProduct
    if product is None:
        raise RuntimeError(
            "No active product open. Open or create a Fusion 360 design first."
        )
    design = adsk.fusion.Design.cast(product)
    if design is None:
        raise RuntimeError(
            f"Active product is not a Fusion 360 design "
            f"(got: {type(product).__name__})."
        )
    return design


def _all_body_names(root_comp) -> list:
    names = [b.name for b in root_comp.bRepBodies]
    for occ in root_comp.allOccurrences:
        names.extend(b.name for b in occ.component.bRepBodies)
    return sorted(names)


def _find_body(root_comp, body_name: str):
    """Search for a BRepBody by name in root and all sub-components."""
    for body in root_comp.bRepBodies:
        if body.name == body_name:
            return body
    for occ in root_comp.allOccurrences:
        for body in occ.component.bRepBodies:
            if body.name == body_name:
                return body
    return None


def _bodies_for_component(root_comp, comp_name: str):
    """Return list of BRepBodies for a named component, or None if not found."""
    if root_comp.name == comp_name:
        return list(root_comp.bRepBodies)
    for occ in root_comp.allOccurrences:
        if occ.component.name == comp_name:
            return list(occ.component.bRepBodies)
    return None


def _all_component_names(root_comp) -> list:
    names = {root_comp.name}
    for occ in root_comp.allOccurrences:
        names.add(occ.component.name)
    return sorted(names)


# ---------------------------------------------------------------------------
# measure_body
# ---------------------------------------------------------------------------


def measure_body(arguments: dict) -> dict:
    """Return physical measurements for a named body (or first solid body).

    Values are returned in mm / mm² / mm³. The center of mass is in mm
    relative to the design origin.
    """
    body_name = arguments.get("body_name")

    try:
        design = _get_design()
        root_comp = design.rootComponent

        # Resolve the body
        body = None
        if body_name:
            body = _find_body(root_comp, body_name)
            if body is None:
                raise ValueError(
                    f"Body '{body_name}' not found. "
                    f"Available bodies: {_all_body_names(root_comp)}"
                )
        else:
            # First solid visible body in root component
            for b in root_comp.bRepBodies:
                if b.isSolid and b.isVisible:
                    body = b
                    break
            # Fall back to first solid visible body in any sub-component
            if body is None:
                for occ in root_comp.allOccurrences:
                    for b in occ.component.bRepBodies:
                        if b.isSolid and b.isVisible:
                            body = b
                            break
                    if body is not None:
                        break
            if body is None:
                raise RuntimeError(
                    "No solid visible body found in the design. "
                    "Use body_name to target a specific body."
                )

        # Physical properties (low accuracy = fast; sufficient for inspection)
        props = body.getPhysicalProperties(
            adsk.fusion.CalculationAccuracy.LowCalculationAccuracy
        )

        # Bounding box (cm → mm)
        bb = body.boundingBox
        x_mm = (bb.maxPoint.x - bb.minPoint.x) * _CM_TO_MM
        y_mm = (bb.maxPoint.y - bb.minPoint.y) * _CM_TO_MM
        z_mm = (bb.maxPoint.z - bb.minPoint.z) * _CM_TO_MM

        com = props.centerOfMass
        result = {
            "body_name": body.name,
            "is_solid": body.isSolid,
            "volume_mm3": round(props.volume * _CM3_TO_MM3, 4),
            "surface_area_mm2": round(props.area * _CM2_TO_MM2, 4),
            "bounding_box_mm": {
                "x": round(x_mm, 4),
                "y": round(y_mm, 4),
                "z": round(z_mm, 4),
            },
            "center_of_mass_mm": {
                "x": round(com.x * _CM_TO_MM, 4),
                "y": round(com.y * _CM_TO_MM, 4),
                "z": round(com.z * _CM_TO_MM, 4),
            },
        }
        log(
            f"[MCP] measure_body: {body.name} "
            f"vol={result['volume_mm3']} mm³ "
            f"area={result['surface_area_mm2']} mm²"
        )
        return _ok(result)

    except Exception as exc:
        log(f"[MCP] measure_body failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# check_interference
# ---------------------------------------------------------------------------


def check_interference(arguments: dict) -> dict:
    """Check whether two named components physically interfere.

    Returns ``interferes`` (bool) and ``interference_volume_mm3`` (the summed
    volume of all overlapping regions, or null if zero or not computable).
    """
    component_a = arguments.get("component_a")
    component_b = arguments.get("component_b")

    if not component_a:
        return _fail("Error: ValueError: 'component_a' is required")
    if not component_b:
        return _fail("Error: ValueError: 'component_b' is required")

    try:
        design = _get_design()
        root_comp = design.rootComponent

        bodies_a = _bodies_for_component(root_comp, component_a)
        bodies_b = _bodies_for_component(root_comp, component_b)

        if bodies_a is None:
            raise ValueError(
                f"Component '{component_a}' not found. "
                f"Available: {_all_component_names(root_comp)}"
            )
        if bodies_b is None:
            raise ValueError(
                f"Component '{component_b}' not found. "
                f"Available: {_all_component_names(root_comp)}"
            )
        if not bodies_a:
            raise RuntimeError(f"Component '{component_a}' has no bodies.")
        if not bodies_b:
            raise RuntimeError(f"Component '{component_b}' has no bodies.")

        # Build entity collection for the interference engine
        entities = adsk.core.ObjectCollection.create()
        for b in bodies_a:
            entities.add(b)
        for b in bodies_b:
            entities.add(b)

        interference_input = design.createInterferenceInput(entities)
        results = design.computeInterference(interference_input)

        interferes = results.count > 0
        interference_volume_mm3 = None

        if interferes:
            total_cm3 = 0.0
            for i in range(results.count):
                try:
                    vol = results.item(i).interferingBody.volume
                    total_cm3 += vol
                except Exception:
                    # interferingBody geometry may be unavailable for some pairs
                    pass
            if total_cm3 > 0:
                interference_volume_mm3 = round(total_cm3 * _CM3_TO_MM3, 4)

        log(
            f"[MCP] check_interference: {component_a} vs {component_b} "
            f"-> interferes={interferes}"
        )
        return _ok(
            {
                "component_a": component_a,
                "component_b": component_b,
                "interferes": interferes,
                "interference_volume_mm3": interference_volume_mm3,
                "interference_pair_count": results.count,
            }
        )

    except Exception as exc:
        log(f"[MCP] check_interference failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# list_components
# ---------------------------------------------------------------------------


def list_components(arguments: dict) -> dict:
    """Return a flat tree of all components and bodies in the active design.

    Each entry has: name, type ("component" | "body"), parent (component name
    or null for root), is_visible, and for bodies also is_solid.
    Components that appear in multiple places (reused) are listed once per
    occurrence to preserve the parent relationship.
    """
    try:
        design = _get_design()
        root_comp = design.rootComponent

        entries = []

        def collect(comp, parent_name: str | None, depth: int = 0):
            if depth > 32:
                return  # guard against pathological circular references

            # Bodies owned by this component
            for body in comp.bRepBodies:
                entries.append(
                    {
                        "name": body.name,
                        "type": "body",
                        "parent": comp.name,
                        "is_visible": body.isVisible,
                        "is_solid": body.isSolid,
                    }
                )

            # Direct child occurrences (one level down from this component)
            for occ in comp.occurrences:
                sub_comp = occ.component
                entries.append(
                    {
                        "name": sub_comp.name,
                        "type": "component",
                        "parent": comp.name,
                        "is_visible": occ.isVisible,
                        "occurrence_name": occ.name,
                    }
                )
                collect(sub_comp, comp.name, depth + 1)

        # Root component entry (no parent)
        entries.append(
            {
                "name": root_comp.name,
                "type": "component",
                "parent": None,
                "is_visible": True,
            }
        )
        collect(root_comp, None)

        log(f"[MCP] list_components: {len(entries)} entries")
        return _ok({"count": len(entries), "components": entries})

    except Exception as exc:
        log(f"[MCP] list_components failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )
