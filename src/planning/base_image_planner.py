import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.constants import DEFAULT_LLM_MODEL
from src.language_handlers import (
    LANGUAGE_HANDLERS,
    LanguageHandler,
    detect_language,
    get_language_handler,
)
from src.planning.repository_evidence import RepositoryEvidence


@dataclass
class BaseImageDecision:
    selected_image: str
    detected_language: str
    language_handler: LanguageHandler
    detected_runtime: Optional[str] = None
    platform_override: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    selection_method: str = "heuristic"
    candidate_images: List[str] = field(default_factory=list)

    def to_repo_summary_fields(self) -> Dict[str, object]:
        return {
            "primary_language": self.detected_language,
            "recommended_base_image": self.selected_image,
            "detected_runtime": self.detected_runtime,
            "platform_override": self.platform_override,
            "base_image_evidence": list(self.evidence),
            "base_image_selection_method": self.selection_method,
        }

    def to_runtime_metadata(self) -> Dict[str, object]:
        return {
            "selected_image": self.selected_image,
            "detected_language": self.detected_language,
            "detected_runtime": self.detected_runtime,
            "platform_override": self.platform_override,
            "selection_method": self.selection_method,
            "candidate_images": list(self.candidate_images),
        }


class BaseImagePlanner:
    def __init__(self, client=None, model: str = DEFAULT_LLM_MODEL):
        self.client = client
        self.model = model
        self.token_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

    def get_token_usage(self) -> Dict[str, int]:
        return dict(self.token_usage)

    def select(
        self,
        evidence: RepositoryEvidence,
        platform: str = "linux",
        language_hint: Optional[str] = None,
    ) -> BaseImageDecision:
        detected_language = self._detect_language(evidence, language_hint)
        language_handler = get_language_handler(detected_language)
        candidate_images = language_handler.base_images(platform)

        selected_image, detected_runtime, image_evidence = self._select_candidate_image(
            detected_language,
            candidate_images,
            evidence,
        )
        platform_override = self._detect_platform_override(evidence)

        return BaseImageDecision(
            selected_image=selected_image,
            detected_language=detected_language,
            language_handler=language_handler,
            detected_runtime=detected_runtime,
            platform_override=platform_override,
            evidence=image_evidence,
            selection_method="heuristic",
            candidate_images=candidate_images,
        )

    def _detect_language(
        self,
        evidence: RepositoryEvidence,
        language_hint: Optional[str],
    ) -> str:
        if language_hint:
            normalized = language_hint.strip().lower()
            aliases = {"js": "javascript", "ts": "typescript", "cpp": "c++"}
            normalized = aliases.get(normalized, normalized)
            if normalized in LANGUAGE_HANDLERS:
                return normalized

        manifest_language = self._language_from_root_manifests(evidence)
        if manifest_language:
            return manifest_language

        detected = detect_language(evidence.repo_structure, evidence.files_content)
        return detected or "python"

    def _language_from_root_manifests(self, evidence: RepositoryEvidence) -> Optional[str]:
        root_files = {
            path.lower()
            for path in evidence.files_content
            if "/" not in path.replace(os.sep, "/")
        }
        python_root_files = {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "requirements-test.txt",
            "requirements-dev.txt",
            "poetry.lock",
            "pipfile",
            "pipfile.lock",
            "environment.yml",
            "environment.yaml",
            "conda.yml",
            "conda.yaml",
            ".python-version",
            "tox.ini",
            "pytest.ini",
            "pdm.lock",
            "uv.lock",
        }
        if root_files & python_root_files:
            return "python"
        return None

    def _select_candidate_image(
        self,
        detected_language: str,
        candidate_images: List[str],
        evidence: RepositoryEvidence,
    ):
        if detected_language == "python":
            image, runtime, sources = self._select_python_image(candidate_images, evidence)
            return image, runtime, sources

        preferred = self._preferred_non_python_image(detected_language, candidate_images)
        return preferred, detected_language, self._generic_language_evidence(evidence)

    def _select_python_image(
        self,
        candidate_images: List[str],
        evidence: RepositoryEvidence,
    ):
        version, runtime, sources = self._detect_python_version(evidence)
        if version:
            exact = f"python:{version}"
            if exact in candidate_images:
                return exact, runtime or f"python=={version}", sources
            compatible = self._closest_python_candidate(version, candidate_images)
            if compatible:
                return compatible, runtime or f"python>={version}", sources

        default = "python:3.11" if "python:3.11" in candidate_images else candidate_images[-1]
        return default, runtime or "python", sources or self._generic_language_evidence(evidence)

    def _detect_python_version(self, evidence: RepositoryEvidence):
        content = evidence.files_content

        version = self._version_from_python_version_file(content.get(".python-version", ""))
        if version:
            return version, f"python=={version}", [".python-version"]

        classifier_version, classifier_source = self._python_classifier_version(content)
        if classifier_version:
            return classifier_version, f"python=={classifier_version}", [classifier_source]

        for path in ("pyproject.toml", "setup.cfg", "setup.py"):
            version, runtime = self._version_from_python_requirement(content.get(path, ""))
            if version:
                return version, runtime, [path]

        ci_version, ci_source = self._version_from_ci(content)
        if ci_version:
            return ci_version, f"python=={ci_version}", [ci_source]

        tox_version = self._version_from_tox(content.get("tox.ini", ""))
        if tox_version:
            return tox_version, f"python=={tox_version}", ["tox.ini"]

        docker_version, docker_source = self._version_from_dockerfile(content)
        if docker_version:
            return docker_version, f"python=={docker_version}", [docker_source]

        return None, None, []

    def _version_from_python_version_file(self, text: str) -> Optional[str]:
        match = re.search(r"\b(3\.\d+)", text or "")
        return match.group(1) if match else None

    def _python_classifier_version(self, files_content: Dict[str, str]):
        versions = []
        source = None
        for path in ("pyproject.toml", "setup.cfg", "setup.py"):
            text = files_content.get(path, "")
            found = re.findall(r"Programming Language :: Python :: (3\.\d+)", text)
            if found:
                source = path
                versions.extend(found)
        if versions:
            return self._max_version(versions), source
        return None, None

    def _version_from_python_requirement(self, text: str):
        if not text:
            return None, None
        patterns = (
            r"requires-python\s*=\s*[\"']([^\"']+)[\"']",
            r"python_requires\s*=\s*[\"']?([^\"'\n]+)[\"']?",
            r"^\s*python\s*=\s*[\"']([^\"']+)[\"']",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if not match:
                continue
            spec = match.group(1).strip()
            version = self._version_from_specifier(spec)
            if version:
                return version, f"python{spec}"
        return None, None

    def _version_from_specifier(self, spec: str) -> Optional[str]:
        normalized = spec.replace(" ", "")
        exact = re.search(r"==\s*(3\.\d+)", normalized)
        if exact:
            return exact.group(1)
        compatible = re.search(r"~=\s*(3\.\d+)", normalized)
        if compatible:
            return compatible.group(1)
        lower = re.search(r">=\s*(3\.\d+)", normalized)
        if lower:
            return lower.group(1)
        any_version = re.search(r"\b(3\.\d+)\b", normalized)
        return any_version.group(1) if any_version else None

    def _version_from_ci(self, files_content: Dict[str, str]):
        versions = []
        source = None
        for path, text in files_content.items():
            normalized = path.replace(os.sep, "/").lower()
            if not normalized.startswith(".github/workflows/") and normalized != ".travis.yml":
                continue
            found = re.findall(r"\b3\.\d+\b", text)
            if found:
                source = path
                versions.extend(found)
        if versions:
            return self._max_version(versions), source
        return None, None

    def _version_from_tox(self, text: str) -> Optional[str]:
        versions = []
        for major, minor in re.findall(r"py(3)(\d+)", text or ""):
            versions.append(f"{major}.{minor}")
        return self._max_version(versions) if versions else None

    def _version_from_dockerfile(self, files_content: Dict[str, str]):
        for path, text in files_content.items():
            if "dockerfile" not in os.path.basename(path).lower():
                continue
            match = re.search(r"FROM\s+python:(3\.\d+)", text, re.IGNORECASE)
            if match:
                return match.group(1), path
        return None, None

    def _max_version(self, versions: List[str]) -> str:
        def key(version: str):
            major, minor = version.split(".", 1)
            return int(major), int(minor)

        return sorted(set(versions), key=key)[-1]

    def _closest_python_candidate(
        self,
        version: str,
        candidate_images: List[str],
    ) -> Optional[str]:
        if f"python:{version}" in candidate_images:
            return f"python:{version}"
        return None

    def _preferred_non_python_image(
        self,
        detected_language: str,
        candidate_images: List[str],
    ) -> str:
        preferred_by_language = {
            "javascript": ("node:20", "node:22"),
            "typescript": ("node:20", "node:22"),
            "rust": ("rust:1.80", "rust:1.75"),
            "go": ("golang:1.22", "golang:1.23"),
            "java": ("openjdk:17", "eclipse-temurin:17"),
        }
        for preferred in preferred_by_language.get(detected_language, ()):
            if preferred in candidate_images:
                return preferred
        if candidate_images:
            return candidate_images[len(candidate_images) // 2]
        return "ubuntu:22.04"

    def _generic_language_evidence(self, evidence: RepositoryEvidence) -> List[str]:
        for candidate in (
            "pyproject.toml",
            "setup.py",
            "requirements.txt",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
        ):
            if candidate in evidence.files_content:
                return [candidate]
        return evidence.relevant_files[:1]

    def _detect_platform_override(self, evidence: RepositoryEvidence) -> Optional[str]:
        combined = "\n".join(evidence.files_content.values()).lower()
        arm64_risk_markers = (
            "embedded-postgres",
            "embedded-postgresql",
            "embedded-mysql",
            "zonky",
            "linux/amd64",
        )
        if any(marker in combined for marker in arm64_risk_markers):
            return "linux/amd64"
        return None
