"""MAP-13 — adapt freely, never invisibly (changes/2026-09-14-adapt-visibly.md).

The mechanism: the Adaptation record, the three-position switch, plan(),
the record on Response and on the stream's start event, the client-side
stop, and the refusal's `feature` path.  Adapter-by-adapter verdicts live
next to each adapter's tests; this file is the machinery.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import lm15
from lm15 import (
    Adaptation,
    AnthropicLM,
    Config,
    LMRouter,
    Message,
    OpenAILM,
    Request,
    Response,
    RouterConfig,
    StreamStartEvent,
    Usage,
)
from lm15.adaptation import ADAPTATION_ACTIONS, adapt, collecting, current_policy, nearest_effort
from lm15.errors import UnsupportedFeatureError
from lm15.providers import AsyncOpenAILM
from lm15.serde import response_from_dict, response_to_dict, stream_event_from_dict, stream_event_to_dict
from lm15.testing import FakeResponse, FakeTransport
from lm15.types import StreamDeltaEvent, StreamEndEvent, TextDelta, TextPart


# ─── the record ──────────────────────────────────────────────────────

def test_adaptation_is_validated_and_exported() -> None:
    a = Adaptation(field="config.seed", action="dropped", reason="no seed here", asked=7)
    assert (a.field, a.action, a.asked, a.applied) == ("config.seed", "dropped", 7, None)
    assert lm15.Adaptation is Adaptation and lm15.AdaptationPolicy
    assert set(ADAPTATION_ACTIONS) == {"dropped", "clamped", "substituted", "client_side", "satisfied", "defaulted"}
    with pytest.raises(ValueError, match="action"):
        Adaptation(field="x", action="ignored", reason="r")
    with pytest.raises(ValueError, match="reason"):
        Adaptation(field="x", action="dropped", reason="")


def test_response_and_start_event_carry_the_record_and_omit_it_when_empty() -> None:
    plain = Response(id=None, model="m", message=Message.assistant("hi"), finish_reason="stop", usage=Usage())
    assert plain.adaptations == () and "adaptations" not in response_to_dict(plain)
    noted = Response(id=None, model="m", message=Message.assistant("hi"), finish_reason="stop", usage=Usage(),
                     adaptations=(Adaptation(field="config.seed", action="dropped", reason="r", asked=7),))
    d = response_to_dict(noted)
    assert d["adaptations"] == [{"field": "config.seed", "action": "dropped", "reason": "r", "asked": 7}]
    assert response_from_dict(d) == noted
    assert "adaptations=['config.seed:dropped']" in repr(noted)
    start = StreamStartEvent(model="m", adaptations=noted.adaptations)
    assert stream_event_from_dict(stream_event_to_dict(start)) == start
    assert "adaptations" not in stream_event_to_dict(StreamStartEvent(model="m"))
    with pytest.raises(TypeError, match="Adaptation"):
        Response(id=None, model="m", message=Message.assistant("hi"), finish_reason="stop", usage=Usage(), adaptations=("x",))  # type: ignore[arg-type]


# ─── the switch ──────────────────────────────────────────────────────

def test_switch_note_silent_refuse() -> None:
    with collecting("note", provider="p") as scope:
        adapt("config.a", "dropped", "gone", asked=1)
        adapt("config.b", "defaulted", "filled", applied=2)
    assert [(a.field, a.action) for a in scope.records] == [("config.a", "dropped"), ("config.b", "defaulted")]
    with collecting("silent") as scope:
        adapt("config.a", "dropped", "gone")
    assert scope.records == []
    with collecting("refuse", provider="p") as scope:
        # A required field the caller left open is filled, not refused.
        adapt("config.max_tokens", "defaulted", "the wire requires it", applied=10)
        with pytest.raises(UnsupportedFeatureError, match=r"p: config.a would be dropped: gone \(adaptations='refuse'\)") as err:
            adapt("config.a", "dropped", "gone")
        assert err.value.feature == "config.a" and err.value.provider == "p"
    assert [a.action for a in scope.records] == ["defaulted"]
    # No scope open: nothing is kept, nothing raises (a builder called directly).
    assert current_policy() == "note"
    adapt("config.a", "dropped", "gone")
    with pytest.raises(ValueError, match="adaptations must be one of"):
        with collecting("loud"):  # type: ignore[arg-type]
            pass


def test_policy_reaches_every_constructor_and_the_router() -> None:
    req = Request(model="claude-sonnet-4-5", messages=(Message.user("hi"),), config=Config(max_tokens=10, seed=7))
    assert [a.action for a in AnthropicLM(api_key="k").plan(req)] == ["dropped"]
    assert AnthropicLM(api_key="k", adaptations="silent").plan(req) == ()
    with pytest.raises(UnsupportedFeatureError) as err:
        AnthropicLM(api_key="k", adaptations="refuse").plan(req)
    assert err.value.feature == "config.seed"
    with pytest.raises(ValueError, match="adaptations must be one of"):
        AnthropicLM(api_key="k", adaptations="maybe")  # type: ignore[arg-type]
    router = LMRouter(RouterConfig(env={}, api_keys={"anthropic": "k"}, adaptations="refuse"))
    assert router.lm("anthropic:m").adaptations == "refuse"
    with pytest.raises(UnsupportedFeatureError):
        router.plan(Request(model="anthropic:claude-sonnet-4-5", messages=(Message.user("hi"),), config=Config(max_tokens=10, seed=7)))
    with pytest.raises(ValueError):
        RouterConfig(adaptations="never")  # type: ignore[arg-type]
    # The OpenAI-chat door's client-keyword table points drop_params here.
    from lm15.router import _CLIENT_KEYWORDS
    assert "adaptations='silent'" in _CLIENT_KEYWORDS["drop_params"]


# ─── plan() and the record on complete / stream ──────────────────────

_ANTHROPIC_BODY = json.dumps({
    "id": "msg_1", "model": "claude-sonnet-4-5", "role": "assistant",
    "content": [{"type": "text", "text": "hello END world"}], "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}).encode()


def test_plan_matches_complete_and_the_record_rides_the_response() -> None:
    req = Request(model="claude-sonnet-4-5", messages=(Message.user("hi"),), config=Config(seed=7, temperature=1.5))
    lm = AnthropicLM(api_key="k", transport=FakeTransport([FakeResponse(status=200, body=_ANTHROPIC_BODY)]))
    plan = lm.plan(req)
    assert [(a.field, a.action, a.applied) for a in plan] == [
        ("config.max_tokens", "defaulted", 16384), ("config.seed", "dropped", None), ("config.temperature", "clamped", 1.0),
    ]
    response = lm.complete(req)
    assert response.adaptations == plan and response.text == "hello END world"
    # plan() sent nothing: the fake served exactly one request, to complete().
    assert len(lm.transport.requests) == 1


def test_stream_stamps_the_record_on_the_start_event() -> None:
    sse = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","model":"claude-sonnet-4-5","role":"assistant","content":[],"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
        b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'
        b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    req = Request(model="claude-sonnet-4-5", messages=(Message.user("hi"),), config=Config(max_tokens=10, seed=7))
    lm = AnthropicLM(api_key="k", transport=FakeTransport([FakeResponse(status=200, body=sse)]))
    events = list(lm.stream(req))
    assert events[0].type == "start" and [a.field for a in events[0].adaptations] == ["config.seed"]
    assert sum(e.type == "start" for e in events) == 1 and events[-1].type == "end"
    from lm15.result import materialize_response
    lm2 = AnthropicLM(api_key="k", transport=FakeTransport([FakeResponse(status=200, body=sse)]))
    assert [a.field for a in materialize_response(lm2.stream(req), req).adaptations] == ["config.seed"]


# ─── client-side stop on the Responses wire ──────────────────────────

_RESPONSES_BODY = json.dumps({
    "id": "resp_1", "model": "gpt-5", "status": "completed",
    "output": [{"type": "message", "content": [{"type": "output_text", "text": "alpha END beta"}]}],
    "usage": {"input_tokens": 3, "output_tokens": 9},
}).encode()


def test_complete_with_a_stop_streams_under_the_hood_and_cuts() -> None:
    # Decision 2026-09-14: a client-side stop on a non-streaming call is
    # honoured by streaming and closing at the cut — nothing past the
    # sequence is generated or billed; usage is not reported (never
    # estimated).  The fake serves the streamed body the wire would send.
    req = Request(model="gpt-5", messages=(Message.user("hi"),), config=Config(stop=("END",)))
    body = _responses_sse("alpha ", "END beta")
    lm = OpenAILM(api_key="k", transport=FakeTransport([FakeResponse(status=200, body=body)]))
    response = lm.complete(req)
    assert response.text == "alpha " and response.finish_reason == "stop"
    assert response.usage == Usage()  # not reported: the final frame was never read
    assert response.adaptations[0].field == "config.stop" and response.adaptations[0].action == "client_side"
    sent = json.loads(lm.transport.requests[0].body)
    assert sent["stream"] is True and "stop" not in sent


def test_complete_with_a_stop_that_never_hits_keeps_the_usage() -> None:
    req = Request(model="gpt-5", messages=(Message.user("hi"),), config=Config(stop=("END",)))
    body = _responses_sse("alpha ", "beta")
    lm = OpenAILM(api_key="k", transport=FakeTransport([FakeResponse(status=200, body=body)]))
    response = lm.complete(req)
    assert response.text == "alpha beta" and response.usage.output_tokens == 9


def _responses_sse(*texts: str) -> bytes:
    frames = [
        'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5"}}\n\n',
        'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","id":"m1","role":"assistant","content":[]}}\n\n',
    ]
    for t in texts:
        frames.append('event: response.output_text.delta\ndata: ' + json.dumps({"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": t}) + "\n\n")
    frames.append('event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5","status":"completed","output":[],"usage":{"input_tokens":3,"output_tokens":9}}}\n\n')
    return "".join(frames).encode()


def test_stream_cuts_at_a_stop_sequence_split_across_deltas_and_closes_early() -> None:
    req = Request(model="gpt-5", messages=(Message.user("hi"),), config=Config(stop=("END",)))
    body = _responses_sse("alp", "ha E", "ND beta", " gamma")
    lm = OpenAILM(api_key="k", transport=FakeTransport([FakeResponse(status=200, body=body)]))
    events = list(lm.stream(req))
    text = "".join(e.delta.text for e in events if e.type == "delta" and isinstance(e.delta, TextDelta))
    assert text == "alpha "
    assert events[-1].type == "end" and events[-1].finish_reason == "stop" and events[-1].usage is None
    assert sum(e.type == "end" for e in events) == 1
    assert events[0].adaptations[0].action == "client_side"


def test_stream_without_a_hit_releases_the_withheld_tail() -> None:
    req = Request(model="gpt-5", messages=(Message.user("hi"),), config=Config(stop=("END",)))
    body = _responses_sse("alpha ", "beta")
    lm = OpenAILM(api_key="k", transport=FakeTransport([FakeResponse(status=200, body=body)]))
    events = list(lm.stream(req))
    text = "".join(e.delta.text for e in events if e.type == "delta" and isinstance(e.delta, TextDelta))
    assert text == "alpha beta" and events[-1].usage is not None and events[-1].usage.output_tokens == 9


def test_async_stream_cuts_at_stop_too() -> None:
    from tests.test_async_adapters import FakeAsyncTransport

    req = Request(model="gpt-5", messages=(Message.user("hi"),), config=Config(stop=("END",)))
    body = _responses_sse("alpha EN", "D beta")
    lm = AsyncOpenAILM(api_key="k", transport=FakeAsyncTransport(body))

    async def collect():
        return [e async for e in lm.stream(req)]

    events = asyncio.run(collect())
    text = "".join(e.delta.text for e in events if e.type == "delta" and isinstance(e.delta, TextDelta))
    assert text == "alpha " and events[-1].finish_reason == "stop"
    assert lm.plan(req)[0].action == "client_side"
    assert lm.adaptations == "note"


def test_apply_client_side_stop_on_a_response_value() -> None:
    from lm15.result import apply_client_side_stop

    r = Response(id=None, model="m", finish_reason="length", usage=Usage(),
                 message=Message.assistant((TextPart(text="one"), TextPart(text="two STOP three"), TextPart(text="four"))))
    cut = apply_client_side_stop(r, ("STOP",))
    assert [p.text for p in cut.message.parts] == ["one", "two "] and cut.finish_reason == "stop"
    assert apply_client_side_stop(r, ("nowhere",)) is r


# ─── shared clamp ────────────────────────────────────────────────────

def test_nearest_effort_ties_go_lower() -> None:
    assert nearest_effort("medium", ("low", "high")) == "low"
    assert nearest_effort("xhigh", ("low", "medium", "high")) == "high"
    assert nearest_effort("minimal", ("low", "high", "max")) == "low"
    assert nearest_effort("high", ("low", "high", "max")) == "high"
    with pytest.raises(ValueError):
        nearest_effort("low", ())
