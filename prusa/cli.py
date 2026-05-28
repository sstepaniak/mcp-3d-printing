import re
import shutil
import subprocess
from pathlib import Path

_FALLBACK_BINARY = (
    "/Applications/Original Prusa Drivers/"
    "PrusaSlicer.app/Contents/MacOS/PrusaSlicer"
)


class SlicerError(Exception):
    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class PrusaCLI:
    def __init__(self):
        self.binary = self._detect_binary()
        self.help_fff_text = self._run(["--help-fff"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_binary(self) -> str:
        if shutil.which("prusa-slicer"):
            return "prusa-slicer"
        fallback = Path(_FALLBACK_BINARY)
        if fallback.exists():
            return str(fallback)
        raise SlicerError(
            "PrusaSlicer binary not found. "
            "Expected 'prusa-slicer' on PATH or "
            f"'{_FALLBACK_BINARY}' on disk."
        )

    def _run(self, args: list[str]) -> str:
        """Run the binary with *args*; return combined stdout. Raise SlicerError on failure."""
        cmd = [self.binary, *args]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise SlicerError(
                f"PrusaSlicer exited with code {result.returncode}: {cmd}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        # PrusaSlicer occasionally writes useful output to stderr (e.g. progress);
        # combine so callers get the full picture.
        return (result.stdout + result.stderr).strip()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, args: list[str]) -> str:
        """Public wrapper — run the slicer with *args* and return output."""
        return self._run(args)

    @property
    def version(self) -> str:
        """Parse the version string from the cached --help-fff header."""
        match = re.search(r"PrusaSlicer-([\d.]+)", self.help_fff_text)
        if match:
            return match.group(1)
        # Fallback: ask the binary directly; --version is not a standard flag
        # so we extract from the first line of --help output instead.
        first_line = self.help_fff_text.splitlines()[0] if self.help_fff_text else ""
        return first_line.split()[0] if first_line else "unknown"
