import pytest
from python_deps.depgraph.patch import parse_patch_proposal, PatchParseError

def test_missing_required_key_raises_structured():
    with pytest.raises(PatchParseError) as ei:
        parse_patch_proposal({"patch": {"add_requirements": [{"type": "SystemLib", "layer": "system"}]}})
    assert any("id" in e for e in ei.value.errors)

def test_provider_missing_command_raises_structured():
    with pytest.raises(PatchParseError):
        parse_patch_proposal({"patch": {"add_providers": [{"id": "apt:x", "kind": "apt"}]}})

def test_override_round_trips():
    p = parse_patch_proposal({"patch": {"add_providers": [
        {"id": "apt:libpq-dev", "kind": "apt", "command": "apt-get install -y libpq-dev",
         "provides": ["syslib:libpq"], "override": True}]}})
    assert p.add_providers[0].override is True

def test_well_formed_without_override_defaults_false():
    p = parse_patch_proposal({"patch": {"add_providers": [
        {"id": "apt:x", "kind": "apt", "command": "apt-get install -y x"}]}})
    assert p.add_providers[0].override is False


@pytest.mark.parametrize("payload", [
    "not-an-object",
    {"patch": "not-an-object"},
    {"patch": {"add_requirements": ["not-an-object"]}},
    {"patch": {"add_providers": [None]}},
    {"patch": {"add_edges": [42]}},
    {"patch": {"script_patches": [True]}},
])
def test_malformed_container_or_item_raises_structured(payload):
    with pytest.raises(PatchParseError) as exc_info:
        parse_patch_proposal(payload)
    assert "expected an object" in str(exc_info.value)


@pytest.mark.parametrize("field", [
    "add_requirements", "add_providers", "add_edges", "script_patches",
])
def test_patch_collection_fields_require_arrays(field):
    with pytest.raises(PatchParseError) as exc_info:
        parse_patch_proposal({"patch": {field: {"unexpected": "object"}}})
    assert f"{field}: expected an array" in str(exc_info.value)
