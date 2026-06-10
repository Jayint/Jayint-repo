import os
import platform
import sys
from typing import Dict


class PlanningHostEnvironmentProbe:
    """Collect non-sensitive facts about the host running the planning agent."""

    def collect(self, target_platform: str = "linux") -> Dict[str, object]:
        os_name = platform.system() or "Unknown"
        machine = platform.machine() or "unknown"
        normalized_os = self._normalize_os(os_name)
        normalized_arch = self._normalize_arch(machine)

        return {
            "role": "planning_host",
            "os_name": os_name,
            "normalized_os": normalized_os,
            "machine": machine,
            "normalized_arch": normalized_arch,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "target_platform": target_platform,
            "docker_default_platform_env": os.environ.get("DOCKER_DEFAULT_PLATFORM"),
            "inside_container": self._looks_inside_container(),
            "host_is_apple_silicon": normalized_os == "macos" and normalized_arch == "arm64",
            "notes": self._notes(normalized_os, normalized_arch, target_platform),
        }

    def _normalize_os(self, os_name: str) -> str:
        lowered = (os_name or "").lower()
        if lowered == "darwin":
            return "macos"
        if lowered.startswith("win"):
            return "windows"
        if lowered == "linux":
            return "linux"
        return lowered or "unknown"

    def _normalize_arch(self, machine: str) -> str:
        lowered = (machine or "").lower()
        if lowered in {"arm64", "aarch64"}:
            return "arm64"
        if lowered in {"x86_64", "amd64"}:
            return "amd64"
        if lowered in {"i386", "i686", "x86"}:
            return "x86"
        return lowered or "unknown"

    def _looks_inside_container(self) -> bool:
        if os.path.exists("/.dockerenv"):
            return True
        try:
            with open("/proc/1/cgroup", "r", encoding="utf-8", errors="ignore") as file_obj:
                content = file_obj.read().lower()
        except OSError:
            return False
        return any(marker in content for marker in ("docker", "kubepods", "containerd"))

    def _notes(self, normalized_os: str, normalized_arch: str, target_platform: str):
        notes = []
        if normalized_os and target_platform and normalized_os != target_platform:
            notes.append(
                "Planning host OS differs from the target container platform; prefer sandbox runtime facts for dependency decisions."
            )
        if normalized_os == "macos" and normalized_arch == "arm64":
            notes.append(
                "Planning host appears to be Apple Silicon; use linux/amd64 only when repository evidence indicates ARM64-incompatible Linux artifacts."
            )
        if normalized_os == "windows":
            notes.append(
                "Planning host is Windows; normalize paths and rely on the Linux sandbox for final setup commands."
            )
        return notes
