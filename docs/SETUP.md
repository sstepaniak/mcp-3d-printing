# Setup Guide

## Prerequisites

Before installing, verify that each of the following is in place:

- [ ] **Python 3.11+** — `python3 --version`
- [ ] **PrusaSlicer 2.7+** installed at its default path or accessible as `prusa-slicer` on your `$PATH`
- [ ] **Autodesk Fusion 360** installed and licensed
- [ ] **Node.js 18+** (required by the MCP runtime) — `node --version`
- [ ] **Claude Desktop** (or another MCP-capable client) installed
- [ ] **Git** — `git --version`
- [ ] An OctoPrint instance reachable on the local network (optional — required only for `push_to_printer`)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/youruser/mcp-3d-printing.git
cd mcp-3d-printing
git submodule update --init --recursive
```

### 2. Create and activate the Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install Python dependencies

```bash
pip install -e ".[dev]"
```

This installs both production dependencies (`mcp`, `httpx`) and the test tools (`pytest`, `pytest-asyncio`, `pytest-mock`).

### 4. Install the Fusion 360 add-in

Copy the add-in directory into Fusion's add-ins folder:

```bash
# macOS
cp -r fusion/addin/AutodeskFusionMCP \
  "$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/"

# Windows
xcopy /E /I fusion\addin\AutodeskFusionMCP ^
  "%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\AutodeskFusionMCP"
```

Then inside Fusion 360:

1. Open **Tools → Add-Ins → Scripts and Add-Ins**
2. Select **AutodeskFusionMCP** under the **Add-Ins** tab
3. Click **Run** and enable **Run on Startup**

The add-in starts an HTTP server on `localhost:8765`.

### 5. Configure the PrusaSlicer config bundle path (optional)

The PrusaSlicer MCP server reads profile data from:

- `PrusaSlicer_config_bundle.ini` in the repo root (export yours from PrusaSlicer → **File → Export → Export Config Bundle**)
- `~/Library/Application Support/PrusaSlicer/` (macOS) — read automatically

If your bundle lives elsewhere, edit `prusa/tools/profile.py` and update `_CONFIG_BUNDLE`.

---

## Adding the servers to Claude config

Open your Claude Desktop configuration file:

```
# macOS
~/Library/Application Support/Claude/claude_desktop_config.json

# Windows
%APPDATA%\Claude\claude_desktop_config.json
```

Add all three servers under the `mcpServers` key:

```json
{
  "mcpServers": {
    "prusa-mcp": {
      "command": "/path/to/mcp-3d-printing/.venv/bin/python",
      "args": ["-m", "prusa.server"],
      "cwd": "/path/to/mcp-3d-printing"
    },
    "bridge-mcp": {
      "command": "/path/to/mcp-3d-printing/.venv/bin/python",
      "args": ["-m", "bridge.server"],
      "cwd": "/path/to/mcp-3d-printing"
    },
    "fusion-mcp": {
      "command": "node",
      "args": ["/path/to/mcp-3d-printing/fusion/mcp-server/index.js"]
    }
  }
}
```

Replace `/path/to/mcp-3d-printing` with the absolute path to your clone.

Restart Claude Desktop after saving.

---

## Verifying each server is working

### Prusa MCP server

Ask Claude:

> "List my PrusaSlicer profiles"

A successful response lists profiles by name and category (print / filament / printer).

### Fusion MCP server

1. Confirm the add-in is running (Fusion toolbar should show the MCP icon).
2. Ask Claude:

   > "What components are in the active Fusion design?"

   Claude should return a list of component names from the open document.

### Bridge MCP server

Ask Claude:

> "Export the active Fusion component as STL, slice it with my default print profile, and tell me the estimated print time."

A successful response shows the export path, print time in hours/minutes, and filament usage.

---

## Troubleshooting common issues

### "PrusaSlicer binary not found"

- Verify PrusaSlicer is installed: `ls "/Applications/Original Prusa Drivers/PrusaSlicer.app"`
- Or add `prusa-slicer` to your `$PATH` by creating a symlink:
  ```bash
  sudo ln -s "/Applications/Original Prusa Drivers/PrusaSlicer.app/Contents/MacOS/PrusaSlicer" \
    /usr/local/bin/prusa-slicer
  ```

### "Profile not found: 'my-profile'"

- Export a fresh config bundle from PrusaSlicer: **File → Export → Export Config Bundle**
- Place the exported `.ini` at the repo root as `PrusaSlicer_config_bundle.ini`
- Confirm the profile name exactly matches what PrusaSlicer shows (case-sensitive)

### Fusion add-in HTTP server not reachable (port 8765)

- Check that the add-in is listed as **Running** in **Tools → Add-Ins → Scripts and Add-Ins**
- Look for errors in Fusion's **Text Commands** panel (open via **Tools → Text Commands**)
- Confirm nothing else is using port 8765: `lsof -i :8765`

### Claude doesn't show the MCP tools

- Verify the JSON in `claude_desktop_config.json` is valid (use `python3 -m json.tool < claude_desktop_config.json`)
- Check that the `command` paths are absolute and the virtual environment is activated for that path
- Restart Claude Desktop fully (quit from the menu bar icon, not just the window)

### Tests fail with import errors

```bash
# Make sure the venv is active and the package is installed in editable mode
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest prusa/tests/ bridge/tests/ -v
```
