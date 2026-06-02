"""History and version tools for Autodesk Fusion 360.

Covers the parametric timeline (inspect, suppress, roll back) and document
version management (save a named version, list versions).

All functions follow the same contract as the other fusion_bridge modules:
  - Accept an ``arguments`` dict (already extracted from the MCP envelope).
  - Run on the Fusion main thread (guaranteed by the dispatch layer).
  - Return ``{"content": [{"type": "text", "text": ...}], "isError": bool}``.

Timeline marker semantics:
  ``Timeline.markerPosition`` is a 0-based integer.  Features whose index is
  *less than* ``markerPosition`` are active; features *at or after*
  ``markerPosition`` are rolled back.  Setting ``markerPosition = N``
  therefore means "feature N is the first rolled-back feature."
"""

import datetime
import json
import os
import re
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


def _timeline_obj_name(obj) -> str:
    """Best-effort name for a TimelineObject."""
    try:
        name = obj.name
        if name:
            return name
    except Exception:
        pass
    try:
        entity = obj.entity
        if entity is not None:
            name = getattr(entity, "name", None)
            if name:
                return name
    except Exception:
        pass
    return f"Feature_{obj.index}"


def _feature_type(obj) -> str:
    """Extract a readable feature type from a TimelineObject's entity."""
    try:
        entity = obj.entity
        if entity is None:
            return "Unknown"
        obj_type = getattr(entity, "objectType", "")
        # objectType looks like "adsk::fusion::ExtrudeFeature" — keep the last segment
        return obj_type.split("::")[-1] if "::" in obj_type else (obj_type or "Unknown")
    except Exception:
        return "Unknown"


def _validate_index(timeline, index: int) -> None:
    """Raise IndexError if index is outside [0, timeline.count - 1]."""
    count = timeline.count
    if count == 0:
        raise IndexError("Timeline is empty.")
    if index < 0 or index >= count:
        raise IndexError(
            f"Index {index} is out of range. Valid range: 0 – {count - 1}."
        )


def _safe_version_number(data_file) -> int | None:
    try:
        return data_file.versionNumber
    except Exception:
        return None


# ---------------------------------------------------------------------------
# get_timeline
# ---------------------------------------------------------------------------


def get_timeline(arguments: dict) -> dict:
    """Return the ordered list of timeline features.

    Each entry contains index, name, type, is_suppressed, and is_rolled_back
    (True when the feature is beyond the current rollback marker).
    Returns an empty list if the timeline is unavailable or the design has no
    features (e.g. Direct Modelling mode or a blank design).
    """
    try:
        design = _get_design()

        # Timeline may be None in Direct Modelling mode
        timeline = design.timeline
        if timeline is None or timeline.count == 0:
            return _ok(
                {
                    "count": 0,
                    "marker_position": 0,
                    "features": [],
                }
            )

        marker = timeline.markerPosition
        features = []

        for i in range(timeline.count):
            obj = timeline.item(i)
            features.append(
                {
                    "index": i,
                    "name": _timeline_obj_name(obj),
                    "type": _feature_type(obj),
                    "is_suppressed": obj.isSuppressed,
                    "is_rolled_back": i >= marker,
                }
            )

        log(f"[MCP] get_timeline: {len(features)} features, marker at {marker}")
        return _ok(
            {
                "count": len(features),
                "marker_position": marker,
                "features": features,
            }
        )

    except Exception as exc:
        log(f"[MCP] get_timeline failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# suppress_feature
# ---------------------------------------------------------------------------


def suppress_feature(arguments: dict) -> dict:
    """Suppress the timeline feature at the given index.

    Returns index, name, and suppressed=True on success.
    """
    index = arguments.get("index")
    if index is None:
        return _fail("Error: ValueError: 'index' is required")

    try:
        index = int(index)
        design = _get_design()
        timeline = design.timeline
        _validate_index(timeline, index)

        obj = timeline.item(index)
        name = _timeline_obj_name(obj)
        obj.isSuppressed = True

        log(f"[MCP] suppress_feature: [{index}] {name!r}")
        return _ok({"index": index, "name": name, "suppressed": True})

    except Exception as exc:
        log(f"[MCP] suppress_feature failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# unsuppress_feature
# ---------------------------------------------------------------------------


def unsuppress_feature(arguments: dict) -> dict:
    """Unsuppress the timeline feature at the given index.

    Returns index, name, and suppressed=False on success.
    """
    index = arguments.get("index")
    if index is None:
        return _fail("Error: ValueError: 'index' is required")

    try:
        index = int(index)
        design = _get_design()
        timeline = design.timeline
        _validate_index(timeline, index)

        obj = timeline.item(index)
        name = _timeline_obj_name(obj)
        obj.isSuppressed = False

        log(f"[MCP] unsuppress_feature: [{index}] {name!r}")
        return _ok({"index": index, "name": name, "suppressed": False})

    except Exception as exc:
        log(f"[MCP] unsuppress_feature failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# rollback_to
# ---------------------------------------------------------------------------


def rollback_to(arguments: dict) -> dict:
    """Reposition the timeline rollback marker.

    After this call features 0 … index-1 are active; feature at *index* and
    beyond are rolled back.  The operation is non-destructive — no geometry is
    deleted.  Calling rollback_to(timeline.count) fully rolls the timeline
    forward.

    Returns rolled_back_to_index and the name of the feature now at the marker
    boundary.
    """
    index = arguments.get("index")
    if index is None:
        return _fail("Error: ValueError: 'index' is required")

    try:
        index = int(index)
        design = _get_design()
        timeline = design.timeline

        if timeline is None:
            raise RuntimeError("Timeline is not available (design may be in Direct Modelling mode).")

        count = timeline.count
        # Allow index == count to mean "fully rolled forward"
        if index < 0 or index > count:
            raise IndexError(
                f"Index {index} is out of range. Valid range: 0 – {count} "
                f"(use {count} to roll fully forward)."
            )

        timeline.markerPosition = index

        # Name of the feature at this boundary (or "end of timeline")
        if index < count:
            feature_name = _timeline_obj_name(timeline.item(index))
        else:
            feature_name = "<end of timeline>"

        log(f"[MCP] rollback_to: marker -> {index} ({feature_name!r})")
        return _ok(
            {
                "rolled_back_to_index": index,
                "feature_name_at_index": feature_name,
                "marker_position": timeline.markerPosition,
            }
        )

    except Exception as exc:
        log(f"[MCP] rollback_to failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# save_version
# ---------------------------------------------------------------------------


def save_version(arguments: dict) -> dict:
    """Save the active document as a named version.

    Cloud documents (backed by the Fusion cloud):
        Calls ``Document.save(description)`` which creates a new numbered
        version.  Returns version_type="cloud" and the resulting version ID.

    Local / unsaved documents:
        Exports the design as a ``.f3d`` archive (Fusion Design Archive) with
        a datestamp in the filename, placed in ``~/Documents`` (falling back to
        the system temp directory).  Returns version_type="local" and the
        full path of the saved archive.
    """
    description = arguments.get("description", "")

    try:
        app = get_app()
        doc = app.activeDocument
        if doc is None:
            raise RuntimeError("No active document is open.")

        data_file = doc.dataFile  # None for local / unsaved documents

        if data_file is not None:
            # ── Cloud document ─────────────────────────────────────────────
            doc.save(description)
            version_id = data_file.versionId
            version_number = _safe_version_number(data_file)
            log(f"[MCP] save_version: cloud v{version_number} id={version_id}")
            return _ok(
                {
                    "version_type": "cloud",
                    "path_or_version_id": version_id,
                    "version_number": version_number,
                    "description": description,
                }
            )

        else:
            # ── Local / unsaved document ───────────────────────────────────
            design = _get_design()
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = re.sub(r"[^\w\-]", "_", doc.name or "Untitled")
            filename = f"{safe_name}_{timestamp}.f3d"

            # Choose save directory: ~/Documents, then tempdir as fallback
            candidates = [
                os.path.join(os.path.expanduser("~"), "Documents"),
                tempfile.gettempdir(),
            ]
            save_dir = next((d for d in candidates if os.path.isdir(d)), tempfile.gettempdir())
            file_path = os.path.join(save_dir, filename)

            # Export as Fusion Archive (.f3d)
            options = design.exportManager.createFusionArchiveExportOptions(file_path)
            if not design.exportManager.execute(options):
                raise RuntimeError("Fusion Archive export returned False.")

            size = os.path.getsize(file_path)
            log(f"[MCP] save_version: local {file_path} ({size} bytes)")
            return _ok(
                {
                    "version_type": "local",
                    "path_or_version_id": file_path,
                    "file_size_bytes": size,
                    "description": description,
                }
            )

    except Exception as exc:
        log(f"[MCP] save_version failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# list_versions
# ---------------------------------------------------------------------------


def list_versions(arguments: dict) -> dict:
    """Return saved versions of the active document.

    Cloud documents:
        Attempts to enumerate all versions via ``DataFile.versions``.
        Falls back to reporting only the current version if the full history
        is not accessible (e.g. due to permissions or API limitations).

    Local / unsaved documents:
        Returns an empty list — local ``.f3d`` exports have no version index.
    """
    try:
        app = get_app()
        doc = app.activeDocument
        if doc is None:
            raise RuntimeError("No active document is open.")

        data_file = doc.dataFile

        if data_file is None:
            log("[MCP] list_versions: local document — no cloud version history")
            return _ok(
                {
                    "count": 0,
                    "versions": [],
                    "note": (
                        "Local or unsaved document has no cloud version history. "
                        "Use save_version to export a timestamped .f3d archive."
                    ),
                }
            )

        versions = []

        # ── Try to enumerate all versions ─────────────────────────────────
        try:
            all_versions = data_file.versions  # DataVersions collection
            for v in all_versions:
                entry = {
                    "index": _safe_attr(v, "versionNumber"),
                    "name": _safe_attr(v, "name") or data_file.name,
                    "description": _safe_attr(v, "description") or "",
                    "timestamp": _safe_str(v, "dateCreated"),
                    "version_id": _safe_attr(v, "id"),
                    "is_latest": _safe_attr(v, "isLatestVersion"),
                }
                versions.append(entry)
        except Exception:
            # Fall back to current-version-only info
            versions.append(
                {
                    "index": _safe_version_number(data_file),
                    "name": data_file.name,
                    "description": _safe_attr(data_file, "description") or "",
                    "timestamp": None,
                    "version_id": data_file.versionId,
                    "is_latest": _safe_attr(data_file, "isLatestVersion"),
                }
            )

        # Sort by version number ascending (None sorts last)
        versions.sort(key=lambda v: (v["index"] is None, v["index"] or 0))

        log(f"[MCP] list_versions: {len(versions)} version(s)")
        return _ok({"count": len(versions), "versions": versions})

    except Exception as exc:
        log(f"[MCP] list_versions failed: {exc}")
        return _fail(
            f"Error: {type(exc).__name__}: {exc}\nTraceback:\n{traceback.format_exc()}"
        )


# ---------------------------------------------------------------------------
# Internal helpers for safe attribute access
# ---------------------------------------------------------------------------


def _safe_attr(obj, attr):
    try:
        return getattr(obj, attr, None)
    except Exception:
        return None


def _safe_str(obj, attr) -> str | None:
    val = _safe_attr(obj, attr)
    return str(val) if val is not None else None
