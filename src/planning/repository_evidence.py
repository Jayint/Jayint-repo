import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class RepositoryEvidence:
    repo_path: str
    repo_structure: str
    relevant_files: List[str]
    files_content: Dict[str, str]
    docs: str
    metadata: Dict[str, object] = field(default_factory=dict)

    def to_summary_dict(self) -> Dict[str, object]:
        return {
            "repo_path": self.repo_path,
            "relevant_files": list(self.relevant_files),
            "metadata": dict(self.metadata),
        }


class RepositoryEvidenceCollector:
    FILE_SIZE_THRESHOLD = 128 * 1000 * 2
    MAX_STRUCTURE_LINES = 600
    MAX_DOCS_CHARS = 24000
    MAX_FILE_SNIPPET_CHARS = 6000

    SKIP_DIRS = {
        "__pycache__",
        "node_modules",
        "target",
        "build",
        "dist",
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".eggs",
        ".idea",
        ".vscode",
    }

    VERSION_PRIORITY_FILES = [
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "requirements.txt",
        "requirements-test.txt",
        "requirements-dev.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Gemfile",
        "composer.json",
        "pubspec.yaml",
        ".python-version",
        ".nvmrc",
        "rust-toolchain",
        ".ruby-version",
        "tox.ini",
        "pytest.ini",
        "Makefile",
    ]

    EXACT_RELEVANT_NAMES = {
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".travis.yml",
        "tox.ini",
        "pytest.ini",
        "noxfile.py",
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
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
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "makefile",
        ".python-version",
        ".env.example",
    }

    def collect(self, repo_path: str, log_dir: Optional[str] = None) -> RepositoryEvidence:
        repo_path = os.path.abspath(repo_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        repo_structure = self.generate_repo_structure(repo_path)
        if log_dir:
            self._write_structure_log(log_dir, repo_structure)

        relevant_files = self.locate_relevant_files(repo_path)
        files_content = self.read_files_content(repo_path, relevant_files)
        docs = self.build_docs_content(files_content)

        evidence = RepositoryEvidence(
            repo_path=repo_path,
            repo_structure=repo_structure,
            relevant_files=relevant_files,
            files_content=files_content,
            docs=docs,
            metadata={
                "collector": "deterministic",
                "relevant_file_count": len(relevant_files),
            },
        )
        if log_dir:
            self._write_summary_log(log_dir, evidence)
        return evidence

    def generate_repo_structure(self, repo_path: str) -> str:
        structure_lines = []

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = sorted(directory for directory in dirs if directory not in self.SKIP_DIRS)

            level = root.replace(repo_path, "").count(os.sep)
            indent = "  " * level
            folder_name = os.path.basename(root) or os.path.basename(repo_path)
            structure_lines.append(f"{indent}{folder_name}/")

            sub_indent = "  " * (level + 1)
            for filename in sorted(files):
                structure_lines.append(f"{sub_indent}{filename}")

        return "\n".join(structure_lines)

    def locate_relevant_files(self, repo_path: str) -> List[str]:
        relevant = []
        test_files_added = 0
        max_test_files = 20

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = sorted(directory for directory in dirs if directory not in self.SKIP_DIRS)
            rel_root = os.path.relpath(root, repo_path)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            if depth > 6:
                dirs[:] = []
                continue

            for filename in sorted(files):
                rel_path = os.path.relpath(os.path.join(root, filename), repo_path)
                normalized = rel_path.replace(os.sep, "/")
                if self._is_relevant_file(normalized, filename):
                    relevant.append(normalized)
                    continue
                if self._is_test_source(normalized) and test_files_added < max_test_files:
                    relevant.append(normalized)
                    test_files_added += 1

        return sorted(dict.fromkeys(relevant), key=self._relevant_sort_key)

    def read_files_content(self, repo_path: str, file_paths: List[str]) -> Dict[str, str]:
        content = {}
        for rel_path in file_paths:
            full_path = os.path.join(repo_path, rel_path)
            try:
                if os.path.getsize(full_path) > self.FILE_SIZE_THRESHOLD:
                    continue
                with open(full_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                    content[rel_path] = file_obj.read(self.FILE_SIZE_THRESHOLD)
            except OSError:
                continue
        return content

    def build_docs_content(self, files_content: Dict[str, str]) -> str:
        docs_parts = ["------ BEGIN RELEVANT FILES ------\n"]
        current_size = len(docs_parts[0])

        for file_path in sorted(files_content, key=self._relevant_sort_key):
            raw_content = files_content[file_path]
            content = raw_content[: self.MAX_FILE_SNIPPET_CHARS]
            if len(raw_content) > self.MAX_FILE_SNIPPET_CHARS:
                content += (
                    f"\n... ({len(raw_content) - self.MAX_FILE_SNIPPET_CHARS} "
                    "more characters truncated)"
                )
            block = f"File: {file_path}\n```\n{content}\n```\n"
            if current_size + len(block) > self.MAX_DOCS_CHARS:
                docs_parts.append(
                    f"... additional relevant files truncated to stay within "
                    f"{self.MAX_DOCS_CHARS} characters ...\n"
                )
                break
            docs_parts.append(block)
            current_size += len(block)

        docs_parts.append("------ END RELEVANT FILES ------")
        return "\n".join(docs_parts)

    def _is_relevant_file(self, rel_path: str, filename: str) -> bool:
        lowered_path = rel_path.lower()
        lowered_name = filename.lower()

        if lowered_name in self.EXACT_RELEVANT_NAMES:
            return True
        if lowered_name.startswith("readme") and lowered_name.endswith((".md", ".rst", ".txt")):
            return True
        if lowered_path.startswith(".github/workflows/") and lowered_name.endswith(
            (".yml", ".yaml")
        ):
            return True
        if lowered_name.startswith("dockerfile"):
            return True
        if lowered_name.endswith((".env.example", ".env.sample")):
            return True
        if lowered_path.startswith("docs/") and lowered_name.endswith((".md", ".rst")):
            return True
        return False

    def _is_test_source(self, rel_path: str) -> bool:
        lowered = rel_path.lower()
        return (
            (lowered.startswith("tests/") or "/tests/" in lowered)
            and lowered.endswith((".py", ".js", ".ts", ".java", ".go", ".rs"))
        )

    def _relevant_sort_key(self, file_path: str):
        basename = os.path.basename(file_path)
        try:
            priority = self.VERSION_PRIORITY_FILES.index(basename)
        except ValueError:
            priority = len(self.VERSION_PRIORITY_FILES)
        return priority, file_path

    def _write_structure_log(self, log_dir: str, repo_structure: str):
        path = os.path.join(log_dir, "structure.txt")
        with open(path, "w", encoding="utf-8") as file_obj:
            file_obj.write("------ BEGIN REPOSITORY STRUCTURE ------\n")
            file_obj.write(repo_structure)
            file_obj.write("\n------ END REPOSITORY STRUCTURE ------\n")

    def _write_summary_log(self, log_dir: str, evidence: RepositoryEvidence):
        path = os.path.join(log_dir, "evidence_summary.json")
        with open(path, "w", encoding="utf-8") as file_obj:
            json.dump(evidence.to_summary_dict(), file_obj, indent=2, ensure_ascii=False)
