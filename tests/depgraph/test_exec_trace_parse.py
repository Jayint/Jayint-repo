from graph.python.enrich.exec_trace import parse
from graph.python.enrich.diagnose import RepoContext

_CTX = RepoContext(local_names=frozenset({"myapp"}))

_CASCADING = '''\
tests/test_x.py:2: in <module>
    from myapp import thing
myapp/__init__.py:4: in <module>
    from .db import Session
myapp/db.py:1: in <module>
    import psycopg2
E   ModuleNotFoundError: No module named 'psycopg2'
'''


def test_parse_reconstructs_chain_and_causal():
    pf = parse("python -m pytest", _CASCADING, "collection", _CTX)
    assert pf.failure_type == "module_not_found"
    assert pf.causal == "import:psycopg2"
    assert pf.chain[-1][2] == "import:psycopg2"          # root is deepest
    assert "tests/test_x.py" in "".join(s[0] for s in pf.chain)  # target at top
    assert "tests/test_x.py" in pf.blast_radius
