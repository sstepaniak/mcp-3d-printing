"""Push G-code files to an OctoPrint or Moonraker printer over the network.

Required environment variables
-------------------------------
PRINTER_HOST     Full base URL of the printer, e.g. http://octopi.local or
                 http://192.168.1.42:7125
PRINTER_API_KEY  API key.
                   OctoPrint  – Application Key from Settings → API.
                   Moonraker  – API key from moonraker.conf (if auth enabled);
                                leave blank / set to any value for trusted-client
                                installs where no key is required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx


# ─── exceptions ───────────────────────────────────────────────────────────────

class ConfigError(Exception):
    """Missing environment variable or unreachable printer host."""


class PrinterError(Exception):
    """The printer API returned an unexpected / error response."""


# ─── result dataclass ─────────────────────────────────────────────────────────

@dataclass
class PrinterResult:
    printer_type: str    # "octoprint" or "moonraker"
    upload_url: str      # canonical URL of the uploaded file
    job_id: str | None   # filename used as the job reference (both APIs)
    filename: str        # bare filename of the uploaded gcode
    started: bool        # True if start_immediately succeeded


# ─── detection ────────────────────────────────────────────────────────────────

def _detect_printer(host: str, api_key: str) -> str:
    """Return 'octoprint' or 'moonraker'; raise ConfigError if neither found."""
    # OctoPrint: GET /api/version → {"server": "...", "api": "..."}
    try:
        r = httpx.get(
            f"{host}/api/version",
            headers={"X-Api-Key": api_key},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            if "server" in data and "api" in data:
                return "octoprint"
    except httpx.ConnectError:
        raise  # propagate so push_to_printer can give an "unreachable" message
    except httpx.HTTPError:
        pass

    # Moonraker: GET /server/info → {"result": {...}}
    try:
        r = httpx.get(
            f"{host}/server/info",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8,
        )
        if r.status_code == 200 and "result" in r.json():
            return "moonraker"
    except httpx.ConnectError:
        raise  # propagate so push_to_printer can give an "unreachable" message
    except httpx.HTTPError:
        pass

    raise ConfigError(
        f"No OctoPrint or Moonraker server found at {host!r}.\n"
        "  OctoPrint  expects GET /api/version  → {\"server\": ..., \"api\": ...}\n"
        "  Moonraker  expects GET /server/info   → {\"result\": ...}\n"
        "Check that PRINTER_HOST is correct and the printer is online."
    )


# ─── OctoPrint ────────────────────────────────────────────────────────────────

def _upload_octoprint(
    host: str,
    api_key: str,
    gcode_path: Path,
    start_immediately: bool,
) -> PrinterResult:
    headers = {"X-Api-Key": api_key}
    filename = gcode_path.name

    # 1. Upload the file
    with gcode_path.open("rb") as fh:
        upload_resp = httpx.post(
            f"{host}/api/files/local",
            headers=headers,
            files={"file": (filename, fh, "text/plain")},
        )
    if upload_resp.status_code not in range(200, 300):
        raise PrinterError(
            f"OctoPrint upload failed with HTTP {upload_resp.status_code}: "
            f"{upload_resp.text[:200]}"
        )

    body = upload_resp.json()
    upload_url = (
        body.get("files", {}).get("local", {}).get("refs", {}).get("resource")
        or f"{host}/api/files/local/{filename}"
    )

    # 2. Optionally start printing
    started = False
    if start_immediately:
        start_resp = httpx.post(
            f"{host}/api/job",
            headers={**headers, "Content-Type": "application/json"},
            json={"command": "start"},
        )
        if start_resp.status_code not in range(200, 300):
            raise PrinterError(
                f"OctoPrint start failed with HTTP {start_resp.status_code}: "
                f"{start_resp.text[:200]}"
            )
        started = True

    return PrinterResult(
        printer_type="octoprint",
        upload_url=upload_url,
        job_id=filename,
        filename=filename,
        started=started,
    )


# ─── Moonraker ────────────────────────────────────────────────────────────────

def _upload_moonraker(
    host: str,
    api_key: str,
    gcode_path: Path,
    start_immediately: bool,
) -> PrinterResult:
    headers = {"Authorization": f"Bearer {api_key}"}
    filename = gcode_path.name

    # 1. Upload the file
    with gcode_path.open("rb") as fh:
        upload_resp = httpx.post(
            f"{host}/server/files/upload",
            headers=headers,
            files={"file": (filename, fh, "text/plain")},
            data={"root": "gcodes"},
        )
    if upload_resp.status_code not in range(200, 300):
        raise PrinterError(
            f"Moonraker upload failed with HTTP {upload_resp.status_code}: "
            f"{upload_resp.text[:200]}"
        )

    item = upload_resp.json().get("result", {}).get("item", {})
    root = item.get("root", "gcodes")
    path_in_root = item.get("path", filename)
    upload_url = f"{host}/server/files/{root}/{path_in_root}"

    # 2. Optionally start printing
    started = False
    if start_immediately:
        start_resp = httpx.post(
            f"{host}/printer/print/start",
            headers={**headers, "Content-Type": "application/json"},
            json={"filename": filename},
        )
        if start_resp.status_code not in range(200, 300):
            raise PrinterError(
                f"Moonraker print start failed with HTTP {start_resp.status_code}: "
                f"{start_resp.text[:200]}"
            )
        started = True

    return PrinterResult(
        printer_type="moonraker",
        upload_url=upload_url,
        job_id=filename,
        filename=filename,
        started=started,
    )


# ─── public entry point ───────────────────────────────────────────────────────

def push_to_printer(
    gcode_path: str | Path,
    start_immediately: bool = False,
) -> PrinterResult:
    """Upload a G-code file to OctoPrint or Moonraker and optionally start the print.

    Reads PRINTER_HOST and PRINTER_API_KEY from the environment.  Auto-detects
    the printer type by probing the version endpoints.

    Args:
        gcode_path:       Path to the .gcode file to upload.
        start_immediately: If True, issue the print-start command immediately
                          after a successful upload.

    Returns a PrinterResult with upload_url, job_id, filename, and started flag.

    Raises:
        ConfigError   – PRINTER_HOST / PRINTER_API_KEY are missing, or the host
                        is unreachable / doesn't match any known API.
        PrinterError  – The printer API returned a non-2xx response.
    """
    host = os.environ.get("PRINTER_HOST", "").rstrip("/")
    api_key = os.environ.get("PRINTER_API_KEY", "")

    if not host:
        raise ConfigError(
            "PRINTER_HOST environment variable is not set.\n"
            "Set it to the full base URL of your printer, e.g.:\n"
            "  export PRINTER_HOST=http://octopi.local\n"
            "  export PRINTER_HOST=http://192.168.1.42:7125"
        )
    if not api_key:
        raise ConfigError(
            "PRINTER_API_KEY environment variable is not set.\n"
            "  OctoPrint:  Settings → API → Application Keys\n"
            "  Moonraker:  see moonraker.conf [authorization] api_key"
        )

    gcode_path = Path(gcode_path)
    if not gcode_path.exists():
        raise FileNotFoundError(f"G-code file not found: {gcode_path}")

    try:
        printer_type = _detect_printer(host, api_key)
    except httpx.ConnectError as exc:
        raise ConfigError(
            f"Printer host {host!r} is unreachable: {exc}\n"
            "Check that PRINTER_HOST is correct and the printer is online."
        ) from exc

    if printer_type == "octoprint":
        return _upload_octoprint(host, api_key, gcode_path, start_immediately)
    else:
        return _upload_moonraker(host, api_key, gcode_path, start_immediately)
