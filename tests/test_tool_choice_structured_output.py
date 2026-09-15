"""MAP-8: tool-choice silent cells + the canonical response_format (INV-050).

Receipts: lm15-contract/research/tool-choice/ (2026-09-02).
"""
from __future__ import annotations

import json

import pytest

from lm15 import AnthropicLM, Config, FunctionTool, GeminiLM, Message, OpenAIChatLM, OpenAILM, Request, ToolChoice, UnsupportedFeatureError, XaiLM
from lm15.testing import FakeTransport

TOOLS = (FunctionTool(name="lookup"), FunctionTool(name="weather"))
SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}


def _req(model: str, **cfg) -> Request:
    return Request(model=model, messages=[Message.user("q")], tools=TOOLS, config=Config(**cfg))


def _body(lm, request: Request) -> dict:
    return json.loads(lm.build_request(request, stream=False).body)


# ─── INV-050 ─────────────────────────────────────────────────────────

def test_response_format_has_exactly_two_shapes() -> None:
    Config(response_format={"type": "json_object"})
    Config(response_format={"type": "json_schema", "schema": SCHEMA, "name": "person", "strict": True})
    for bad in ({"format": {"type": "json_schema"}}, {"response_mime_type": "application/json"},
                {"type": "json_schema", "json_schema": {"name": "x"}}, {"type": "object", "properties": {}},
                {"type": "json_object", "schema": SCHEMA}, {"type": "json_schema"}):
        with pytest.raises((ValueError, TypeError), match="response_format"):
            Config(response_format=bad)


def test_schema_is_verbatim_on_every_wire() -> None:
    rf = {"type": "json_schema", "schema": {**SCHEMA, "properties": {"age": {"type": "integer", "minimum": 0}}}, "name": "p", "strict": True}
    assert _body(OpenAILM(api_key="k", transport=FakeTransport([])), _req("gpt-5.6-sol", response_format=rf))["text"] == {
        "format": {"type": "json_schema", "name": "p", "schema": rf["schema"], "strict": True}}
    assert _body(OpenAIChatLM(api_key="k", transport=FakeTransport([])), _req("gpt-5.4-mini", response_format=rf))["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "p", "schema": rf["schema"], "strict": True}}
    # Anthropic rejects minimum server-side; lm15 does not strip it
    assert _body(AnthropicLM(api_key="k", transport=FakeTransport([])), _req("claude-sonnet-5", response_format=rf))["output_config"] == {
        "format": {"type": "json_schema", "schema": rf["schema"]}}
    g = _body(GeminiLM(api_key="k", transport=FakeTransport([])), _req("gemini-2.5-flash", response_format=rf))["generationConfig"]
    assert g["responseMimeType"] == "application/json" and g["responseJsonSchema"] == rf["schema"]


def test_json_object_per_provider() -> None:
    rf = {"type": "json_object"}
    assert _body(OpenAILM(api_key="k", transport=FakeTransport([])), _req("gpt-5.6-sol", response_format=rf))["text"] == {"format": {"type": "json_object"}}
    assert _body(GeminiLM(api_key="k", transport=FakeTransport([])), _req("gemini-2.5-flash", response_format=rf))["generationConfig"] == {"responseMimeType": "application/json"}
    with pytest.raises(UnsupportedFeatureError, match="any-JSON"):
        _body(AnthropicLM(api_key="k", transport=FakeTransport([])), _req("claude-sonnet-5", response_format=rf))


def test_openai_name_defaults_to_response() -> None:
    b = _body(OpenAILM(api_key="k", transport=FakeTransport([])), _req("gpt-5.6-sol", response_format={"type": "json_schema", "schema": SCHEMA}))
    assert b["text"]["format"]["name"] == "response" and "strict" not in b["text"]["format"]


# ─── tool choice silent cells ────────────────────────────────────────

def test_gemini_parallel_false_is_dropped_and_recorded() -> None:
    # MAP-13 (was a MAP-8 rule 2 refusal): GenerateContent has no parallel
    # knob and returned two calls regardless (live 2026-09-02).  A
    # preference: dropped, recorded, and toolConfig goes without it.
    from tests._adapt import adapted, refuses
    lm = GeminiLM(api_key="k", transport=FakeTransport([]))
    out = adapted(lm, _req("gemini-2.5-flash", tool_choice=ToolChoice(parallel=False)))
    assert out["config.tool_choice.parallel"].action == "dropped"
    assert out["__body__"]["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}
    refuses(lm, _req("gemini-2.5-flash", tool_choice=ToolChoice(parallel=False)), "config.tool_choice.parallel")
    assert _body(lm, _req("gemini-2.5-flash", tool_choice=ToolChoice(parallel=True)))["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}


def test_xai_allowlist_is_client_side_and_forced_with_format_raises() -> None:
    # MAP-13 (was a MAP-8 rule 1 refusal): api.x.ai ignores allowed_tools
    # (live 2026-09-02).  The allowlist is applied by sending only those
    # tools — what an allowlist means — and recorded.  A forced tool beside
    # a response_format still raises (rule 4b: the server drops the call).
    from tests._adapt import adapted
    lm = XaiLM(api_key="k", transport=FakeTransport([]))
    out = adapted(lm, _req("grok-4.6", tool_choice=ToolChoice(allowed=("lookup",))))
    assert out["config.tool_choice.allowed"].action == "client_side"
    assert [t["function"]["name"] for t in out["__body__"]["tools"]] == ["lookup"]
    assert out["__body__"]["tool_choice"] == "auto"
    out = adapted(lm, _req("grok-4.6", tool_choice=ToolChoice(mode="required", allowed=("lookup", "weather"))))
    assert out["config.tool_choice.allowed"].applied == ["lookup", "weather"]
    assert out["__body__"]["tool_choice"] == "required"
    assert _body(lm, _req("grok-4.6", tool_choice=ToolChoice(mode="required", allowed=("lookup",))))["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}
    with pytest.raises(UnsupportedFeatureError, match="forced tool") as err:
        _body(lm, _req("grok-4.6", tool_choice=ToolChoice(mode="required"), response_format={"type": "json_object"}))
    assert err.value.feature == "config.tool_choice.mode"
