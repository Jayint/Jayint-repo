"""Structured GraphExecuteAgent actions and host-side probe validation.

The canonical graph executor accepts open-ended diagnostics, but a model prompt is
not a security boundary.  This module parses the three allowed JSON actions and
conservatively rejects probe commands that can mutate the candidate environment.
"""
from __future__ import annotations

import ast
import os
import re
import shlex
from dataclasses import dataclass

from python_deps.depgraph.patch import PatchParseError, PatchProposal, parse_patch_proposal


class AgentActionParseError(ValueError):
    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors = tuple(str(error) for error in errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class ProbeAction:
    target_node: str
    purpose: str
    command: str
    type: str = "probe"


@dataclass(frozen=True)
class ProposePatchAction:
    target_node: str
    rationale: dict
    proposal: PatchProposal
    type: str = "propose_patch"


@dataclass(frozen=True)
class AbstainAction:
    classification: str
    reason: str
    evidence_refs: tuple[str, ...]
    type: str = "abstain"


AgentAction = ProbeAction | ProposePatchAction | AbstainAction


@dataclass(frozen=True)
class ProbeValidation:
    allowed: bool
    errors: tuple[str, ...] = ()


def _required_text(data: dict, key: str, errors: list[str]) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key!r} must be a non-empty string")
        return ""
    return value.strip()


def parse_agent_action(data: dict) -> AgentAction:
    if not isinstance(data, dict):
        raise AgentActionParseError(("action must be one JSON object",))
    action_type = data.get("type")
    if action_type not in {"probe", "propose_patch", "abstain"}:
        raise AgentActionParseError((
            "action 'type' must be one of: probe, propose_patch, abstain",
        ))

    errors: list[str] = []
    if action_type == "probe":
        action = ProbeAction(
            target_node=_required_text(data, "target_node", errors),
            purpose=_required_text(data, "purpose", errors),
            command=_required_text(data, "command", errors),
        )
        if errors:
            raise AgentActionParseError(errors)
        return action

    if action_type == "propose_patch":
        target_node = _required_text(data, "target_node", errors)
        raw_patch = data.get("patch")
        if not isinstance(raw_patch, dict):
            errors.append("'patch' must be an object using the PatchProposal schema")
            raw_patch = {}
        raw_rationale = data.get("rationale", {})
        if isinstance(raw_rationale, str):
            rationale = {"why": raw_rationale}
        elif isinstance(raw_rationale, dict):
            rationale = raw_rationale
        else:
            errors.append("'rationale' must be a string or object")
            rationale = {}
        if not rationale:
            errors.append("'rationale' must explain the proposed patch")
        if errors:
            raise AgentActionParseError(errors)
        try:
            proposal = parse_patch_proposal({"rationale": rationale, "patch": raw_patch})
        except PatchParseError as exc:
            raise AgentActionParseError(exc.errors) from exc
        if proposal.is_empty():
            raise AgentActionParseError(("propose_patch contains an empty patch",))
        return ProposePatchAction(target_node, rationale, proposal)

    classification = _required_text(data, "classification", errors)
    reason = _required_text(data, "reason", errors)
    refs = data.get("evidence_refs", ())
    if not isinstance(refs, (list, tuple)) or any(
        not isinstance(ref, str) or not ref.strip() for ref in refs
    ):
        errors.append("'evidence_refs' must be an array of non-empty strings")
        refs = ()
    if classification and classification != "non_environment":
        errors.append("abstain classification must be 'non_environment'")
    if errors:
        raise AgentActionParseError(errors)
    return AbstainAction(classification, reason, tuple(refs))


_SHELL_MUTATION_WORDS = frozenset({
    "apt", "apt-get", "apk", "brew", "chgrp", "chmod", "chown", "cp", "curl",
    "dd", "git", "install", "kill", "ln", "mkdir", "mount", "mv",
    "pkill", "rm", "rmdir", "service", "systemctl", "tee", "touch", "truncate",
    "umount", "wget",
})

_SIMPLE_READ_COMMANDS = frozenset({
    "[", "apt-cache", "cat", "cut", "egrep", "false", "fgrep", "file", "grep",
    "head", "id", "ldd", "ls", "pkg-config", "printenv", "pwd", "readlink",
    "realpath", "sort", "stat", "tail", "test", "tr", "true", "type", "uname",
    "uniq", "wc", "whereis", "which", "jq",
})

_PYTHON_MUTATING_CALLS = frozenset({
    "chmod", "chown", "connect", "copy", "copy2", "copyfile", "copymode",
    "copystat", "eval", "exec", "execv", "execve", "makedirs", "mkdir", "move",
    "openpty", "popen", "putenv", "remove", "removedirs", "rename", "renames",
    "replace", "rmdir", "rmtree", "run", "spawnl", "spawnle", "spawnlp",
    "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "startfile", "symlink",
    "system", "touch", "truncate", "unlink", "unsetenv", "write", "write_bytes",
    "write_text", "__import__",
})

_PYTHON_DANGEROUS_IMPORTS = frozenset({
    "asyncio.subprocess", "ctypes", "ftplib", "http.client", "multiprocessing",
    "requests", "shutil", "socket", "subprocess", "urllib.request",
})


def _call_leaf_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _validate_python_probe(source: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        return (f"python -c payload is not valid Python: {exc.msg}",)

    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
            blocked = sorted(names & _PYTHON_DANGEROUS_IMPORTS)
            if blocked:
                errors.append("python probe imports mutation-capable module(s): " + ", ".join(blocked))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _PYTHON_DANGEROUS_IMPORTS:
                errors.append(f"python probe imports mutation-capable module: {module}")
        elif isinstance(node, ast.Call):
            leaf = _call_leaf_name(node.func)
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                errors.append("python probe invokes a dynamically resolved callable")
            if leaf in _PYTHON_MUTATING_CALLS:
                errors.append(f"python probe calls mutation-capable function: {leaf}")
            if leaf == "open":
                mode_node = (
                    node.args[1] if len(node.args) > 1 else
                    next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
                )
                if mode_node is not None:
                    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
                        errors.append("python probe uses a non-literal open mode")
                    elif any(flag in mode_node.value for flag in "wax+"):
                        errors.append(f"python probe opens a file for mutation: {mode_node.value!r}")
    return tuple(dict.fromkeys(errors))


def _tokenize_shell(command: str) -> tuple[list[str], tuple[str, ...]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer), ()
    except ValueError as exc:
        return [], (f"invalid shell syntax: {exc}",)


def _split_segments(tokens: list[str]) -> tuple[list[list[str]], tuple[str, ...]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {"|", "&&", "||"}:
            if not current:
                return [], ("empty command in diagnostic pipeline",)
            segments.append(current)
            current = []
            continue
        if token in {";", "&", ">", ">>", "<", "<<", "<>", ">&", "&>"} or any(
            char in token for char in (">", "<")
        ):
            return [], (f"shell redirection/control operator is not allowed: {token!r}",)
        current.append(token)
    if not current:
        return [], ("diagnostic command ends with a shell operator",)
    segments.append(current)
    return segments, ()


def _validate_segment(tokens: list[str]) -> tuple[str, ...]:
    if tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        return ("environment assignments are not allowed in probe commands",)
    executable = os.path.basename(tokens[0])
    args = tokens[1:]
    if executable in _SHELL_MUTATION_WORDS:
        return (f"mutation-capable command is not allowed: {executable}",)

    if executable in _SIMPLE_READ_COMMANDS:
        if executable == "sort" and any(
            arg == "-o" or arg.startswith("--output") for arg in args
        ):
            return ("sort output-file options are not allowed",)
        if executable == "find" and any(
            arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
            for arg in args
        ):
            return ("find mutation/execution actions are not allowed",)
        return ()

    if executable == "find":
        if any(
            arg == "-delete" or arg.startswith(
                ("-exec", "-ok", "-fprint", "-fprintf", "-fls")
            )
            for arg in args
        ):
            return ("find mutation/execution actions are not allowed",)
        return ()

    if executable in {"dpkg", "dpkg-query"}:
        allowed = {"-s", "--status", "-l", "--list", "-L", "--listfiles", "-S", "--search", "-W", "--show"}
        if not args or args[0] not in allowed:
            return (f"{executable} probe must use a read-only query option",)
        return ()

    if executable == "ldconfig":
        return () if args and args[0] in {"-p", "--print-cache", "-V", "--version"} else (
            "ldconfig is allowed only for cache/version queries",
        )

    if executable == "command":
        return () if args and args[0] in {"-v", "-V"} else (
            "command is allowed only with -v or -V",
        )

    if executable in {"pip", "pip3"}:
        allowed = {"check", "debug", "freeze", "index", "list", "show", "--version"}
        return () if args and args[0] in allowed else (
            f"{executable} is allowed only for read-only metadata queries",
        )

    if executable.startswith("python"):
        if "-c" in args:
            index = args.index("-c")
            if index + 1 >= len(args):
                return ("python -c requires a source argument",)
            return _validate_python_probe(args[index + 1])
        if len(args) >= 3 and args[0:2] == ["-m", "pip"]:
            allowed = {"check", "debug", "freeze", "index", "list", "show", "--version"}
            return () if args[2] in allowed else (
                "python -m pip is allowed only for read-only metadata queries",
            )
        if args in (["--version"], ["-V"]):
            return ()
        return ("python probes must use -c, -m pip metadata queries, or --version",)

    if executable in {"npm", "pnpm", "yarn"}:
        allowed = {"list", "ls", "why", "view", "info", "explain", "--version"}
        return () if args and args[0] in allowed else (
            f"{executable} is allowed only for read-only dependency queries",
        )

    if executable == "cargo":
        allowed = {"metadata", "tree", "pkgid", "locate-project", "--version"}
        if not args or args[0] not in allowed:
            return (
                "cargo is allowed only for metadata/tree/pkgid/locate-project queries",
            )
        if args[0] in {"metadata", "tree"} and not (
            "--locked" in args and "--offline" in args
        ):
            return ("cargo metadata/tree probes require --locked and --offline",)
        return ()

    if executable == "go":
        allowed = {"env", "list", "version"}
        if not args or args[0] not in allowed:
            return ("go is allowed only for env/list/version queries",)
        if args[0] == "list" and not any(
            arg == "-mod=readonly" or arg.startswith("-mod=readonly")
            for arg in args
        ):
            return ("go list probes require -mod=readonly",)
        return ()

    if executable in {"mvn", "mvnw"}:
        query = " ".join(args)
        version_query = any(
            arg in {"--version", "-version", "-v"} for arg in args
        )
        allowed = (
            "dependency:tree" in query
            or "dependency:list" in query
            or "help:effective-pom" in query
            or version_query
        )
        if any("outputFile" in arg for arg in args):
            allowed = False
        if (
            allowed
            and not version_query
            and not any(arg in {"-o", "--offline"} for arg in args)
        ):
            allowed = False
        return () if allowed else (
            f"{executable} requires an offline read-only dependency/help query",
        )

    if executable in {"gradle", "gradlew"}:
        allowed_tasks = {"dependencies", "dependencyInsight", "properties", "tasks"}
        allowed = (
            any(arg in allowed_tasks for arg in args)
            or any(arg in {"--version", "-v"} for arg in args)
        )
        if allowed and not any(arg in {"--offline", "--version", "-v"} for arg in args):
            allowed = False
        return () if allowed else (
            f"{executable} requires an offline read-only model query",
        )

    return (f"diagnostic executable is not in the read-only allowlist: {executable}",)


def validate_probe_command(command: str) -> ProbeValidation:
    command = (command or "").strip()
    if not command:
        return ProbeValidation(False, ("probe command is empty",))
    if "\n" in command or "\r" in command:
        return ProbeValidation(False, ("multi-line probe commands are not allowed",))
    if "`" in command or "$(" in command or "$((" in command:
        return ProbeValidation(False, ("command substitution/arithmetic expansion is not allowed",))

    tokens, errors = _tokenize_shell(command)
    if errors:
        return ProbeValidation(False, errors)
    segments, errors = _split_segments(tokens)
    if errors:
        return ProbeValidation(False, errors)

    all_errors: list[str] = []
    for segment in segments:
        all_errors.extend(_validate_segment(segment))
    errors = tuple(dict.fromkeys(all_errors))
    return ProbeValidation(not errors, errors)
