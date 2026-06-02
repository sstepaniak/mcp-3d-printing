"""Parameter tools for Autodesk Fusion 360: list, inspect, and modify user parameters.

All functions follow the same contract as the other fusion_bridge modules:
  - Accept an ``arguments`` dict (already extracted from the MCP envelope).
  - Run on the Fusion main thread (guaranteed by the dispatch layer).
  - Return ``{"content": [{"type": "text", "text": ...}], "isError": bool}``.

Unit note: Fusion stores parameter values in internal units —
centimetres for lengths, radians for angles. The ``value`` field in
responses reflects these raw internal values. The ``expression`` field
(e.g. "5 mm") is the human-readable representation.  Use
``set_parameter`` with the desired numeric value and unit string and
Fusion will handle the conversion automatically.
"""

import json
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


def _param_to_dict(p) -> dict:
    """Serialize a UserParameter to a JSON-safe dict."""
    return {
        "name": p.name,
        "expression": p.expression,
        "value": p.value,       # Fusion internal units: cm (length), rad (angle)
        "unit": p.unit,
        "comment": p.comment or "",
    }


def _available_param_names(design) -> list:
    params = design.userParameters
    return [params.item(i).name for i in range(params.count)]


# ---------------------------------------------------------------------------
# list_parameters
# ---------------------------------------------------------------------------


def list_parameters(arguments: dict) -> dict:
    """Return all user parameters with name, expression, value, unit, comment."""
    try:
        design = _get_design()
        params = design.userParameters
        result = {
            "count": params.count,
            "parameters": [_param_to_dict(params.item(i)) for i in range(params.count)],
        }
        log(f"[MCP] list_parameters: {params.count} parameter(s)")
        return _ok(result)

    except Exception as exc:
        log(f"[MCP] list_parameters failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# get_parameter
# ---------------------------------------------------------------------------


def get_parameter(arguments: dict) -> dict:
    """Return full details for one named user parameter."""
    name = arguments.get("name")
    if not name:
        return _fail("Error: ValueError: 'name' is required")

    try:
        design = _get_design()
        param = design.userParameters.itemByName(name)
        if param is None:
            raise ValueError(
                f"Parameter '{name}' not found. "
                f"Available: {_available_param_names(design)}"
            )
        log(f"[MCP] get_parameter: {name}")
        return _ok(_param_to_dict(param))

    except Exception as exc:
        log(f"[MCP] get_parameter failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# set_parameter
# ---------------------------------------------------------------------------


def set_parameter(arguments: dict) -> dict:
    """Modify a user parameter value and confirm the update by reading it back.

    The ``value`` is treated as being in the parameter's current unit (or the
    explicitly provided ``unit``). Fusion evaluates the resulting expression and
    propagates the change through the parametric timeline automatically.
    """
    name = arguments.get("name")
    value = arguments.get("value")
    unit = arguments.get("unit")  # optional override

    if not name:
        return _fail("Error: ValueError: 'name' is required")
    if value is None:
        return _fail("Error: ValueError: 'value' is required")

    try:
        design = _get_design()
        param = design.userParameters.itemByName(name)
        if param is None:
            raise ValueError(
                f"Parameter '{name}' not found. "
                f"Available: {_available_param_names(design)}"
            )

        # Snapshot before change
        old_value = param.value
        old_expression = param.expression

        # Build the new expression; Fusion resolves units and updates the model
        effective_unit = unit if unit is not None else param.unit
        new_expression = (
            f"{value} {effective_unit}" if effective_unit else str(value)
        )
        param.expression = new_expression

        # Read back the confirmed (possibly normalised) state
        new_value = param.value
        confirmed_expression = param.expression

        log(
            f"[MCP] set_parameter: {name} "
            f"{old_expression!r} -> {confirmed_expression!r}"
        )
        return _ok(
            {
                "name": name,
                "old_value": old_value,
                "new_value": new_value,
                "unit": param.unit,
                "old_expression": old_expression,
                "new_expression": confirmed_expression,
            }
        )

    except Exception as exc:
        log(f"[MCP] set_parameter failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )
