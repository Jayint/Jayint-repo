"""Strict parsers and host-side policy gates for the graph-free ablation."""
from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass
from typing import Any

from .models import (
    AbstainAction,
    AgentAction,
    FlatBlock,
    FlatPatch,
    FlatPlan,
    PatchAction,
    ProbeAction,
)


_BLOCK_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_PATCH_OPS = frozenset(
    {
        "replace_block",
        "insert_before",
        "insert_after",
        "append_block",
        "delete_block",
    }
)
_GRAPH_ONLY_KEYS = frozenset(
    {
        "graph",
        "nodes",
        "node",
        "node_id",
        "target_node",
        "target_node_id",
        "target_node_ids",
        "edges",
        "edge",
        "providers",
        "provider",
        "provider_ids",
        "provides",
        "wave",
        "layer",
        "add_requirements",
        "add_providers",
        "add_edges",
        "request_checks",
    }
)


class PolicyError(ValueError):
    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors = tuple(dict.fromkeys(str(error) for error in errors))
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class ValidationResult:
    allowed: bool
    errors: tuple[str, ...] = ()


def _required_text(data: dict[str, Any], key: str, errors: list[str]) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key!r} must be a non-empty string")
        return ""
    return value.strip()


def _optional_string_list(
    data: dict[str, Any],
    key: str,
    errors: list[str],
) -> tuple[str, ...]:
    value = data.get(key, ())
    if not isinstance(value, (list, tuple)):
        errors.append(f"{key!r} must be an array of strings")
        return ()
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{key}[{index}] must be a non-empty string")
            continue
        result.append(item.strip())
    return tuple(result)


def _reject_graph_fields(value: Any, errors: list[str], path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in _GRAPH_ONLY_KEYS:
                errors.append(f"{path}.{key}: graph field is forbidden in this ablation")
            _reject_graph_fields(child, errors, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_graph_fields(child, errors, f"{path}[{index}]")


def _parse_block(value: Any, errors: list[str], path: str) -> FlatBlock | None:
    if not isinstance(value, dict):
        errors.append(f"{path}: expected an object")
        return None
    allowed = {"block_id", "commands", "checks", "evidence_refs"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f"{path}: unsupported field(s): {', '.join(unknown)}")
    block_id = _required_text(value, "block_id", errors)
    commands = _optional_string_list(value, "commands", errors)
    checks = _optional_string_list(value, "checks", errors)
    evidence_refs = _optional_string_list(value, "evidence_refs", errors)
    return FlatBlock(block_id, commands, checks, evidence_refs)


def parse_initial_plan(data: Any) -> FlatPlan:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise PolicyError(("initial response must be one JSON object",))
    _reject_graph_fields(data, errors)
    if data.get("type") != "initial_plan":
        errors.append("initial response type must be 'initial_plan'")
    raw_blocks = data.get("blocks")
    if not isinstance(raw_blocks, (list, tuple)):
        errors.append("'blocks' must be an array")
        raw_blocks = ()
    blocks: list[FlatBlock] = []
    for index, raw in enumerate(raw_blocks):
        block = _parse_block(raw, errors, f"blocks[{index}]")
        if block is not None:
            blocks.append(block)
    if errors:
        raise PolicyError(errors)
    return FlatPlan(tuple(blocks))


def parse_agent_action(data: Any) -> AgentAction:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise PolicyError(("agent action must be one JSON object",))
    _reject_graph_fields(data, errors)
    action_type = data.get("type")
    if action_type == "probe":
        action = ProbeAction(
            purpose=_required_text(data, "purpose", errors),
            command=_required_text(data, "command", errors),
        )
        if errors:
            raise PolicyError(errors)
        return action

    if action_type == "propose_patch":
        rationale_value = data.get("rationale")
        if isinstance(rationale_value, dict):
            rationale = str(rationale_value.get("why") or "").strip()
        else:
            rationale = str(rationale_value or "").strip()
        if not rationale:
            errors.append("'rationale' must explain the patch")
        raw_patch = data.get("patch")
        if not isinstance(raw_patch, dict):
            errors.append("'patch' must be an object")
            raw_patch = {}
        op = _required_text(raw_patch, "op", errors)
        if op and op not in _PATCH_OPS:
            errors.append(f"unsupported patch op: {op}")
        target_value = raw_patch.get("target_block_id")
        target = (
            target_value.strip()
            if isinstance(target_value, str) and target_value.strip()
            else None
        )
        block = None
        if "block" in raw_patch:
            block = _parse_block(raw_patch.get("block"), errors, "patch.block")
        if op == "append_block":
            if target is not None:
                errors.append("append_block must not set target_block_id")
        elif op in _PATCH_OPS and target is None:
            errors.append(f"{op} requires target_block_id")
        if op == "delete_block":
            if block is not None:
                errors.append("delete_block must not include a replacement block")
        elif op in _PATCH_OPS and block is None:
            errors.append(f"{op} requires a block")
        if errors:
            raise PolicyError(errors)
        return PatchAction(
            rationale=rationale,
            patch=FlatPatch(op=op, target_block_id=target, block=block),  # type: ignore[arg-type]
        )

    if action_type == "abstain":
        classification = _required_text(data, "classification", errors)
        reason = _required_text(data, "reason", errors)
        evidence_refs = _optional_string_list(data, "evidence_refs", errors)
        if classification and classification != "non_environment":
            errors.append("abstain classification must be 'non_environment'")
        if not evidence_refs:
            errors.append("abstain requires at least one evidence reference")
        if errors:
            raise PolicyError(errors)
        return AbstainAction(classification, reason, evidence_refs)

    errors.append(
        "action type must be one of: probe, propose_patch, abstain"
    )
    raise PolicyError(errors)


_MUTATING_PROBE_EXECUTABLES = frozenset(
    {
        "apt",
        "apt-get",
        "apk",
        "brew",
        "chgrp",
        "chmod",
        "chown",
        "cp",
        "curl",
        "dd",
        "docker",
        "git",
        "install",
        "kill",
        "ln",
        "mkdir",
        "mount",
        "mv",
        "patch",
        "pkill",
        "podman",
        "rm",
        "rmdir",
        "sed",
        "service",
        "sudo",
        "systemctl",
        "tee",
        "touch",
        "truncate",
        "umount",
        "wget",
    }
)
_SIMPLE_READ_EXECUTABLES = frozenset(
    {
        "[",
        "apt-cache",
        "cat",
        "cd",
        "cut",
        "dpkg-query",
        "egrep",
        "false",
        "fgrep",
        "file",
        "grep",
        "head",
        "id",
        "ldd",
        "ls",
        "pkg-config",
        "printenv",
        "pwd",
        "readlink",
        "realpath",
        "sort",
        "stat",
        "tail",
        "test",
        "tr",
        "true",
        "type",
        "uname",
        "uniq",
        "wc",
        "whereis",
        "which",
    }
)
_PYTHON_MUTATING_CALLS = frozenset(
    {
        "chmod",
        "chown",
        "connect",
        "copy",
        "copy2",
        "copyfile",
        "eval",
        "exec",
        "makedirs",
        "mkdir",
        "move",
        "open",
        "popen",
        "putenv",
        "remove",
        "rename",
        "replace",
        "rmdir",
        "rmtree",
        "run",
        "symlink",
        "system",
        "touch",
        "truncate",
        "unlink",
        "write",
        "write_bytes",
        "write_text",
        "__import__",
    }
)
_PYTHON_DANGEROUS_IMPORTS = frozenset(
    {
        "asyncio.subprocess",
        "ctypes",
        "ftplib",
        "http.client",
        "multiprocessing",
        "requests",
        "shutil",
        "socket",
        "subprocess",
        "urllib.request",
    }
)
_PYTHON_READ_ONLY_VERSION_MODULES = frozenset({"pip", "pipx", "poetry"})


def _call_leaf_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _validate_python_source(source: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return (f"python probe is not valid: {exc.msg}",)
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _PYTHON_DANGEROUS_IMPORTS:
                    errors.append(f"probe imports mutation-capable module: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _PYTHON_DANGEROUS_IMPORTS:
                errors.append(f"probe imports mutation-capable module: {module}")
        elif isinstance(node, ast.Call):
            leaf = _call_leaf_name(node.func)
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                errors.append("probe invokes a dynamically resolved callable")
            if leaf in _PYTHON_MUTATING_CALLS:
                errors.append(f"probe calls mutation-capable function: {leaf}")
    return tuple(dict.fromkeys(errors))


def _probe_segment_error(tokens: list[str]) -> tuple[str, ...]:
    if not tokens:
        return ("empty probe segment",)
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[0]):
        return ("environment assignments are not allowed in probe commands",)
    executable = tokens[0].rsplit("/", 1)[-1]
    if executable in _MUTATING_PROBE_EXECUTABLES:
        return (f"probe executable can mutate the environment: {executable}",)

    # These shell/system tools can dispatch another command or mutate state
    # unless they are restricted to one exact read-only query form.
    if executable == "command":
        return () if len(tokens) == 3 and tokens[1] in {"-v", "-V"} else (
            "command probes are allowed only as: command -v NAME or command -V NAME",
        )
    if executable == "env":
        return () if tokens == ["env"] else (
            "env probes may print the environment only; command dispatch is forbidden",
        )
    if executable == "ldconfig":
        return () if len(tokens) == 2 and tokens[1] in {
            "-p",
            "--print-cache",
            "-V",
            "--version",
        } else ("ldconfig is allowed only for cache/version queries",)
    if executable == "sort":
        if any(
            token == "-o"
            or token.startswith("-o")
            or token.startswith("--output")
            or token.startswith("--compress-program")
            or (
                token.startswith("-")
                and not token.startswith("--")
                and "o" in token[1:]
            )
            for token in tokens[1:]
        ):
            return ("sort probes may not write output files or execute helpers",)
        return ()

    if executable in _SIMPLE_READ_EXECUTABLES:
        return ()

    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        if "-c" in tokens:
            index = tokens.index("-c")
            if index + 1 >= len(tokens):
                return ("python -c probe is missing its source",)
            return _validate_python_source(tokens[index + 1])
        if (
            len(tokens) == 4
            and tokens[1] == "-m"
            and tokens[2] in _PYTHON_READ_ONLY_VERSION_MODULES
            and tokens[3] in {"--version", "-V"}
        ):
            return ()
        if len(tokens) >= 4 and tokens[1:3] == ["-m", "pip"]:
            if tokens[3] in {"show", "check", "list", "freeze", "debug"}:
                return ()
        if len(tokens) == 2 and tokens[1] in {"--version", "-V"}:
            return ()
        return (
            "python probes must use safe -c code, interpreter/version queries, "
            "or read-only pip queries",
        )

    if executable in {"pip", "pip3"}:
        return () if len(tokens) >= 2 and tokens[1] in {
            "show",
            "check",
            "list",
            "freeze",
            "debug",
        } else ("pip probe must be a read-only query",)
    if executable in {"node", "java", "javac", "ruby", "php"}:
        return () if any(token in {"--version", "-version", "-v"} for token in tokens[1:]) else (
            f"{executable} probe must only request version information",
        )
    if executable in {"npm", "pnpm", "yarn"}:
        return () if len(tokens) >= 2 and tokens[1] in {
            "--version",
            "-v",
            "list",
            "ls",
            "view",
        } else (f"{executable} probe must be a read-only query",)
    if executable == "go":
        if len(tokens) < 2 or tokens[1] not in {"env", "list", "version"}:
            return ("go probe must use env, list, or version",)
        if tokens[1] == "env" and any(
            token == "-w" or token == "-u" or token.startswith(("-w=", "-u="))
            for token in tokens[2:]
        ):
            return ("go env probes may not write or unset configuration",)
        if tokens[1] == "list" and not any(
            token == "-mod=readonly" or token.startswith("-mod=readonly")
            for token in tokens[2:]
        ):
            return ("go list probes require -mod=readonly",)
        return ()
    if executable == "cargo":
        if len(tokens) < 2 or tokens[1] not in {
            "metadata",
            "tree",
            "--version",
            "-V",
        }:
            return ("cargo probe must use metadata, tree, or version",)
        if tokens[1] in {"metadata", "tree"} and not (
            "--locked" in tokens and "--offline" in tokens
        ):
            return ("cargo metadata/tree probes require --locked and --offline",)
        return ()
    if executable in {"mvn", "mvnw", "gradle", "gradlew", "dotnet"}:
        return () if any(token in {"--version", "-version", "-v", "--info"} for token in tokens[1:]) else (
            f"{executable} probe must only request version information",
        )
    return (f"probe executable is not in the read-only allowlist: {executable}",)


def validate_probe_command(command: str) -> ValidationResult:
    command = (command or "").strip()
    if not command:
        return ValidationResult(False, ("probe command is empty",))
    if len(command) > 2_000:
        return ValidationResult(False, ("probe command is too long",))
    if "\n" in command or "\r" in command:
        return ValidationResult(False, ("multi-line probes are not allowed",))
    if "`" in command or "$(" in command or "${" in command:
        return ValidationResult(False, ("shell expansion/substitution is not allowed",))
    try:
        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars=";&<>|",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        return ValidationResult(False, (f"probe shell syntax is invalid: {exc}",))
    if any(
        token == ";" or "<" in token or ">" in token
        for token in tokens
    ):
        return ValidationResult(
            False,
            ("redirection and semicolon operators are not allowed",),
        )

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in {"&&", "||", "|"}:
            if not segments[-1]:
                return ValidationResult(False, ("empty probe pipeline segment",))
            segments.append([])
        else:
            segments[-1].append(token)
    if not segments[-1]:
        return ValidationResult(False, ("empty probe pipeline segment",))
    errors: list[str] = []
    for segment in segments:
        errors.extend(_probe_segment_error(segment))
    unique = tuple(dict.fromkeys(errors))
    return ValidationResult(not unique, unique)


_TEST_COMMAND_RE = re.compile(
    r"(?:^|(?:&&|\|\||;)\s*)"
    r"(?:cd\s+\S+\s+&&\s+)?"
    r"(?:"
    r"(?:(?:poetry|pdm|uv|hatch|pipenv)\s+run\s+)?"
    r"(?:python(?:\d+(?:\.\d+)*)?\s+-m\s+)?(?:pytest|unittest)\b"
    r"|(?:tox|nox|nosetests|nose)\b"
    r"|(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b"
    r"|(?:npx\s+)?(?:jest|vitest|mocha|karma|cypress)\b"
    r"|cargo\s+(?:test|nextest\b.*\brun)\b"
    r"|go\s+test\b"
    r"|dotnet\s+test\b"
    r"|(?:\./)?(?:mvnw|gradlew)\b.*\b(?:test|check)\b"
    r"|(?:mvn|gradle|make|gmake|ninja)\b.*\b(?:test|tests|check)\b"
    r"|(?:bundle\s+exec\s+)?(?:rspec|rake\s+test)\b"
    r"|(?:\./)?(?:vendor/bin/)?(?:phpunit|pest)\b"
    r"|ctest\b"
    r")",
    re.IGNORECASE,
)

_WRAPPER_OPTIONS_WITH_VALUE = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "sudo": {
        "-C",
        "--close-from",
        "-D",
        "--chdir",
        "-g",
        "--group",
        "-h",
        "--host",
        "-p",
        "--prompt",
        "-R",
        "--chroot",
        "-r",
        "--role",
        "-t",
        "--type",
        "-u",
        "--user",
    },
    "xvfb-run": {"-e", "--error-file", "-f", "--auth-file", "-n", "--server-num",
                 "-p", "--xauth-protocol", "-s", "--server-args", "-w", "--wait"},
    "timeout": {"-k", "--kill-after", "-s", "--signal"},
}


def _wrapped_command_body(tokens: list[str]) -> str | None:
    """Return the command dispatched by a common wrapper, if present."""
    if not tokens:
        return None
    executable = tokens[0].rsplit("/", 1)[-1].lower()
    if executable in {"bash", "sh", "zsh"}:
        if len(tokens) >= 3 and tokens[1].startswith("-") and "c" in tokens[1]:
            return tokens[2]
        return None
    if executable == "exec":
        return shlex.join(tokens[1:]) if len(tokens) > 1 else None
    if executable not in {"env", "command", "sudo", "xvfb-run", "timeout"}:
        return None

    index = 1
    options_with_value = _WRAPPER_OPTIONS_WITH_VALUE.get(executable, set())
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if executable == "env" and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*=.*", token
        ):
            index += 1
            continue
        if not token.startswith("-") or token == "-":
            break
        option = token.split("=", 1)[0]
        index += 1
        if option in options_with_value and "=" not in token:
            index += 1

    # timeout has one required duration operand before the dispatched command.
    if executable == "timeout" and index < len(tokens):
        index += 1
    return shlex.join(tokens[index:]) if index < len(tokens) else None


def _contains_test_command(command: str) -> bool:
    if _TEST_COMMAND_RE.search(command):
        return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in {"&&", "||", ";", "|"}:
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append(token)
    for segment in segments:
        body = _wrapped_command_body(segment)
        if body and _contains_test_command(body):
            return True
    return False


_PROTECTED_REPO_PATH_RE = re.compile(
    r"(?:"
    r"/app(?:/|\b)"
    r"|(?:^|[\s'\"=])(?:\./)?"
    r"(?:src|source|tests?|testdata|examples?|docs?|packages?|lib)"
    r"(?:/|\b)"
    r"|(?:^|[\s'\"=])(?:\./)?"
    r"(?:pyproject\.toml|setup\.py|setup\.cfg|package\.json|cargo\.toml|"
    r"go\.mod|pom\.xml|build\.gradle(?:\.kts)?|tox\.ini|pytest\.ini|"
    r"conftest\.py)(?:[\s'\";|&]|$)"
    r")",
    re.IGNORECASE,
)


def _explicit_repo_mutation(command: str) -> bool:
    """Catch common direct writes to repository-owned source/test/config paths.

    This is deliberately conservative and supplements, rather than replaces,
    terminal test verification. Environment setup may write to system/cache
    paths, but it may not edit the checked-out program used by the test gate.
    """
    if not _PROTECTED_REPO_PATH_RE.search(command):
        return False
    return bool(
        re.search(
            r"\b(?:cp|install|tee|touch|chmod|chown|chgrp|truncate|rm|mv)\b",
            command,
            re.IGNORECASE,
        )
        or re.search(
            r"\bfind\b[^\n]*(?:-delete|-exec|-execdir|-ok|-okdir)\b",
            command,
            re.IGNORECASE,
        )
        or re.search(r"(?:^|[|;&])\s*(?:printf|echo|cat)\b[^\n]*\|\s*tee\b",
                     command, re.IGNORECASE)
    )


def _validate_setup_command(command: str) -> tuple[str, ...]:
    errors: list[str] = []
    stripped = (command or "").strip()
    if not stripped:
        return ("setup command is empty",)
    if len(stripped) > 4_000:
        errors.append("setup command is too long")
    if "\n" in stripped or "\r" in stripped or "\x00" in stripped:
        errors.append("setup commands must be single-line text")
    lowered = stripped.lower()
    if "#@block" in lowered or BLOCK_MARKER_TEXT in stripped:
        errors.append("setup command may not forge host block markers")
    if re.search(r"\btrap\b|\bset\s+\+e\b", lowered):
        errors.append("setup command may not alter host failure handling")
    if re.search(r"\|\|\s*(?:true|:)\b|(?:^|[;&])\s*exit\s+0\b", lowered):
        errors.append("setup command may not mask a failure")
    if _contains_test_command(stripped):
        errors.append("test commands must not appear in setup.sh")
    if re.search(r"--(?:ignore|ignore-glob|deselect)(?:=|\s|$)", lowered):
        errors.append("test-exclusion flags are forbidden")
    if re.search(r"\b(?:docker|podman)\b|\b(?:mount|umount|shutdown|reboot)\b", lowered):
        errors.append("host/container control commands are forbidden")
    if re.search(
        r"\b(?:service|systemctl|initctl|rc-service|supervisord|daemonize|nohup)\b",
        lowered,
    ):
        errors.append("setup.sh may not start a background runtime service")
    if re.search(r"\bcurl\b[^|]*\|\s*(?:ba)?sh\b|\bwget\b[^|]*\|\s*(?:ba)?sh\b", lowered):
        errors.append("piping remote code directly to a shell is forbidden")
    if re.search(r"\bgit\s+(?:apply|checkout|reset|clean|restore)\b|\bpatch\s+-p", lowered):
        errors.append("source-control/source patching commands are forbidden")
    if re.search(r"\bsed\b[^\n]*\s-i(?:\s|$)|\bperl\b[^\n]*\s-pi(?:\s|$)", lowered):
        errors.append("in-place source editing is forbidden")
    if re.search(
        r"\b(?:rm|mv|truncate)\b[^\n]*(?:/app(?:/|\b)|(?:^|\s)(?:\./)?(?:src|tests?)(?:/|\b))",
        lowered,
    ):
        errors.append("source or test tree mutation is forbidden")
    if re.search(
        r"\bpython(?:\d+(?:\.\d+)*)?\s+-c\s+.*(?:write_text|write_bytes|open\s*\([^)]*['\"]w|unlink|remove)",
        stripped,
        re.IGNORECASE,
    ):
        errors.append("inline Python source mutation is forbidden")
    if _explicit_repo_mutation(stripped):
        errors.append("source, test, or repository configuration mutation is forbidden")

    for match in re.finditer(r"(?<!<)(?:>>|>)\s*([^\s;&|]+)", stripped):
        target = match.group(1).strip("'\"")
        if target.startswith("&"):
            continue
        allowed_prefixes = (
            "/dev/",
            "/tmp/",
            "/var/",
            "/etc/",
            "/usr/",
            "/opt/",
            "/root/",
        )
        if target.startswith("/app/") or not target.startswith(allowed_prefixes):
            errors.append(f"write redirection to the repository is forbidden: {target}")
    return tuple(dict.fromkeys(errors))


BLOCK_MARKER_TEXT = "__ABLATION_BLOCK__:"


class FlatPlanGate:
    """Validate plans and atomically apply one local flat-script patch."""

    def __init__(
        self,
        *,
        max_blocks: int = 48,
        max_commands: int = 192,
        max_checks: int = 96,
    ) -> None:
        self.max_blocks = max_blocks
        self.max_commands = max_commands
        self.max_checks = max_checks

    def validate_plan(
        self,
        plan: FlatPlan,
        evidence_ids: frozenset[str],
    ) -> ValidationResult:
        errors: list[str] = []
        if not isinstance(plan, FlatPlan):
            return ValidationResult(False, ("plan has the wrong type",))
        if plan.version != 1:
            errors.append("only flat plan version 1 is supported")
        if len(plan.blocks) > self.max_blocks:
            errors.append(f"plan exceeds {self.max_blocks} blocks")
        ids = [block.block_id for block in plan.blocks]
        if len(ids) != len(set(ids)):
            errors.append("block_id values must be unique")
        command_count = sum(len(block.commands) for block in plan.blocks)
        check_count = sum(len(block.checks) for block in plan.blocks)
        if command_count > self.max_commands:
            errors.append(f"plan exceeds {self.max_commands} setup commands")
        if check_count > self.max_checks:
            errors.append(f"plan exceeds {self.max_checks} checks")

        for block in plan.blocks:
            prefix = f"block {block.block_id!r}"
            if not _BLOCK_ID_RE.fullmatch(block.block_id or ""):
                errors.append(f"{prefix}: invalid block_id")
            if not block.commands:
                errors.append(f"{prefix}: at least one setup command is required")
            for command in block.commands:
                errors.extend(f"{prefix}: {error}" for error in _validate_setup_command(command))
            if not block.evidence_refs:
                errors.append(f"{prefix}: at least one evidence_ref is required")
            unknown_refs = sorted(set(block.evidence_refs) - evidence_ids)
            if unknown_refs:
                errors.append(
                    f"{prefix}: unknown evidence_ref(s): {', '.join(unknown_refs)}"
                )
            for check in block.checks:
                validation = validate_probe_command(check)
                if not validation.allowed:
                    errors.extend(
                        f"{prefix} check: {error}" for error in validation.errors
                    )
                try:
                    first = shlex.split(check)[0].rsplit("/", 1)[-1]
                except (ValueError, IndexError):
                    first = ""
                if first in {"echo", "printf", "true", "false", "pwd"}:
                    errors.append(f"{prefix}: check is not meaningful: {check}")
        unique = tuple(dict.fromkeys(errors))
        return ValidationResult(not unique, unique)

    def apply_patch(
        self,
        plan: FlatPlan,
        patch: FlatPatch,
        evidence_ids: frozenset[str],
        *,
        failed_block_id: str | None = None,
        failure_kind: str | None = None,
    ) -> FlatPlan:
        errors: list[str] = []
        target = patch.target_block_id
        existing = [block.block_id for block in plan.blocks]

        if patch.op not in _PATCH_OPS:
            errors.append(f"unsupported patch op: {patch.op}")
        if patch.op == "append_block":
            if target is not None:
                errors.append("append_block must not have a target")
        elif not target:
            errors.append(f"{patch.op} requires target_block_id")
        elif target not in existing:
            errors.append(f"target block does not exist: {target}")

        if patch.op == "delete_block":
            if patch.block is not None:
                errors.append("delete_block must not include a block")
        elif patch.block is None:
            errors.append(f"{patch.op} requires a block")

        if patch.block is not None:
            if patch.op == "replace_block" and target and patch.block.block_id != target:
                errors.append("replacement block_id must equal target_block_id")
            if patch.op in {"insert_before", "insert_after", "append_block"}:
                if patch.block.block_id in existing:
                    errors.append(f"inserted block_id already exists: {patch.block.block_id}")

        if failure_kind in {"setup", "check", "terminal_setup", "terminal_check"}:
            if patch.op in {"insert_after", "append_block"}:
                errors.append(
                    "a setup/check failure cannot be repaired only after the failing block"
                )
            elif (
                failed_block_id
                and patch.op in {"replace_block", "delete_block", "insert_before"}
            ):
                if target != failed_block_id:
                    errors.append(
                        "setup/check repair must target the failing block"
                    )

        if errors:
            raise PolicyError(errors)

        blocks = list(plan.blocks)
        if patch.op == "append_block":
            assert patch.block is not None
            blocks.append(patch.block)
        else:
            assert target is not None
            index = existing.index(target)
            if patch.op == "replace_block":
                assert patch.block is not None
                blocks[index] = patch.block
            elif patch.op == "insert_before":
                assert patch.block is not None
                blocks.insert(index, patch.block)
            elif patch.op == "insert_after":
                assert patch.block is not None
                blocks.insert(index + 1, patch.block)
            elif patch.op == "delete_block":
                del blocks[index]

        candidate = FlatPlan(tuple(blocks), version=plan.version)
        validation = self.validate_plan(candidate, evidence_ids)
        if not validation.allowed:
            raise PolicyError(validation.errors)
        if candidate.digest() == plan.digest():
            raise PolicyError(("patch is a no-op",))
        return candidate
