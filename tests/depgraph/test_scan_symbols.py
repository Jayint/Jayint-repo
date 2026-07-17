from graph.python.read.scan import scan_to_nodes
from graph.schema import NodeType


def test_import_node_carries_used_symbols(tmp_path):
    (tmp_path / "app.py").write_text("import cv2\ncv2.imread('x')\n")
    graph = scan_to_nodes(str(tmp_path))
    imp = next(n for n in graph.nodes if n.type is NodeType.IMPORT and n.name == "cv2")
    assert imp.data.get("symbols") == ("imread",)
