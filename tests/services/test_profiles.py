"""The pre-approved Platform registry that ships with the app (`data/platforms.json`)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.seed_platforms import DEFAULT_REGISTRY
from tests.support.ucp_spec import schema_errors
from tillhand.core.constants import SUGGESTIONS, TILLHAND_SITE
from tillhand.services.profiles import load_pre_approved, registry_entries
from tillhand.utils.namespace import schema_authority_matches

pytestmark = pytest.mark.anyio

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "platforms.json"
HARNESS_PROFILE_URL = f"{TILLHAND_SITE}/profiles/harness.json"


def entries() -> list[dict[str, object]]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["platforms"]


def test_the_seed_script_loads_the_shipped_registry_by_default() -> None:
    assert DEFAULT_REGISTRY == Path("data/platforms.json")


def test_each_registry_entry_keeps_its_profile_document_as_written() -> None:
    loaded = registry_entries(REGISTRY.read_bytes())

    assert [json.loads(document) for _, document in loaded] == [e["profile"] for e in entries()]


async def test_the_shipped_registry_pre_approves_the_harness() -> None:
    registry = await load_pre_approved(REGISTRY)

    assert await registry.get(HARNESS_PROFILE_URL) is not None
    assert await registry.get("https://platform.example/profiles/agent.json") is None


@pytest.mark.parametrize("entry", entries(), ids=lambda e: str(e["profile_url"]))
def test_every_registered_profile_is_one_the_ucp_schema_accepts(entry: dict[str, object]) -> None:
    assert schema_errors(entry["profile"], "profile", "platform_schema") == []


@pytest.mark.parametrize("entry", entries(), ids=lambda e: str(e["profile_url"]))
def test_every_registered_capability_passes_the_namespace_check(entry: dict[str, object]) -> None:
    profile = entry["profile"]
    assert isinstance(profile, dict)
    for name, versions in profile["ucp"]["capabilities"].items():
        for capability in versions:
            assert schema_authority_matches(name, capability["schema"]), name


def test_the_harness_declares_our_suggestions_extension_as_the_server_defines_it() -> None:
    (harness,) = [e for e in entries() if e["profile_url"] == HARNESS_PROFILE_URL]
    profile = harness["profile"]
    assert isinstance(profile, dict)
    (declared,) = profile["ucp"]["capabilities"][SUGGESTIONS.name]

    assert declared["schema"] == SUGGESTIONS.schema_url
    assert declared["version"] == SUGGESTIONS.version
    assert declared["extends"] == list(SUGGESTIONS.extends)


async def test_a_registry_listing_a_url_twice_stops_the_app_starting(tmp_path: Path) -> None:
    (entry,) = entries()
    duplicated = tmp_path / "platforms.json"
    duplicated.write_text(json.dumps({"platforms": [entry, entry]}), encoding="utf-8")

    with pytest.raises(ValueError, match="more than once"):
        await load_pre_approved(duplicated)


async def test_a_registry_with_an_invalid_profile_stops_the_app_starting(tmp_path: Path) -> None:
    (entry,) = entries()
    broken = tmp_path / "platforms.json"
    broken.write_text(json.dumps({"platforms": [{**entry, "profile": {"ucp": {}}}]}), encoding="utf-8")

    with pytest.raises(ValidationError):
        await load_pre_approved(broken)
