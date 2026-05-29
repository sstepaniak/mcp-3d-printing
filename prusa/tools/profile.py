"""Profile discovery, reading, writing, and analysis for PrusaSlicer."""

from __future__ import annotations

import configparser
import re
import subprocess
from dataclasses import dataclass
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


# ─── tune_profile data ────────────────────────────────────────────────────────

@dataclass
class TuneResult:
    proposed_changes: dict[str, str]
    explanations: dict[str, str]
    new_profile_ini_content: str
    suggested_profile_name: str


# Known-good fallback values for settings that live in the printer or filament
# profile and therefore may be absent from a pure print profile.  These let
# fix_stringing / fix_under_extrusion etc. still emit useful changes even when
# called on a print-only base profile.
_SETTING_DEFAULTS: dict[str, str] = {
    "retract_before_travel":            "2",
    "retract_length":                   "0.8",
    "retract_speed":                    "40",
    "extrusion_multiplier":             "1.0",
    "max_fan_speed":                    "100",
    "first_layer_bed_temperature":      "55",
    "support_material_interface_layers": "3",
}


# Each entry in _TUNE_RULES:
#   setting -> (value_fn, explanation)
# value_fn(current: str | None) -> str | None
#   Return None to skip this setting (e.g. when it's absent and no default applies).
_TUNE_RULES: dict[str, dict[str, tuple]] = {

    "faster": {
        "layer_height": (
            lambda v: f"{min(float(v) * 1.5, 0.3):.2f}" if v else None,
            "Increased 50% to print fewer total layers; capped at 0.3 mm to maintain acceptable quality.",
        ),
        "perimeters": (
            lambda v: str(max(int(v) - 1, 1)) if v else None,
            "One fewer wall cuts perimeter print time; minimum of 1 is kept for structural integrity.",
        ),
        "fill_density": (
            lambda v: f"{max(int(v.rstrip('%')) - 5, 5)}%" if v else None,
            "Reduced 5 pp to trim infill time; slight reduction in compressive strength is the trade-off.",
        ),
        "infill_speed": (
            lambda v: str(min(int(float(v)) + 30, 200)) if v else None,
            "Faster infill has the least quality impact of any speed change; capped at 200 mm/s.",
        ),
        "top_solid_layers": (
            lambda v: str(max(int(v) - 1, 2)) if v else None,
            "One fewer top layer reduces finishing time; 2 layers minimum prevents infill show-through.",
        ),
    },

    "stronger": {
        "fill_density": (
            lambda v: f"{min(int(v.rstrip('%')) + 10, 60)}%" if v else None,
            "Increased 10 pp for greater structural strength and load resistance.",
        ),
        "perimeters": (
            lambda v: str(min(int(v) + 1, 6)) if v else None,
            "Extra perimeter wall significantly improves compressive strength and impact resistance.",
        ),
        "fill_pattern": (
            lambda v: "gyroid",
            "Gyroid infill is isotropic — equal strength in all load directions vs rectilinear.",
        ),
        "bottom_solid_layers": (
            lambda v: str(min(int(v) + 1, 8)) if v else None,
            "Extra bottom layer improves bed adhesion and base structural rigidity.",
        ),
        "top_solid_layers": (
            lambda v: str(min(int(v) + 1, 8)) if v else None,
            "Extra top solid layer fully caps the infill for a more solid top surface.",
        ),
    },

    "better_surface_quality": {
        "layer_height": (
            lambda v: f"{max(float(v) * 0.6, 0.05):.2f}" if v else None,
            "Reduced 40% for noticeably finer layer lines and a smoother visible surface.",
        ),
        "perimeters": (
            lambda v: str(min(int(v) + 1, 5)) if v else None,
            "Extra perimeter widens the shell, reducing infill pattern show-through on outer walls.",
        ),
        "ironing": (
            lambda v: "1",
            "Ironing re-traces the top surface at low flow to flatten and smooth it.",
        ),
        "external_perimeter_speed": (
            lambda v: str(max(int(float(v)) - 10, 15)) if v else None,
            "Slower outer wall gives the filament time to settle and bond cleanly to the previous layer.",
        ),
        "top_solid_layers": (
            lambda v: str(min(int(v) + 2, 8)) if v else None,
            "Extra top layers fully bury the infill pattern beneath the visible surface.",
        ),
    },

    "fix_stringing": {
        "retract_before_travel": (
            lambda v: "0.5",
            "Retract before any travel >0.5 mm; larger values skip short moves and allow ooze.",
        ),
        "retract_length": (
            lambda v: f"{min(float(v) + 0.5, 2.0):.1f}" if v else None,
            "Longer retraction pulls melt further back into the nozzle, reducing ooze during travel.",
        ),
        "retract_speed": (
            lambda v: str(min(int(float(v)) + 10, 70)) if v else None,
            "Faster retraction reduces the window where molten filament can ooze during pull-back.",
        ),
        "travel_speed": (
            lambda v: str(min(int(float(v)) + 30, 250)) if v else None,
            "Faster travel shortens the time between retract and unretract, limiting ooze distance.",
        ),
    },

    "fix_warping": {
        "brim_type": (
            lambda v: "outer_only",
            "An outer brim anchors base perimeters against corner lift during cooling.",
        ),
        "brim_width": (
            lambda v: f"{max(float(v), 8.0):.1f}" if v else "8.0",
            "8 mm brim provides sufficient contact area to hold corners down against thermal contraction.",
        ),
        "first_layer_speed": (
            lambda v: str(min(int(float(v)), 20)) if v else None,
            "Capping first layer speed at 20 mm/s maximises adhesion contact time with the bed.",
        ),
        "elefant_foot_compensation": (
            lambda v: v if float(v or 0) > 0 else "0.1",
            "Small compensation prevents the brim from fusing into the part perimeter under squish.",
        ),
    },

    "fix_layer_separation": {
        "layer_height": (
            lambda v: f"{max(float(v) * 0.8, 0.05):.2f}" if v else None,
            "Thinner layers increase the surface-area ratio and bonding contact between adjacent layers.",
        ),
        "perimeter_speed": (
            lambda v: str(max(int(float(v)) - 15, 20)) if v else None,
            "Slower perimeter speed gives each layer more dwell time to bond to the one below.",
        ),
        "external_perimeter_speed": (
            lambda v: str(max(int(float(v)) - 10, 15)) if v else None,
            "Slowing the outer wall reduces cooling speed and improves inter-layer fusion on the surface.",
        ),
    },

    "fix_elephant_foot": {
        "elefant_foot_compensation": (
            lambda v: f"{max(float(v) + 0.15, 0.2):.2f}" if v else "0.2",
            "Shrinks the first layer perimeter inward to counteract outward squish spread.",
        ),
        "first_layer_height": (
            lambda v: f"{max(float(v) - 0.05, 0.1):.2f}" if v else None,
            "Reducing first layer height lessens the over-squish that causes base flaring.",
        ),
        "first_layer_speed": (
            lambda v: str(max(int(float(v)) - 5, 10)) if v else None,
            "Slower first layer reduces the lateral nozzle pressure that pushes filament outward.",
        ),
    },

    "fix_under_extrusion": {
        "extrusion_multiplier": (
            lambda v: f"{min(float(v) + 0.05, 1.2):.2f}" if v else None,
            "Flow multiplier compensates directly for under-extrusion; increase in 0.05 steps.",
        ),
        "perimeter_speed": (
            lambda v: str(max(int(float(v)) - 20, 20)) if v else None,
            "Slower speed gives the extruder motor more time to push the required melt volume.",
        ),
        "infill_speed": (
            lambda v: str(max(int(float(v)) - 30, 30)) if v else None,
            "Reducing infill speed stops the hotend running faster than it can melt filament.",
        ),
    },

    "fix_over_extrusion": {
        "extrusion_multiplier": (
            lambda v: f"{max(float(v) - 0.05, 0.8):.2f}" if v else None,
            "Reducing flow multiplier by 0.05 corrects the excess volume being deposited per mm.",
        ),
        "external_perimeter_speed": (
            lambda v: str(min(int(float(v)) + 5, 60)) if v else None,
            "Slightly faster outer wall reduces nozzle dwell time and resulting blobbing.",
        ),
    },

    "fix_bridging": {
        "bridge_flow_ratio": (
            lambda v: f"{max(float(v) - 0.1, 0.7):.2f}" if v else None,
            "Reduced flow lowers the volume of sagging filament across unsupported spans.",
        ),
        "bridge_speed": (
            lambda v: str(max(int(float(v)) - 10, 15)) if v else None,
            "Slower bridge speed lets individual strands cool and stiffen before the next pass.",
        ),
        "max_fan_speed": (
            lambda v: "100",
            "Full fan speed during bridging maximises cooling to solidify strands mid-air.",
        ),
    },

    "fix_support_adhesion": {
        "support_material_contact_distance": (
            lambda v: f"{max(float(v) - 0.05, 0.05):.2f}" if v else None,
            "Smaller gap improves support-to-model adhesion, preventing separation mid-print.",
        ),
        "support_material_interface_layers": (
            lambda v: str(min(int(v) + 1, 5)) if v else None,
            "Extra interface layers create a denser, more stable contact surface between support and model.",
        ),
        "support_material_interface_spacing": (
            lambda v: "0",
            "Zero spacing creates a solid interface surface, maximising support adhesion.",
        ),
    },
}

# Ordered longest-first so "fix elephant foot" matches before bare "elephant foot".
_KEYWORD_MAP: list[tuple[str, str]] = [
    ("better surface quality",    "better_surface_quality"),
    ("surface quality",           "better_surface_quality"),
    ("surface finish",            "better_surface_quality"),
    ("fix layer separation",      "fix_layer_separation"),
    ("layer separation",          "fix_layer_separation"),
    ("fix elephant foot",         "fix_elephant_foot"),
    ("elephant foot",             "fix_elephant_foot"),
    ("fix under-extrusion",       "fix_under_extrusion"),
    ("fix under extrusion",       "fix_under_extrusion"),
    ("under-extrusion",           "fix_under_extrusion"),
    ("underextrusion",            "fix_under_extrusion"),
    ("fix over-extrusion",        "fix_over_extrusion"),
    ("fix over extrusion",        "fix_over_extrusion"),
    ("over-extrusion",            "fix_over_extrusion"),
    ("overextrusion",             "fix_over_extrusion"),
    ("fix support adhesion",      "fix_support_adhesion"),
    ("support adhesion",          "fix_support_adhesion"),
    ("fix stringing",             "fix_stringing"),
    ("stringing",                 "fix_stringing"),
    ("fix warping",               "fix_warping"),
    ("warping",                   "fix_warping"),
    ("fix bridging",              "fix_bridging"),
    ("bridging",                  "fix_bridging"),
    ("stronger",                  "stronger"),
    ("strength",                  "stronger"),
    ("faster",                    "faster"),
    ("speed up",                  "faster"),
]


def _detect_goals(text: str) -> list[str]:
    """Return an ordered, deduplicated list of goal keys matched in *text*."""
    low = text.lower()
    seen: set[str] = set()
    goals: list[str] = []
    for keyword, goal_key in _KEYWORD_MAP:
        if keyword in low and goal_key not in seen:
            seen.add(goal_key)
            goals.append(goal_key)
    return goals


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

    # ------------------------------------------------------------------
    # 6. tune_profile
    # ------------------------------------------------------------------

    def tune_profile(
        self,
        base_profile_name: str,
        goal_description: str,
        failure_description: str | None = None,
    ) -> TuneResult:
        """Return a TuneResult with proposed changes to reach *goal_description*.

        *failure_description* is parsed for the same set of failure modes and
        its fixes are merged on top of the goal changes (goals take priority on
        conflicting keys).  Nothing is written to disk — callers decide whether
        to pass *new_profile_ini_content* to write_profile().
        """
        base = self.read_profile(base_profile_name)

        goals = _detect_goals(goal_description)
        extra = _detect_goals(failure_description) if failure_description else []
        # Goals come first; extras fill in additional changes without overriding.
        all_goals = list(dict.fromkeys(goals + extra))

        proposed_changes: dict[str, str] = {}
        explanations: dict[str, str] = {}

        for goal_key in all_goals:
            for setting, (value_fn, explanation) in _TUNE_RULES.get(goal_key, {}).items():
                if setting in proposed_changes:
                    continue  # earlier goal already owns this key
                current = base.get(setting) or _SETTING_DEFAULTS.get(setting)
                try:
                    new_val = value_fn(current)
                except (TypeError, ValueError, AttributeError):
                    continue
                if new_val is None or new_val == base.get(setting):
                    continue  # no change or skipped
                proposed_changes[setting] = new_val
                explanations[setting] = explanation

        # Build merged settings: base overridden by proposed changes.
        merged = {**base, **proposed_changes}

        # Render .ini content (sectionless format, sorted keys).
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        goal_label = ", ".join(g.replace("_", " ") for g in all_goals)
        ini_lines = [f"# tuned by ProfileManager on {timestamp} UTC — {goal_label}\n"]
        for key in sorted(merged):
            ini_lines.append(f"{key} = {merged[key]}\n")
        ini_content = "".join(ini_lines)

        # Suggested name: strip trailing " - Copy" variants, append goal labels.
        base_clean = re.sub(r"\s*-\s*copy\s*$", "", base_profile_name, flags=re.IGNORECASE).strip()
        suffix = " + ".join(g.replace("_", " ") for g in all_goals)
        suggested_name = f"{base_clean} - {suffix}"

        return TuneResult(
            proposed_changes=proposed_changes,
            explanations=explanations,
            new_profile_ini_content=ini_content,
            suggested_profile_name=suggested_name,
        )
