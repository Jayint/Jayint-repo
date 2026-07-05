from __future__ import annotations

from src.eval.language_package_eval.go.gomod import (
    Exclude,
    Replace,
    Require,
    parse_go_mod,
)

GOMOD_BLOCK = """\
module github.com/acme/app

go 1.21

toolchain go1.21.4

require (
    github.com/spf13/cobra v1.8.0
    github.com/inconshreveable/mousetrap v1.1.0 // indirect
)

require github.com/spf13/pflag v1.0.5

replace github.com/old/mod => github.com/new/mod v1.5.0
replace github.com/local/mod => ../local

exclude github.com/bad/mod v1.1.0
"""


def test_parse_go_mod_full(tmp_path):
    p = tmp_path / "go.mod"
    p.write_text(GOMOD_BLOCK)
    gm = parse_go_mod(p)

    assert gm.module_path == "github.com/acme/app"
    assert gm.go_version == "1.21"
    assert gm.toolchain == "go1.21.4"
    assert Require("github.com/spf13/cobra", "v1.8.0", False) in gm.requires
    assert (
        Require("github.com/inconshreveable/mousetrap", "v1.1.0", True) in gm.requires
    )
    assert Require("github.com/spf13/pflag", "v1.0.5", False) in gm.requires
    assert (
        Replace("github.com/old/mod", None, "github.com/new/mod", "v1.5.0")
        in gm.replaces
    )
    assert Replace("github.com/local/mod", None, "../local", None) in gm.replaces
    assert Exclude("github.com/bad/mod", "v1.1.0") in gm.excludes


def test_parse_go_mod_no_go_directive(tmp_path):
    (tmp_path / "go.mod").write_text("module github.com/x/y\n")
    gm = parse_go_mod(tmp_path / "go.mod")
    assert gm.go_version == ""
    assert gm.requires == ()
