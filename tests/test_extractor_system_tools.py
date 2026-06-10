from src.envstate.extractor import EXTRACTOR_COMMANDS, run_extractor, SYSTEM_TOOL_PROBES


def test_system_tools_command_present_and_curated():
    assert "system_tools" in EXTRACTOR_COMMANDS
    # the failure-artifact tools we care about must be probed
    for t in ("gcc", "pg_config", "pkg-config", "make", "cmake", "mysql_config"):
        assert t in SYSTEM_TOOL_PROBES


def test_system_tools_parsed_as_present_subset():
    # fake exec returns only gcc + pg_config present (one name per line)
    table = {EXTRACTOR_COMMANDS["system_tools"]: (0, "gcc\npg_config\n")}
    res = run_extractor(lambda cmd: table.get(cmd, (1, "")), fields=("system_tools",))
    assert res.fields["system_tools"] == "gcc\npg_config"
