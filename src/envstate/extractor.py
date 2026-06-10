from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

ProbeExecutor = Callable[[str], Tuple[int, str]]

# field_name -> read-only command (design §12 extractor list, V1 subset)
EXTRACTOR_COMMANDS: Dict[str, str] = {
    "os_release": "cat /etc/os-release",
    "arch": "uname -m",
    "python_version": "python --version 2>&1",
    "pip_version": "pip --version 2>&1",
    "path": "echo \"$PATH\"",
    "which_python": "command -v python",
    "venv": "echo \"${VIRTUAL_ENV:-}\"",
    "installed_pip": "pip list --format=freeze 2>/dev/null",
    "dpkg_packages": "dpkg -l 2>/dev/null | awk '/^ii/{print $2}'",
    "pkg_config_modules": "pkg-config --list-all 2>/dev/null",
}

# Cheap subset re-run after every env mutation (design §12 run schedule).
LIGHTWEIGHT_FIELDS = ("python_version", "pip_version", "installed_pip", "arch")


@dataclass(frozen=True)
class ExtractionResult:
    fields: Dict[str, str]            # successfully-read field -> trimmed stdout
    raw: Dict[str, Tuple[int, str]]   # field -> (rc, raw stdout) for every attempted command


def run_extractor(
    executor: ProbeExecutor, fields: Optional[Tuple[str, ...]] = None
) -> ExtractionResult:
    names = fields if fields is not None else tuple(EXTRACTOR_COMMANDS.keys())
    parsed: Dict[str, str] = {}
    raw: Dict[str, Tuple[int, str]] = {}
    for name in names:
        command = EXTRACTOR_COMMANDS[name]
        rc, stdout = executor(command)
        raw[name] = (rc, stdout)
        if rc == 0 and stdout.strip():
            parsed[name] = stdout.strip()
    return ExtractionResult(fields=parsed, raw=raw)
