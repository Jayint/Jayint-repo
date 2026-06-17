from src.envstate.world_model import apply_deterministic, initial_map, Fact


def _base():
    return initial_map(base_image="b", workdir="/r", language="python 3.11",
                       build_system="pip", repo_layout=("tests/",), required=(Fact("opencv-python", ""),))


def test_apply_deterministic_folds_import_results():
    snap = type("S", (), {"env": {"python_version": "3.11"}, "installed": (Fact("opencv-python", ""),),
                          "system_installed": (), "import_results": (("cv2", False),)})()
    man = type("M", (), {"required": (Fact("opencv-python", ""),), "build_system": "pip"})()
    m = apply_deterministic(_base(), snap, man)
    assert m.import_results == (("cv2", False),)
