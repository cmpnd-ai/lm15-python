"""The vet shim's provider_tables and resolve_openai_chat_model ops.

provider_tables is what lm15-contract publishes as tables/providers.json and
what the TypeScript, Rust and Go ports generate their tables from, so it must
be exact JSON of the tables the reference itself uses.
"""

from __future__ import annotations

import json

import pytest

from lm15 import compat, registry, router
from lm15.errors import UnknownModelError
from lm15.vet import op_provider_tables, op_resolve_openai_chat_model


def test_tables_are_json_and_mirror_the_registry() -> None:
    tables = op_provider_tables({})
    assert json.loads(json.dumps(tables)) == tables  # JSON values only, nothing lost
    assert tables["schema"] == 1
    assert [row["id"] for row in tables["providers"]] == list(registry.PROVIDERS)
    for row in tables["providers"]:
        definition = registry.PROVIDERS[row["id"]]
        assert row["access"]["provider"] == definition.access.provider
        assert row["access"]["env_keys"] == list(definition.access.env_keys)
        assert row["kind"] == ("hosted" if definition.hosted else "bound" if definition.bound else "adapter-owned")
        assert row["note"] == definition.note


def test_compat_presets_list_only_the_knobs_they_set() -> None:
    tables = op_provider_tables({})
    assert set(tables["compat"]["chat"]) == set(compat.OPENAI_CHAT_PRESETS)
    deepinfra = tables["compat"]["chat"]["deepinfra"]
    assert "routing" not in deepinfra  # unset knobs inherit and are absent
    prefix, knobs = deepinfra["model_overrides"][0]
    assert isinstance(prefix, str) and isinstance(knobs, dict)
    assert tables["compat"]["anthropic"]["anthropic"] == {}


def test_router_tables_in_match_order() -> None:
    tables = op_provider_tables({})
    assert [r["prefix"] for r in tables["routing"]["default_rules"]] == [r.prefix for r in router.DEFAULT_RULES]
    assert tables["routing"]["litellm_prefixes"] == dict(router.LITELLM_PROVIDER_PREFIXES)


def test_resolve_openai_chat_model_reads_litellm_spellings() -> None:
    assert op_resolve_openai_chat_model({"model": "groq/openai/gpt-oss-20b", "env": {}}) == {
        "provider": "groq", "model": "openai/gpt-oss-20b", "source": "prefix"}
    assert op_resolve_openai_chat_model({"model": "gpt-4.1-mini", "env": {}})["provider"] == "openai-chat"
    with pytest.raises(UnknownModelError):
        op_resolve_openai_chat_model({"model": "bedrock/anthropic.claude-sonnet-5", "env": {}})
