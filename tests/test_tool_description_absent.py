"""MAP-17: a function tool with no description reaches every wire with no
description key, never ``"description": null``.

The contract cases ``cases/*/tool_no_description.json`` and
``cases/*/live_tool_no_description.json`` pin the ``None`` wire. These tests
add what canonical JSON cannot carry (``""``, which serializes the same as
``None``) and the paths no case pins (a Gemini cached prefix, a batch body).
"""

from __future__ import annotations

import json

import pytest

from lm15 import Config, FunctionTool, LiveConfig, Message, Request
from lm15.providers import GeminiLM, OpenAILM
from lm15.vet import adapter_for_provider

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}}


def tool(description):
    return FunctionTool(name="get_weather", description=description, parameters=SCHEMA)


def declarations(body):
    """Every object in a wire body that declares the tool."""
    if isinstance(body, dict):
        if body.get("name") == "get_weather" and any(k in body for k in ("parameters", "input_schema", "parametersJsonSchema")):
            yield body
        for value in body.values():
            yield from declarations(value)
    elif isinstance(body, list):
        for value in body:
            yield from declarations(value)


def only_declaration(body):
    found = list(declarations(body))
    assert len(found) == 1, body
    return found[0]


@pytest.mark.parametrize("provider", ["anthropic", "openai", "openai-chat", "gemini", "groq", "deepseek-anthropic", "xai"])
@pytest.mark.parametrize("description", [None, ""])
def test_absent_description_is_left_off_every_dialect(provider, description):
    request = Request(model="deepseek-v4-flash", messages=(Message.user("hi"),), tools=(tool(description),), config=Config(max_tokens=64))
    body = json.loads(adapter_for_provider(provider, "k").build_request(request, stream=False).body)
    declared = only_declaration(body)
    assert "description" not in declared
    keys = [k for k in declared if k != "type"]
    assert keys[0] == "name" and keys[1] in ("parameters", "input_schema", "parametersJsonSchema")


@pytest.mark.parametrize("provider", ["anthropic", "openai", "openai-chat", "gemini"])
def test_present_description_keeps_its_slot(provider):
    request = Request(model="m-1", messages=(Message.user("hi"),), tools=(tool("Weather for a city"),))
    body = json.loads(adapter_for_provider(provider, "k").build_request(request, stream=False).body)
    keys = list(only_declaration(body))
    assert keys[keys.index("name") + 1] == "description"
    assert only_declaration(body)["description"] == "Weather for a city"


@pytest.mark.parametrize("description", [None, ""])
def test_live_setup_frames_leave_it_off(description):
    for lm, model in ((OpenAILM(api_key="k"), "gpt-realtime-mini"), (GeminiLM(api_key="k"), "gemini-3.1-flash-live-preview")):
        frames = lm._live_setup_frames(LiveConfig(model=model, tools=(tool(description),)))
        assert "description" not in only_declaration(frames)


@pytest.mark.parametrize("description", [None, ""])
def test_gemini_cached_prefix_leaves_it_off(description):
    prefix = Request(model="gemini-2.5-flash", messages=(Message.user("a long stable prefix"),), tools=(tool(description),))
    body = json.loads(GeminiLM(api_key="k")._cache_create_request(prefix, 300, None).body)
    assert "description" not in only_declaration(body)


def test_anthropic_batch_body_leaves_it_off():
    from lm15.types import BatchRequest

    nested = Request(model="claude-haiku-4-5", messages=(Message.user("hi"),), tools=(tool(None),), config=Config(max_tokens=64))
    lm = adapter_for_provider("anthropic", "k")
    body = json.loads(lm._batch_submit_request(BatchRequest(requests=(nested,)), None).body)
    assert "description" not in only_declaration(body)
