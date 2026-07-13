"""A container that isn't one — for exercising the graph arm without Docker.

`docker_adapters` (entry.py:71) only ever asks a sandbox for three things:

    reset_to_base()              -> restore the base image
    run_install_script(script)   -> InstallResult(rc, failing_command, stderr, lineno)
    exec_readonly(cmd)           -> (rc, stdout)

Implement those three and the ENTIRE arm runs unchanged: the real classifier, the real
`enrich`, the real `certify_all`, the real renderer, the real loop, the real planner seam.
Only Docker is fake. That is the point -- a mock of `render_graph_context` would prove the
renderer renders; this proves the ARM works, which is a different claim and the one we need.

The container models the one causal fact the arm exists to discover: **an sdist needs a build
tool at install time, and apt is what provides it.** `pip install psycopg2` fails with the real
setuptools wording until `libpq-dev` is installed, because libpq-dev is what ships `pg_config`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InstallResult:
    rc: int
    failing_command: str | None = None
    stderr: str = ""
    lineno: int | None = None


# apt package -> the binaries it puts on PATH. The arm has to LEARN this mapping at runtime;
# nothing in the graph knows it up front.
APT_PROVIDES: dict[str, tuple[str, ...]] = {
    "libpq-dev": ("pg_config",),
    "pkg-config": ("pkg-config",),
    "build-essential": ("gcc", "make"),
    "default-jre-headless": ("java",),
}

# pip package -> the binary its BUILD needs, and the error it prints when that binary is absent.
# These strings are the real ones: setuptools/psycopg2 emit "Error: pg_config executable not
# found." with no colon, which is precisely the shape that used to classify as AMBIGUOUS.
SDIST_NEEDS: dict[str, tuple[str, str]] = {
    "psycopg2": ("pg_config", "Error: pg_config executable not found."),
    "asyncpg": ("pg_config", "Error: pg_config executable not found."),
    "mysqlclient": ("mysql_config", "Error: mysql_config executable not found."),
}


@dataclass
class FakeSandbox:
    """A tiny, deterministic stand-in for the Docker sandbox.

    `base_binaries` are what the base image already ships. Everything else must be installed by
    the script the agent writes -- which is the whole game.
    """
    base_binaries: frozenset[str] = frozenset({"python3", "pip", "sh"})
    tests_per_module: dict[str, int] = field(default_factory=dict)

    binaries: set[str] = field(init=False)
    pip: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.reset_to_base()

    # -- the three methods docker_adapters actually calls -------------------------------------

    def reset_to_base(self) -> None:
        """Every react turn re-runs the WHOLE script from base. That is why a turn is expensive,
        and why pre-empting one discovery hop is worth so much."""
        self.binaries = set(self.base_binaries)
        self.pip = set()

    def run_install_script(self, script: str) -> InstallResult:
        for lineno, raw in enumerate(script.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            rc, err = self._run_line(line)
            if rc != 0:
                return InstallResult(rc=rc, failing_command=line, stderr=err, lineno=lineno)
        return InstallResult(rc=0)

    def exec_readonly(self, cmd: str) -> tuple[int, str]:
        if cmd.startswith("command -v "):
            return (0, "") if cmd.split()[-1] in self.binaries else (1, "")
        if "python3 -c" in cmd and "import " in cmd:
            mod = cmd.split("import ")[-1].strip().strip("\"'")
            return (0, "") if mod in self.pip else (1, f"ModuleNotFoundError: No module named '{mod}'")
        if "pytest" in cmd:
            return 0, self._pytest_output()
        return 0, ""

    # -- the container's causal model ----------------------------------------------------------

    def _run_line(self, line: str) -> tuple[int, str]:
        if line.startswith(("apt-get install", "apt install")):
            for pkg in [t for t in line.split() if t not in
                        ("apt-get", "apt", "install", "-y", "--no-install-recommends")]:
                if pkg not in APT_PROVIDES:
                    return 100, f"E: Unable to locate package {pkg}"
                self.binaries.update(APT_PROVIDES[pkg])
            return 0, ""

        if line.startswith(("pip install", "pip3 install")):
            for tok in [t for t in line.split()[2:] if not t.startswith("-")]:
                name = tok.split("==")[0]
                need, err = SDIST_NEEDS.get(name, (None, ""))
                if need is not None and need not in self.binaries:
                    # THE failure the whole arm is built around.
                    return 1, err
                self.pip.add(name)
            return 0, ""

        return 0, ""

    def _pytest_output(self) -> str:
        """Real-shaped pytest output. A module whose import is missing produces a COLLECTION
        error -- per FILE, with its tests never becoming items, which is exactly why the count
        pytest reports is modules and not tests."""
        errors, n_err = [], 0
        for module, imports in (("tests/test_db.py", "psycopg2"), ("tests/test_async.py", "asyncpg")):
            if imports not in self.pip:
                n_err += 1
                errors.append(
                    f"_______________ ERROR collecting {module} ________________\n"
                    f"{module}:1: in <module>\n"
                    f"    import {imports}\n"
                    f"E   ModuleNotFoundError: No module named '{imports}'\n"
                )
        body = ""
        if errors:
            body += "==================================== ERRORS ====================================\n"
            body += "\n".join(errors)
        # One genuine test-body failure that NO environment fix can touch. It must never become
        # a graph node, however its message reads.
        body += (
            "=================================== FAILURES ===================================\n"
            "__________________________________ test_math ___________________________________\n"
            "    def test_math():\n"
            ">       assert 3 == 4\n"
            "E       assert 3 == 4\n"
            "\n"
            "tests/test_logic.py:2: AssertionError\n"
        )
        passed = 0 if n_err else 8
        tail = f"{passed} passed, 1 failed" + (f", {n_err} errors" if n_err else "")
        return body + f"\n=========== {tail} ============\n"
