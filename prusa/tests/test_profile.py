"""Tests for prusa/tools/profile.py — ProfileManager and TuneResult."""

from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prusa.tools.profile import ProfileManager, TuneResult, _detect_goals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_SETTINGS = {
    "layer_height": "0.20",
    "fill_density": "15%",
    "perimeters": "3",
    "infill_speed": "80",
    "perimeter_speed": "45",
    "external_perimeter_speed": "25",
    "travel_speed": "150",
    "first_layer_speed": "20",
    "top_solid_layers": "5",
    "bottom_solid_layers": "4",
    "retract_length": "0.8",
    "retract_speed": "40",
    "retract_before_travel": "2",
    "brim_width": "5.0",
    "brim_type": "no_brim",
    "elefant_foot_compensation": "0.0",
    "extrusion_multiplier": "1.0",
}


def _make_bundle(path: Path, name: str = "0.20mm QUALITY", settings: dict | None = None) -> None:
    """Write a minimal config bundle .ini with one print profile."""
    s = settings or _SAMPLE_SETTINGS
    cp = configparser.RawConfigParser()
    section = f"print:{name}"
    cp[section] = s
    with path.open("w", encoding="utf-8") as f:
        cp.write(f)


def _make_manager(tmp_path: Path, *, bundle_name: str = "0.20mm QUALITY") -> tuple[ProfileManager, Path]:
    bundle = tmp_path / "bundle.ini"
    _make_bundle(bundle, bundle_name)
    pm = ProfileManager(
        config_bundle=bundle,
        local_dir=tmp_path / "local",   # won't exist — that's fine
        profiles_dir=tmp_path / "profiles",
    )
    return pm, bundle


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------

class TestListProfiles:
    def test_returns_non_empty_list(self, tmp_path):
        pm, _ = _make_manager(tmp_path)
        profiles = pm.list_profiles()
        assert len(profiles) > 0

    def test_each_entry_has_required_keys(self, tmp_path):
        pm, _ = _make_manager(tmp_path)
        for p in pm.list_profiles():
            assert "name" in p
            assert "category" in p
            assert "source_path" in p

    def test_local_ini_files_are_included(self, tmp_path):
        pm, _ = _make_manager(tmp_path)
        print_dir = tmp_path / "local" / "print"
        print_dir.mkdir(parents=True)
        (print_dir / "myprofile.ini").write_text("layer_height = 0.1\n")
        # Re-create manager pointing at tmp local dir
        pm2 = ProfileManager(
            config_bundle=tmp_path / "bundle.ini",
            local_dir=tmp_path / "local",
            profiles_dir=tmp_path / "profiles",
        )
        names = [p["name"] for p in pm2.list_profiles()]
        assert "myprofile" in names


# ---------------------------------------------------------------------------
# read_profile / write_profile round-trip
# ---------------------------------------------------------------------------

class TestReadWriteProfile:
    def test_read_returns_settings_dict(self, tmp_path):
        pm, _ = _make_manager(tmp_path, bundle_name="0.20mm QUALITY")
        settings = pm.read_profile("0.20mm QUALITY")
        assert isinstance(settings, dict)
        assert "layer_height" in settings

    def test_raises_key_error_for_unknown_profile(self, tmp_path):
        pm, _ = _make_manager(tmp_path)
        with pytest.raises(KeyError):
            pm.read_profile("does not exist")

    def test_write_then_read_roundtrip(self, tmp_path):
        pm, _ = _make_manager(tmp_path)
        profiles_dir = tmp_path / "profiles"

        # Patch subprocess.run so the git commit in write_profile doesn't fail
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            saved_path = pm.write_profile("test-roundtrip", {"layer_height": "0.15", "fill_density": "20%"})

        assert saved_path.exists()
        text = saved_path.read_text()
        assert "layer_height = 0.15" in text
        assert "fill_density = 20%" in text

    def test_written_file_is_sectionless(self, tmp_path):
        pm, _ = _make_manager(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            saved_path = pm.write_profile("sectionless-test", {"key": "value"})
        lines = saved_path.read_text().splitlines()
        # No [section] header line
        assert not any(line.strip().startswith("[") for line in lines)


# ---------------------------------------------------------------------------
# compare_profiles
# ---------------------------------------------------------------------------

class TestCompareProfiles:
    def _make_two_profiles(self, tmp_path) -> ProfileManager:
        bundle = tmp_path / "bundle.ini"
        cp = configparser.RawConfigParser()
        cp["print:profile_a"] = {"layer_height": "0.20", "fill_density": "15%", "perimeters": "3"}
        cp["print:profile_b"] = {"layer_height": "0.10", "fill_density": "15%", "perimeters": "4"}
        with bundle.open("w") as f:
            cp.write(f)
        return ProfileManager(
            config_bundle=bundle,
            local_dir=tmp_path / "local",
            profiles_dir=tmp_path / "profiles",
        )

    def test_returns_non_empty_diff(self, tmp_path):
        pm = self._make_two_profiles(tmp_path)
        diffs = pm.compare_profiles("profile_a", "profile_b")
        assert len(diffs) > 0

    def test_diff_keys_are_actually_different(self, tmp_path):
        pm = self._make_two_profiles(tmp_path)
        diffs = pm.compare_profiles("profile_a", "profile_b")
        # layer_height and perimeters differ; fill_density is the same
        assert "layer_height" in diffs
        assert "perimeters" in diffs
        assert "fill_density" not in diffs

    def test_diff_contains_both_values(self, tmp_path):
        pm = self._make_two_profiles(tmp_path)
        diffs = pm.compare_profiles("profile_a", "profile_b")
        assert diffs["layer_height"]["profile_a"] == "0.20"
        assert diffs["layer_height"]["profile_b"] == "0.10"

    def test_identical_profiles_return_empty_diff(self, tmp_path):
        pm = self._make_two_profiles(tmp_path)
        # Compare profile_a with itself
        diffs = pm.compare_profiles("profile_a", "profile_a")
        assert diffs == {}


# ---------------------------------------------------------------------------
# tune_profile
# ---------------------------------------------------------------------------

class TestTuneProfile:
    def _pm_with_sample(self, tmp_path) -> ProfileManager:
        pm, _ = _make_manager(tmp_path, bundle_name="base")
        return pm

    def test_fix_stringing_returns_non_empty_changes(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "fix stringing")
        assert isinstance(result, TuneResult)
        assert len(result.proposed_changes) > 0
        assert len(result.explanations) > 0

    def test_fix_warping_returns_non_empty_changes(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "fix warping")
        assert len(result.proposed_changes) > 0
        # brim_type must be proposed
        assert "brim_type" in result.proposed_changes

    def test_faster_returns_non_empty_changes(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "faster")
        assert len(result.proposed_changes) > 0
        assert "layer_height" in result.proposed_changes

    def test_stronger_returns_non_empty_changes(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "stronger")
        assert len(result.proposed_changes) > 0
        assert "fill_density" in result.proposed_changes

    def test_proposed_changes_keys_match_explanations(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "faster")
        assert set(result.proposed_changes.keys()) == set(result.explanations.keys())

    def test_ini_content_contains_all_base_keys(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "fix stringing")
        for key in _SAMPLE_SETTINGS:
            assert f"{key} = " in result.new_profile_ini_content

    def test_suggested_name_contains_goal(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "fix stringing")
        assert "fix stringing" in result.suggested_profile_name

    def test_failure_description_merged_with_goal(self, tmp_path):
        pm = self._pm_with_sample(tmp_path)
        result = pm.tune_profile("base", "faster", failure_description="also stringing everywhere")
        # Both goals should produce changes
        assert len(result.proposed_changes) > 0
        assert "layer_height" in result.proposed_changes  # from "faster"


# ---------------------------------------------------------------------------
# _detect_goals (keyword matching)
# ---------------------------------------------------------------------------

class TestDetectGoals:
    def test_exact_keyword(self):
        goals = _detect_goals("fix stringing")
        assert "fix_stringing" in goals

    def test_case_insensitive(self):
        goals = _detect_goals("Fix Warping please")
        assert "fix_warping" in goals

    def test_no_duplicates(self):
        goals = _detect_goals("stringing stringing stringing")
        assert goals.count("fix_stringing") == 1

    def test_unrecognised_text_returns_empty(self):
        goals = _detect_goals("everything looks fine")
        assert goals == []
