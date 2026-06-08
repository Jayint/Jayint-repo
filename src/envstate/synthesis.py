from __future__ import annotations
from typing import List

from src.envstate.ledger import ActionLedger


def build_commands_from_ledger(ledger: ActionLedger, distill=None) -> List[str]:
    """Authoritative, order-preserving build-command extraction (design §15).

    Includes only successful (rc==0) env-mutating commands, in trajectory order.
    Read-only commands (mutation_class is None and not a mutation) and test
    commands (also mutation_class None) are excluded. Duplicates are intentionally
    preserved — setup side effects are NOT algebraically mergeable (see CLAUDE.md).

    `distill`, if given (e.g. Synthesizer._extract_recordable_setup_commands), is
    applied to each kept command and must return a list of 0+ distilled command
    strings. This strips a trailing test invocation from a compound 'install && test'
    action so it never becomes a Dockerfile RUN step, matching the legacy path.
    Without it, the raw command is kept.
    """
    commands: List[str] = []
    for event in ledger.events():
        if event.rc != 0:
            continue
        if not event.mutation_class:
            continue  # read-only or test command
        if distill is not None:
            commands.extend(distill(event.cmd))
        else:
            commands.append(event.cmd)
    return commands
