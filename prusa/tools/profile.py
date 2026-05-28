"""Profile discovery, reading, writing, and analysis for PrusaSlicer."""

from __future__ import annotations

import configparser
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_CONFIG_BUNDLE = Path("/Users/admin/mcp-3d-printing/PrusaSlicer_config_bundle.ini")
_LOCAL_PRUSA_DIR = Path.home() / "Library/Application Support/PrusaSlicer"
_PROFILES_DIR = Path(__file__).parent.parent / "profiles"
_REPO_ROOT = Path(__file__).parent.parent.parent

_CATEGORY_DIRS = ("print", "filament", "printer")


# ─── INI parsing helpers ───────────────────────────────────────────────────────

def _parse_sectionless(path: Path) -> dict[str, str]:
    """Parse a PrusaSlicer per-profile .ini that has no [section] header."""
    cp = configparser.RawConfigParser()
    cp.read_string("[_]\n" + path.read_text(encoding="utf-8", errors="replace"))
    return dict(cp["_"])


def _parse_bundle(path: Path) -> list[dict]:
    """Return all print/filament/printer entries from a bundle .ini."""
    cp = configparser.RawConfigParser()
    cp.read(str(path), encoding="utf-8")
    profiles = []
    for section in cp.sections():
        if ":" not in section:
            continue
        category, name = section.split(":", 1)
        if category in _CATEGORY_DIRS:
            profiles.append(
                {"name": name, "category": category, "source_path": str(path), "_settings": dict(cp[section])}
            )
    return profiles


# ─── explain_profile metadata ──────────────────────────────────────────────────

# (setting_key) -> (group, human label)
_SETTING_META: dict[str, tuple[str, str]] = {
    # quality
    "layer_height":                     ("quality",   "Layer height"),
    "ironing":                          ("quality",   "Top surface ironing"),
    "gap_fill_enabled":                 ("quality",   "Gap fill"),
    "gcode_resolution":                 ("quality",   "G-code curve resolution"),
    "external_perimeter_extrusion_width": ("quality", "Outer wall extrusion width"),
    # speed
    "perimeter_speed":                  ("speed",     "Perimeter speed"),
    "external_perimeter_speed":         ("speed",     "Outer perimeter speed"),
    "infill_speed":                     ("speed",     "Infill speed"),
    "first_layer_speed":                ("speed",     "First layer speed"),
    "bridge_speed":                     ("speed",     "Bridge speed"),
    "travel_speed":                     ("speed",     "Travel speed"),
    # strength
    "fill_density":                     ("strength",  "Infill density"),
    "fill_pattern":                     ("strength",  "Infill pattern"),
    "perimeters":                       ("strength",  "Perimeter count"),
    "bottom_solid_layers":              ("strength",  "Bottom solid layers"),
    "top_solid_layers":                 ("strength",  "Top solid layers"),
    # thermal  (mostly filament profiles)
    "temperature":                      ("thermal",   "Nozzle temp"),
    "first_layer_temperature":          ("thermal",   "First layer nozzle temp"),
    "bed_temperature":                  ("thermal",   "Bed temp"),
    "first_layer_bed_temperature":      ("thermal",   "First layer bed temp"),
    "cooling":                          ("thermal",   "Fan cooling"),
    "min_fan_speed":                    ("thermal",   "Min fan speed"),
    "max_fan_speed":                    ("thermal",   "Max fan speed"),
    "disable_fan_first_layers":         ("thermal",   "Disable fan first N layers"),
    # support
    "support_material":                 ("support",   "Support material"),
    "support_material_style":           ("support",   "Support style"),
    "support_material_threshold":       ("support",   "Overhang threshold (°)"),
    "support_material_spacing":         ("support",   "Support spacing"),
    # special
    "fuzzy_skin":                       ("special",   "Fuzzy skin"),
    "brim_type":                        ("special",   "Brim type"),
    "brim_width":                       ("special",   "Brim width"),
    "elefant_foot_compensation":        ("special",   "Elephant foot compensation"),
    "complete_objects":                 ("special",   "Sequential printing"),
    "draft_shield":                     ("special",   "Draft shield"),
}

# (key, predicate, warning message)  — predicate receives the raw string value
_THRESHOLDS: list[tuple[str, object, str]] = [
    ("layer_height",             lambda v: float(v) < 0.1,
     f"Very fine layer height — print times will be dramatically longer"),
    ("layer_height",             lambda v: float(v) >= 0.3,
     "Coarse layer height (≥0.3 mm) — fast prints but visible layer lines"),
    ("fill_density",             lambda v: float(v.rstrip("%")) < 10,
     "Very low infill (<10%) — parts may be fragile under load"),
    ("fill_density",             lambda v: float(v.rstrip("%")) >= 40,
     "High infill (≥40%) — strong but slow; heavy on filament"),
    ("first_layer_speed",        lambda v: float(v) > 30,
     "First layer speed >30 mm/s — may compromise bed adhesion"),
    ("external_perimeter_speed", lambda v: float(v) > 50,
     "Outer perimeter speed >50 mm/s — may visibly affect surface quality"),
    ("perimeters",               lambda v: int(v) < 2,
     "Only 1 perimeter — very thin walls, structurally weak"),
    ("perimeters",               lambda v: int(v) >= 5,
     "5+ perimeters — very thick walls; strong but slow"),
    ("bridge_flow_ratio",        lambda v: float(v) < 0.8,
     "Low bridge flow ratio (<0.8) — conservative, reduces sagging risk"),
    ("bridge_flow_ratio",        lambda v: float(v) > 1.05,
     "High bridge flow ratio (>1.05) — aggressive; bridged spans may sag"),
    ("temperature",              lambda v: float(v) > 0 and float(v) > 250,
     "Nozzle temp >250 °C — verify material supports this; heat-creep risk"),
    ("infill_speed",             lambda v: float(v) > 150,
     "Infill speed >150 mm/s — verify printer motion system can keep up"),
]


# ─── ProfileManager ────────────────────────────────────────────────────────────

class ProfileManager:
    def __init__(
        self,
        config_bundle: Path = _CONFIG_BUNDLE,
        local_dir: Path = _LOCAL_PRUSA_DIR,
        profiles_dir: Path = _PROFILES_DIR,
    ):
        self._bundle = config_bundle
        self._local_dir = local_dir
        self._profiles_dir = profiles_dir

    # ------------------------------------------------------------------
    # 1. list_profiles
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[dict[str, str]]:
        """Return every profile from the config bundle and local AppSupport dirs."""
        profiles: list[dict[str, str]] = []

        if self._bundle.exists():
            for entry in _parse_bundle(self._bundle):
                profiles.append(
                    {"name": entry["name"], "category": entry["category"], "source_path": entry["source_path"]}
                )

        for category in _CATEGORY_DIRS:
            cat_dir = self._local_dir / category
            if not cat_dir.exists():
                continue
            for ini_path in sorted(cat_dir.glob("*.ini")):
                profiles.append(
                    {"name": ini_path.stem, "category": category, "source_path": str(ini_path)}
                )

        return profiles

    # ------------------------------------------------------------------
    # 2. read_profile
    # ------------------------------------------------------------------

    def read_profile(self, name: str) -> dict[str, str]:
        """Return the key-value settings for a named profile.

        Raises KeyError if the profile cannot be found in any source.
        """
        # Bundle first (user-exported profiles live here)
        if self._bundle.exists():
            cp = configparser.RawConfigParser()
            cp.read(str(self._bundle), encoding="utf-8")
            for section in cp.sections():
                if ":" in section:
                    _, sname = section.split(":", 1)
                    if sname == name:
                        return dict(cp[section])

        # Then per-category AppSupport files
        for category in _CATEGORY_DIRS:
            path = self._local_dir / category / f"{name}.ini"
            if path.exists():
                return _parse_sectionless(path)

        raise KeyError(f"Profile not found: {name!r}")

    # ------------------------------------------------------------------
    # 3. write_profile
    # ------------------------------------------------------------------

    def write_profile(self, name: str, settings: dict[str, str]) -> Path:
        """Save *settings* as a .ini file under prusa/profiles/ and git-commit it.

        Returns the path of the written file.
        """
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-. ]", "_", name)
        out_path = self._profiles_dir / f"{safe_name}.ini"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

        lines = [f"# written by ProfileManager on {timestamp} UTC\n"]
        for key in sorted(settings):
            lines.append(f"{key} = {settings[key]}\n")
        out_path.write_text("".join(lines), encoding="utf-8")

        # Commit in the submodule if it has its own git; otherwise the main repo.
        git_dir = self._profiles_dir / ".git"
        repo_cwd = str(self._profiles_dir if git_dir.exists() else _REPO_ROOT)
        subprocess.run(["git", "add", str(out_path)], cwd=repo_cwd, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"profile: update {name} - {timestamp}"],
            cwd=repo_cwd,
            check=True,
        )
        return out_path

    # ------------------------------------------------------------------
    # 4. compare_profiles
    # ------------------------------------------------------------------

    def compare_profiles(self, name_a: str, name_b: str) -> dict[str, dict[str, str]]:
        """Return only the keys that differ between two profiles, with both values."""
        a = self.read_profile(name_a)
        b = self.read_profile(name_b)
        diffs: dict[str, dict[str, str]] = {}
        for key in sorted(set(a) | set(b)):
            va, vb = a.get(key), b.get(key)
            if va != vb:
                diffs[key] = {name_a: va if va is not None else "(not set)",
                              name_b: vb if vb is not None else "(not set)"}
        return diffs

    # ------------------------------------------------------------------
    # 5. explain_profile
    # ------------------------------------------------------------------

    def explain_profile(self, name: str) -> str:
        """Return a plain-English summary grouped by category, with anomaly flags."""
        settings = self.read_profile(name)
        groups: dict[str, list[str]] = {g: [] for g in ("quality", "speed", "strength", "thermal", "support", "special")}
        flags: list[str] = []

        for key, (group, label) in _SETTING_META.items():
            raw = settings.get(key)
            if raw is None or raw == "":
                continue

            # Format the value with units / context
            try:
                if key == "layer_height":
                    v = float(raw)
                    tier = (
                        "ultra-fine" if v <= 0.05
                        else "fine" if v <= 0.1
                        else "standard" if v <= 0.2
                        else "coarse"
                    )
                    line = f"{label}: {raw} mm  ({tier})"
                elif key in ("min_fan_speed", "max_fan_speed", "bridge_fan_speed"):
                    line = f"{label}: {raw}%"
                elif key.endswith("_speed") and raw not in ("0", "nil"):
                    line = f"{label}: {float(raw):.0f} mm/s"
                elif key in ("temperature", "first_layer_temperature",
                             "bed_temperature", "first_layer_bed_temperature"):
                    v = float(raw)
                    if v <= 0:
                        continue
                    line = f"{label}: {v:.0f} °C"
                elif key in ("cooling", "ironing", "gap_fill_enabled",
                             "support_material", "complete_objects"):
                    line = f"{label}: {'enabled' if raw == '1' else 'disabled'}"
                elif key in ("min_fan_speed", "max_fan_speed", "bridge_fan_speed"):
                    line = f"{label}: {raw}%"
                elif key.endswith("_layers") or key == "perimeters":
                    line = f"{label}: {raw}"
                else:
                    line = f"{label}: {raw}"
            except (ValueError, TypeError):
                line = f"{label}: {raw}"

            groups[group].append(line)

        # Collect anomaly flags
        for key, predicate, message in _THRESHOLDS:
            raw = settings.get(key)
            if raw is None or raw in ("", "nil"):
                continue
            try:
                if predicate(raw):
                    flags.append(f"  ! {message}")
            except (ValueError, TypeError):
                pass

        # Render
        parts = [f"Profile: {name}", "=" * (9 + len(name))]
        for group_name, items in groups.items():
            if items:
                parts.append(f"\n{group_name.upper()}:")
                parts.extend(f"  {item}" for item in items)
        if flags:
            parts.append("\nFLAGS:")
            parts.extend(flags)
        else:
            parts.append("\n(no unusual settings flagged)")
        return "\n".join(parts)
