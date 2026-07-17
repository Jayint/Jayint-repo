from graph.python.native.wheel_oracle import (
    _artifact_filename,
    _wheel_matches_platform,
    risk_from_packages,
)

LINUX_X86 = "x86_64-manylinux_2_28"


def test_artifact_filename_prefers_explicit_filename():
    assert _artifact_filename({"filename": "foo-1.0.tar.gz"}) == "foo-1.0.tar.gz"


def test_artifact_filename_derives_from_url():
    assert _artifact_filename({"url": "https://x/foo-1.0-py3-none-any.whl"}) == "foo-1.0-py3-none-any.whl"


def test_artifact_filename_none_for_non_dict():
    assert _artifact_filename(None) is None


def test_wheel_matches_platform_universal_wheel():
    assert _wheel_matches_platform("foo-1.0-py3-none-any.whl", LINUX_X86) is True


def test_wheel_matches_platform_arch_mismatch():
    assert _wheel_matches_platform("foo-1.0-cp311-cp311-macosx_11_0_arm64.whl", LINUX_X86) is False


def test_wheel_matches_platform_sdist_never_matches():
    assert _wheel_matches_platform("foo-1.0.tar.gz", LINUX_X86) is False


def test_risk_from_packages_build_from_source_when_no_matching_wheel():
    raw = [{
        "name": "psycopg2",
        "sdist": {"filename": "psycopg2-2.9.9.tar.gz", "hash": "sha256:abc"},
        "wheels": [{"filename": "psycopg2-2.9.9-cp311-cp311-macosx_11_0_arm64.whl"}],
    }]
    risk = risk_from_packages(raw, LINUX_X86)
    assert risk["psycopg2"]["build_from_source"] is True
    assert risk["psycopg2"]["artifact"] == "psycopg2-2.9.9.tar.gz"
    assert risk["psycopg2"]["hash"] == "sha256:abc"


def test_risk_from_packages_prefers_matching_wheel():
    raw = [{
        "name": "requests",
        "sdist": {"filename": "requests-2.31.0.tar.gz"},
        "wheels": [{"filename": "requests-2.31.0-py3-none-any.whl", "hash": "sha256:xyz"}],
    }]
    risk = risk_from_packages(raw, LINUX_X86)
    assert risk["requests"]["build_from_source"] is False
    assert risk["requests"]["artifact"] == "requests-2.31.0-py3-none-any.whl"


def test_risk_from_packages_skips_unnamed_entries():
    assert risk_from_packages([{"source": {}}], LINUX_X86) == {}


# --------------------------------------------------------------------------- #
# Python-interpreter-tag awareness (Fix A): a wheel whose cp-tag targets a
# different interpreter than the container must NOT count as a matching wheel,
# even when its arch/platform tag matches. Regression for onnxruntime 1.24.3
# (cp311+ wheels, requires_python metadata lies ">=3.10") pinned onto py3.10.
# --------------------------------------------------------------------------- #
ONNX_CP311 = "onnxruntime-1.24.3-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
ONNX_CP310 = "onnxruntime-1.23.2-cp310-cp310-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"


def test_wheel_matches_python_exact_minor():
    assert _wheel_matches_platform(ONNX_CP310, LINUX_X86, "3.10") is True


def test_wheel_matches_python_wrong_minor_rejected():
    # cp311 wheel, arch matches, but target interpreter is 3.10 -> NOT installable.
    assert _wheel_matches_platform(ONNX_CP311, LINUX_X86, "3.10") is False
    # ...and IS installable on the interpreter it actually targets.
    assert _wheel_matches_platform(ONNX_CP311, LINUX_X86, "3.11") is True


def test_wheel_matches_python_abi3_forward_compatible():
    # cp38-abi3 installs on any CPython >= 3.8.
    fn = "cryptography-42.0.0-cp38-abi3-manylinux_2_28_x86_64.whl"
    assert _wheel_matches_platform(fn, LINUX_X86, "3.12") is True
    # ...but a cp312-abi3 wheel does NOT go backwards onto 3.10.
    fn_hi = "cryptography-42.0.0-cp312-abi3-manylinux_2_28_x86_64.whl"
    assert _wheel_matches_platform(fn_hi, LINUX_X86, "3.10") is False


def test_wheel_matches_python_universal_tag():
    assert _wheel_matches_platform("foo-1.0-py3-none-any.whl", LINUX_X86, "3.10") is True
    assert _wheel_matches_platform("foo-1.0-py2.py3-none-any.whl", LINUX_X86, "3.13") is True


def test_wheel_matches_platform_target_python_none_is_legacy():
    # Backward compatible: no target_python -> platform-only (cp-tag ignored).
    assert _wheel_matches_platform(ONNX_CP311, LINUX_X86) is True


def test_risk_installable_false_when_no_artifact_for_interpreter():
    # onnxruntime 1.24.3: cp311+ wheels only, NO sdist. On py3.10 there is no
    # installable artifact at all -> installable False, build_from_source False
    # (nothing to build).
    raw = [{
        "name": "onnxruntime",
        "wheels": [
            {"filename": ONNX_CP311},
            {"filename": "onnxruntime-1.24.3-cp312-cp312-manylinux_2_28_x86_64.whl"},
        ],
    }]
    risk = risk_from_packages(raw, LINUX_X86, "3.10")
    assert risk["onnxruntime"]["installable"] is False
    assert risk["onnxruntime"]["build_from_source"] is False


def test_risk_installable_true_via_sdist_build_when_wheel_missing():
    # pandas 2.0.3: cp<=311 wheels + sdist. On py3.12 no matching wheel, but the
    # sdist makes it installable-by-building (build_from_source True).
    raw = [{
        "name": "pandas",
        "sdist": {"filename": "pandas-2.0.3.tar.gz", "hash": "sha256:abc"},
        "wheels": [{"filename": "pandas-2.0.3-cp311-cp311-manylinux_2_28_x86_64.whl"}],
    }]
    risk = risk_from_packages(raw, LINUX_X86, "3.12")
    assert risk["pandas"]["installable"] is True
    assert risk["pandas"]["build_from_source"] is True


def test_risk_installable_true_via_matching_wheel_on_older_interpreter():
    # Same pandas pin on py3.10: the cp310 wheel matches -> wheel install.
    raw = [{
        "name": "pandas",
        "sdist": {"filename": "pandas-2.0.3.tar.gz"},
        "wheels": [{"filename": "pandas-2.0.3-cp310-cp310-manylinux_2_28_x86_64.whl"}],
    }]
    risk = risk_from_packages(raw, LINUX_X86, "3.10")
    assert risk["pandas"]["installable"] is True
    assert risk["pandas"]["build_from_source"] is False
