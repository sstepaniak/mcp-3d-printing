"""Tests for prusa/cli.py — PrusaCLI binary detection and subprocess handling."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from prusa.cli import PrusaCLI, SlicerError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cli(mocker, *, binary: str = "prusa-slicer", help_text: str = "") -> PrusaCLI:
    """Build a PrusaCLI without hitting the filesystem or real binary."""
    mocker.patch("shutil.which", return_value=binary)
    mocker.patch.object(PrusaCLI, "_run", return_value=help_text)
    return PrusaCLI()


# ---------------------------------------------------------------------------
# Binary detection
# ---------------------------------------------------------------------------

class TestDetectBinary:
    def test_uses_path_binary_when_on_path(self, mocker):
        mocker.patch("shutil.which", return_value="prusa-slicer")
        mocker.patch.object(PrusaCLI, "_run", return_value="")
        cli = PrusaCLI()
        assert cli.binary == "prusa-slicer"

    def test_uses_fallback_when_not_on_path(self, mocker, tmp_path):
        fallback = tmp_path / "PrusaSlicer"
        fallback.touch()
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("prusa.cli._FALLBACK_BINARY", str(fallback))
        mocker.patch.object(PrusaCLI, "_run", return_value="")
        cli = PrusaCLI()
        assert cli.binary == str(fallback)

    def test_raises_slicer_error_when_not_found(self, mocker):
        mocker.patch("shutil.which", return_value=None)
        mocker.patch("prusa.cli.Path.exists", return_value=False)
        with pytest.raises(SlicerError, match="PrusaSlicer binary not found"):
            PrusaCLI()


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------

class TestVersionParsing:
    def test_parses_version_from_help_text(self, mocker):
        cli = _make_cli(mocker, help_text="PrusaSlicer-2.7.4+linux-x64-GTK3 based on Slic3r")
        assert cli.version == "2.7.4"

    def test_version_fallback_when_no_match(self, mocker):
        cli = _make_cli(mocker, help_text="Some other header line\nother content")
        # Falls back to first token of first line
        assert cli.version == "Some"

    def test_version_unknown_on_empty_help(self, mocker):
        cli = _make_cli(mocker, help_text="")
        assert cli.version == "unknown"


# ---------------------------------------------------------------------------
# SlicerError on non-zero exit
# ---------------------------------------------------------------------------

class TestRunErrors:
    def test_raises_slicer_error_on_nonzero_exit(self, mocker):
        mocker.patch("shutil.which", return_value="prusa-slicer")

        completed = MagicMock()
        completed.returncode = 1
        completed.stdout = "stdout content"
        completed.stderr = "stderr content"

        # Patch _run for the constructor call (--help-fff), then use real _run for second call
        mocker.patch.object(PrusaCLI, "_run", return_value="PrusaSlicer-2.7.4")
        cli = PrusaCLI()

        # Now restore the real _run and patch subprocess.run to return failure
        mocker.patch("subprocess.run", return_value=completed)
        cli_run_orig = PrusaCLI._run.__wrapped__ if hasattr(PrusaCLI._run, "__wrapped__") else None

        # Directly call _run by bypassing the mock
        with patch.object(PrusaCLI, "_run", PrusaCLI._run.__func__ if hasattr(PrusaCLI._run, "__func__") else PrusaCLI._run):
            pass  # reset not needed — just test via subprocess mock

        mocker.stopall()
        mocker.patch("shutil.which", return_value="prusa-slicer")
        mocker.patch("subprocess.run", return_value=completed)
        # Re-patch _run for __init__ only
        original_run = PrusaCLI._run
        call_count = {"n": 0}

        def patched_run(self, args):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "PrusaSlicer-2.7.4"
            return original_run(self, args)

        mocker.patch.object(PrusaCLI, "_run", patched_run)
        cli2 = PrusaCLI()
        with pytest.raises(SlicerError) as exc_info:
            cli2._run(["--slice", "nonexistent.stl"])
        err = exc_info.value
        assert err.stdout == "stdout content"
        assert err.stderr == "stderr content"
        assert "exited with code 1" in str(err)

    def test_slicer_error_stores_stdout_stderr(self):
        err = SlicerError("msg", stdout="out", stderr="err")
        assert err.stdout == "out"
        assert err.stderr == "err"
        assert str(err) == "msg"

    def test_successful_run_returns_combined_output(self, mocker):
        mocker.patch("shutil.which", return_value="prusa-slicer")
        completed_help = MagicMock(returncode=0, stdout="PrusaSlicer-2.7.4\n", stderr="")
        completed_extra = MagicMock(returncode=0, stdout="hello ", stderr="world")
        mocker.patch("subprocess.run", side_effect=[completed_help, completed_extra])
        cli = PrusaCLI()
        result = cli._run(["--version"])
        assert result == "hello world"
