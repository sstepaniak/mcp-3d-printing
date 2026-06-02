"""MCP tool routing and generic Fusion API dispatch."""

import json
import os
import traceback

import adsk.core

from . import (
    analysis,
    doc_lookup,
    export,
    history,
    parameters,
    python_exec,
    script_store,
    selection,
    tool_surface,
    value_builders,
    viewport,
)
from .dispatch import log


# -- Response helpers -------------------------------------------------------


def _success(payload):
    """Return a JSON-dict success response."""
    body = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return {"content": [{"type": "text", "text": body}], "isError": False}


def _failure(payload):
    """Return a JSON-dict error response."""
    body = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return {"content": [{"type": "text", "text": body}], "isError": True}


# -- Generic API execution --------------------------------------------------


def _execute_api_call(api_path, args, kwargs, remember_as, return_properties):
    """Resolve *api_path*, invoke it with *args*/*kwargs*, and return a
    human-readable summary string."""
    target = value_builders.resolve_path(api_path)
    resolved_args = [value_builders.coerce_arg(a) for a in args]
    resolved_kwargs = {k: value_builders.coerce_arg(v) for k, v in kwargs.items()}

    result = target(*resolved_args, **resolved_kwargs) if callable(target) else target

    if remember_as:
        value_builders.OBJECT_STORE[remember_as] = result

    result_type, summary = value_builders.format_result(result, return_properties)
    message = f"OK: {api_path} -> {result_type}({summary})"
    if remember_as:
        message += f" [stored as '{remember_as}']"
    message += f"\nContext: {len(value_builders.OBJECT_STORE)} objects stored"
    return message


def _format_call_signature(api_path, args, kwargs):
    parts = [repr(a) for a in args]
    if kwargs:
        parts.extend(f"{k}={v!r}" for k, v in kwargs.items())
    return f"{api_path}({', '.join(parts)})"


# -- Per-tool handler: call_autodesk_api (generic API) ------------------------


def handle_generic_api_call(call_data):
    """Handle the call_autodesk_api generic API tool."""
    params = call_data.get("params", {})
    arguments = params.get("arguments", {})
    description = arguments.get("description", "")

    api_path = arguments.get("api_path", "")
    args = arguments.get("args", [])
    kwargs = arguments.get("kwargs", {})
    remember_as = arguments.get("remember_as")
    return_properties = arguments.get("return_properties", [])

    if description and api_path:
        log(f"[MCP] Tool call: call_autodesk_api - {api_path} ({description})")
    elif api_path:
        log(f"[MCP] Tool call: call_autodesk_api - {api_path}")

    # Dispatch special pseudo-paths
    if api_path == "get_pid":
        return _success(f"Process ID: {os.getpid()}")

    if api_path == "clear_context":
        cleared = value_builders.clear_object_store()
        return _success(f"Cleared {cleared} stored objects")

    try:
        result = _execute_api_call(
            api_path, args, kwargs, remember_as, return_properties
        )
        return _success(result)
    except Exception as exc:
        tb = traceback.format_exc()
        text = (
            f"Error: {type(exc).__name__}: {exc}\n"
            f"Call: {_format_call_signature(api_path, args, kwargs)}\n"
            f"Traceback:\n{tb}"
        )
        log(f"Tool call failed: {exc}", adsk.core.LogLevels.ErrorLogLevel)
        log(tb, adsk.core.LogLevels.ErrorLogLevel)
        return _failure(text)


# -- Wrapper helpers for handlers that take arguments directly ---------------


def _wrap(fn):
    """Wrap a handler that expects an arguments dict to accept call_data envelope."""

    def wrapper(call_data):
        params = call_data.get("params", {})
        arguments = params.get("arguments", {})
        return fn(arguments)

    return wrapper


def _wrap_doc(fn):
    """Wrap doc_lookup handlers that expect (arguments, log_fn)."""

    def wrapper(call_data):
        params = call_data.get("params", {})
        arguments = params.get("arguments", {})
        return fn(arguments, log)

    return wrapper


# -- Handler registry -------------------------------------------------------

TOOL_HANDLERS = tool_surface.build_tool_handlers(
    generic_api_call=handle_generic_api_call,
    run_python=_wrap(python_exec.run_python),
    capture_viewport=_wrap(viewport.capture),
    fetch_api_documentation=_wrap_doc(doc_lookup.fetch_api_documentation),
    fetch_online_documentation=_wrap_doc(doc_lookup.fetch_online_documentation),
    fetch_design_guide=_wrap_doc(doc_lookup.fetch_design_guide),
    save_script=_wrap(script_store.save_script),
    load_script=_wrap(script_store.load_script),
    list_scripts=_wrap(script_store.list_scripts),
    delete_script=_wrap(script_store.delete_script),
    get_active_selection=_wrap(selection.get_active_selection),
    export_stl=_wrap(export.export_stl),
    export_step=_wrap(export.export_step),
    export_3mf=_wrap(export.export_3mf),
    export_dxf=_wrap(export.export_dxf),
    list_parameters=_wrap(parameters.list_parameters),
    get_parameter=_wrap(parameters.get_parameter),
    set_parameter=_wrap(parameters.set_parameter),
    measure_body=_wrap(analysis.measure_body),
    check_interference=_wrap(analysis.check_interference),
    list_components=_wrap(analysis.list_components),
    get_timeline=_wrap(history.get_timeline),
    suppress_feature=_wrap(history.suppress_feature),
    unsuppress_feature=_wrap(history.unsuppress_feature),
    rollback_to=_wrap(history.rollback_to),
    save_version=_wrap(history.save_version),
    list_versions=_wrap(history.list_versions),
)
