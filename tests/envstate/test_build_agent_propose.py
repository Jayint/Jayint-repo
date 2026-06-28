import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.envstate.build_agent import BuildAgent
from src.envstate.repair_scope import RepairScope

class _Msg:
    def __init__(self, c): self.content = c; self.reasoning = None; self.model_extra = {}
class _Choice:
    def __init__(self, c): self.message = _Msg(c)
class _Resp:
    def __init__(self, c): self.choices = [_Choice(c)]; self.usage = None
class _Client:
    def __init__(self, *cs): self._q = list(cs); self.chat = self
    @property
    def completions(self): return self
    def create(self, **kw): return _Resp(self._q.pop(0))

_GOOD = '''Final Patch:
```json
{"patch": {"add_providers": [{"id": "apt:libplacebo-dev", "kind": "apt",
 "command": "apt-get install -y libplacebo-dev", "provides": ["syslib:libplacebo"],
 "override": true}]}}
```'''
_BAD_KEYS = '```json\n{"patch": {"add_providers": [{"kind": "apt"}]}}\n```'   # missing id/command

def _scope():
    return RepairScope("syslib:libplacebo", "apt-get install -y libplacebodev",
                       "Unable to locate package", (), ("apt:libplacebodev",), (),
                       frozenset({"ev.1.0"}))

def test_propose_returns_typed_proposal():
    a = BuildAgent(_Client(_GOOD), "fake", synthesizer=None)
    p = a.propose(_scope(), exec_readonly=lambda c: (0, ""))
    assert p is not None and p.add_providers[0].override is True

def test_propose_parsefail_retries_once_then_rejects():
    a = BuildAgent(_Client(_BAD_KEYS, _BAD_KEYS), "fake", synthesizer=None)  # JSON found, keys missing
    assert a.propose(_scope(), exec_readonly=lambda c: (0, "")) is None

def test_propose_accepts_rejection_errors_kwarg():
    a = BuildAgent(_Client(_GOOD), "fake", synthesizer=None)
    p = a.propose(_scope(), exec_readonly=lambda c: (0, ""),
                  rejection_errors=("provider apt:x provides unknown node",))
    assert p is not None  # the re-prompt feedback is accepted and a valid patch returns
