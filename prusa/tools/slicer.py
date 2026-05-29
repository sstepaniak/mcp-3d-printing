"""Slice STL files via PrusaSlicer CLI and parse G-code diagnostics."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from prusa.cli import PrusaCLI
from prusa.tools.profile import ProfileManager

# Keys that crash PrusaSlicer 2.9.5 CLI when passed via --load.
# wipe_tower_extruder=0 causes a SIGSEGV; the rest are meta/relational fields
# that only make sense inside the GUI's profile database.
_SKIP_KEYS = frozenset({
    "compatible_printers",
    "compatible_printers_condition",
    "compatible_prints",
    "compatible_prints_condition",
    "inherits",
    "custom_parameters_print",
    "gcode_substitutions",
    "notes",
    "wipe_tower_extruder",
    "bed_temperature_extruder",
})


# ─── result dataclasses ───────────────────────────────────────────────────────

@dataclass
class SliceResult:
    print_time_seconds: int
    filament_used_mm: float
    filament_used_g: float
    estimated_cost: float
    layer_count: int
    output_gcode_path: str


@dataclass
class DiagnosticsResult:
    max_overhang_angle: float
    bridge_count: int
    support_volume_mm3: float
    thin_wall_warnings: list[str]
    estimated_weak_layer_count: int
    # Populated from the embedded config block in the gcode; used by suggest_fixes.
    _settings: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass
class FixSuggestion:
    setting: str
    current_value: str
    recommended_value: str
    reason: str


# ─── time parsing ─────────────────────────────────────────────────────────────

def _parse_time(s: str) -> int:
    """Convert a PrusaSlicer time string to seconds.  '1h 23m 45s' → 5025."""
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([hms])", s):
        v = int(value)
        if unit == "h":
            total += v * 3600
        elif unit == "m":
            total += v * 60
        else:
            total += v
    return total


# ─── gcode parsing helpers ────────────────────────────────────────────────────

def _parse_gcode_comments(gcode_path: Path) -> dict[str, str]:
    """Return every 'key = value' comment line from the gcode as a flat dict.

    Covers both the runtime stats block and the embedded prusaslicer_config dump.
    """
    result: dict[str, str] = {}
    for line in gcode_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(";"):
            continue
        body = line[1:].strip()
        if " = " in body:
            key, _, val = body.partition(" = ")
            result[key.strip()] = val.strip()
    return result


def _count_type_sections(text: str, type_name: str) -> int:
    return text.count(f";TYPE:{type_name}")


def _layers_containing_type(text: str, type_name: str) -> set[int]:
    """Return 0-based layer indices that contain the given ;TYPE: marker."""
    layers: set[int] = set()
    layer = -1
    marker = f";TYPE:{type_name}"
    for line in text.splitlines():
        if line == ";LAYER_CHANGE":
            layer += 1
        elif line == marker and layer >= 0:
            layers.add(layer)
    return layers


def _estimate_support_volume_mm3(text: str) -> float:
    """Estimate support material volume from extruded length in support sections.

    Uses 1.75 mm filament cross-section area (≈ 2.405 mm²) as the conversion factor.
    """
    in_support = False
    total_e = 0.0
    last_e = 0.0

    for line in text.splitlines():
        if line.startswith(";TYPE:"):
            in_support = "Support material" in line
            continue
        if not in_support:
            continue
        m = re.match(r"G1\s[^E]*E([\d.]+)", line)
        if m:
            e = float(m.group(1))
            if e > last_e:
                total_e += e - last_e
            last_e = e
        elif line.startswith("G92 E0"):
            last_e = 0.0

    return round(total_e * 2.405, 2)


# ─── fix rules ────────────────────────────────────────────────────────────────

# Each entry: (predicate, setting, recommended_value, reason)
_FIX_RULES: list[tuple] = [
    (
        lambda d: d.bridge_count > 3,
        "bridge_flow_ratio", "0.9",
        "Multiple bridge spans detected; reducing flow ratio to 0.9 prevents sagging on unsupported areas.",
    ),
    (
        lambda d: d.bridge_count > 3,
        "bridge_speed", "20",
        "Slowing bridge speed gives the filament more time to cool, improving span quality over open gaps.",
    ),
    (
        lambda d: d.max_overhang_angle > 50,
        "support_material", "1",
        "Overhangs exceed 50° — enabling supports prevents layer drooping and inter-layer delamination.",
    ),
    (
        lambda d: 0 < d.max_overhang_angle <= 45,
        "support_material_threshold", "55",
        "Threshold is aggressive; raising it to 55° removes supports on shallow overhangs that print cleanly.",
    ),
    (
        lambda d: bool(d.thin_wall_warnings),
        "thin_walls", "1",
        "Thin wall segments detected; enabling thin_walls mode lets PrusaSlicer adapt perimeter widths to fit.",
    ),
    (
        lambda d: d.estimated_weak_layer_count > 5,
        "external_perimeter_speed", "25",
        "Many overhang layers present; slowing outer perimeter speed improves layer fusion on steep angles.",
    ),
    (
        lambda d: d.support_volume_mm3 > 500,
        "support_material_style", "snug",
        "Large support volume; snug style generates tighter contact geometry, reducing material and removal effort.",
    ),
    (
        lambda d: d.support_volume_mm3 > 2000,
        "support_material_spacing", "3.0",
        "Very large support volume; increasing interface spacing significantly cuts print time and filament use.",
    ),
]


# ─── Slicer ───────────────────────────────────────────────────────────────────

class Slicer:
    def __init__(
        self,
        cli: Optional[PrusaCLI] = None,
        profile_manager: Optional[ProfileManager] = None,
    ):
        self._cli = cli or PrusaCLI()
        self._pm = profile_manager or ProfileManager()

    # ------------------------------------------------------------------
    # 1. slice_file
    # ------------------------------------------------------------------

    def slice_file(
        self,
        stl_path: str | Path,
        profile_name: str,
        overrides_dict: Optional[dict[str, str]] = None,
    ) -> SliceResult:
        """Slice *stl_path* using *profile_name* and return parsed stats.

        Optional *overrides_dict* applies per-call setting overrides on top of
        the named profile before slicing.  Output gcode is placed next to the
        STL with a .gcode extension.
        """
        stl_path = Path(stl_path).resolve()
        out_gcode = stl_path.with_suffix(".gcode")

        settings = self._pm.read_profile(profile_name)
        if overrides_dict:
            settings.update(overrides_dict)

        # Write a temp .ini, stripping keys known to crash the CLI binary.
        with tempfile.NamedTemporaryFile(
            suffix=".ini", mode="w", delete=False, encoding="utf-8"
        ) as f:
            tmp_ini = Path(f.name)
            for key in sorted(settings):
                if key not in _SKIP_KEYS:
                    f.write(f"{key} = {settings[key]}\n")

        try:
            self._cli.run([
                "--load", str(tmp_ini),
                "--slice",
                "--output", str(out_gcode),
                str(stl_path),
            ])
        finally:
            tmp_ini.unlink(missing_ok=True)

        return self._parse_slice_result(out_gcode)

    def _parse_slice_result(self, gcode_path: Path) -> SliceResult:
        stats = _parse_gcode_comments(gcode_path)
        text = gcode_path.read_text(encoding="utf-8", errors="replace")

        mm = float(stats.get("filament used [mm]", 0))

        # PrusaSlicer writes 'filament used [g]' only when filament_density is set;
        # fall back to 'total filament used [g]', then compute from cm³ × density.
        g_str = stats.get("filament used [g]") or stats.get("total filament used [g]", "0")
        g = float(g_str)
        if g == 0:
            cm3 = float(stats.get("filament used [cm3]", 0))
            density = float(stats.get("filament_density", 1.24))
            g = cm3 * density

        cost = float(stats.get("total filament cost", 0))
        seconds = _parse_time(stats.get("estimated printing time (normal mode)", "0s"))
        layer_count = text.count(";LAYER_CHANGE")

        return SliceResult(
            print_time_seconds=seconds,
            filament_used_mm=mm,
            filament_used_g=round(g, 3),
            estimated_cost=round(cost, 4),
            layer_count=layer_count,
            output_gcode_path=str(gcode_path),
        )

    # ------------------------------------------------------------------
    # 2. parse_diagnostics
    # ------------------------------------------------------------------

    def parse_diagnostics(self, gcode_path: str | Path) -> DiagnosticsResult:
        """Read a sliced gcode file and return structural diagnostic information."""
        gcode_path = Path(gcode_path)
        text = gcode_path.read_text(encoding="utf-8", errors="replace")
        settings = _parse_gcode_comments(gcode_path)

        # Overhang angle: use the slicer's configured support threshold as a proxy.
        # A threshold of N means the slicer considered N° the worst expected overhang.
        max_overhang = float(settings.get("support_material_threshold", 0))

        bridge_count = _count_type_sections(text, "Bridge infill")
        support_volume = _estimate_support_volume_mm3(text)

        thin_wall_layers = _layers_containing_type(text, "Thin wall")
        thin_wall_warnings = [
            f"Thin wall segment present in layer {layer}" for layer in sorted(thin_wall_layers)
        ]

        weak_layers = _layers_containing_type(text, "Overhang perimeter")

        return DiagnosticsResult(
            max_overhang_angle=max_overhang,
            bridge_count=bridge_count,
            support_volume_mm3=support_volume,
            thin_wall_warnings=thin_wall_warnings,
            estimated_weak_layer_count=len(weak_layers),
            _settings=settings,
        )

    # ------------------------------------------------------------------
    # 3. suggest_fixes
    # ------------------------------------------------------------------

    def suggest_fixes(self, diagnostics: DiagnosticsResult) -> list[FixSuggestion]:
        """Map each diagnostic warning to a concrete setting change with a reason."""
        suggestions: list[FixSuggestion] = []
        for predicate, setting, recommended, reason in _FIX_RULES:
            try:
                if predicate(diagnostics):
                    current = diagnostics._settings.get(setting, "unknown")
                    suggestions.append(
                        FixSuggestion(
                            setting=setting,
                            current_value=current,
                            recommended_value=recommended,
                            reason=reason,
                        )
                    )
            except (TypeError, ValueError):
                pass
        return suggestions
