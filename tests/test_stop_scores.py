"""Stop filtering must preserve event fields, not just the visible text.

No provider calls: synthetic events and provider-shaped SSE exercise the
same sync/async filter used for both complete() and stream().
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from lm15 import Config, Message, Request, Response, Usage
from lm15.providers import OpenAILM, AsyncOpenAILM
from lm15.result import (
    apply_client_side_stop, atruncate_stream_at_stop, materialize_response,
    amaterialize_response, truncate_stream_at_stop,
)
from lm15.testing import FakeResponse, FakeTransport
from lm15.types import (
    CitationDelta, StreamDeltaEvent, StreamEndEvent, StreamStartEvent,
    TextDelta, TokenLogprob, TopLogprob,
)


REQUEST = Request(model="gpt-5", messages=(Message.user("hi"),))


def score(text, *, token_bytes=None):
    return TokenLogprob(token=text, logprob=-0.2,
                       bytes=tuple(text.encode("utf-8")) if token_bytes is None else token_bytes,
                       top=(TopLogprob(token="alternative", logprob=-1.0),))


def text_event(text, *scores, index=0):
    return StreamDeltaEvent(TextDelta(text=text, part_index=index, logprobs=tuple(scores)))


def filtered(events, stop, asynchronous):
    if not asynchronous:
        return list(truncate_stream_at_stop(iter(events), stop))

    async def source():
        for event in events:
            yield event

    async def run():
        return [event async for event in atruncate_stream_at_stop(source(), stop)]

    return asyncio.run(run())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stop", [(), ("NEVER",), ("a very long absent sequence",)])
def test_unmatched_stop_preserves_every_event_and_field(asynchronous, stop):
    events = [StreamStartEvent(model="gpt-5"),
              text_event("hé", score("hé")),
              StreamDeltaEvent(CitationDelta(url="https://example.org", part_index=1)),
              text_event("llo", score("llo"), index=2),
              text_event(""),
              StreamEndEvent(finish_reason="length", usage=Usage(output_tokens=2), provider_data={"id": "r"})]
    output = filtered(events, stop, asynchronous)
    assert output == events
    assert all(a is b for a, b in zip(output, events))
    response = materialize_response(iter(output), REQUEST)
    assert response.logprobs == (score("hé"), score("llo"))
    assert response.usage.output_tokens == 2
    assert response.provider_data == {"id": "r"}
    assert response.logprobs_complete


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("split", range(5))
def test_cross_event_and_part_stop_keeps_retained_scores(asynchronous, split):
    keep = score("alpha ")
    # The stop begins in one event and ends in the next, under a different
    # part index. Try every split, including empty pieces at either end.
    first, second = "STOP"[:split], "STOP"[split:]
    first_scores = (keep, score(first)) if first else (keep,)
    events = [StreamStartEvent(), text_event("alpha " + first, *first_scores),
              text_event(second + " after", score(second + " after"), index=1), StreamEndEvent()]
    response = materialize_response(iter(filtered(events, ("STOP",), asynchronous)), REQUEST)
    assert response.text == "alpha "
    assert response.logprobs == (keep,)
    assert response.finish_reason == "stop"
    assert response.usage == Usage()
    assert response.provider_data is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("aligned", [False, True])
def test_cut_within_a_token_is_not_given_an_invented_score(asynchronous, aligned):
    keep = score("hi ")
    scores = (keep, score("pre"), score("STOPtail")) if aligned else (keep, score("preSTOPtail"))
    events = [StreamStartEvent(), text_event("hi preSTOPtail", *scores), StreamEndEvent()]
    response = materialize_response(iter(filtered(events, ("STOP",), asynchronous)), REQUEST)
    assert response.text == "hi pre"
    assert response.logprobs == ((keep, score("pre")) if aligned else (keep,))
    assert response.logprobs_complete is aligned
    assert response.provider_data is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_unicode_token_bytes_define_the_cut_boundary(asynchronous):
    # The displayed character spans two provider tokens. Neither token's
    # spelling alone represents its bytes, and neither score may be lost.
    a = score("replacement", token_bytes=(0xC3,))
    b = score("replacement", token_bytes=(0xA9,))
    event = text_event("éSTOP", a, b, score("STOP"))
    response = materialize_response(iter(filtered([StreamStartEvent(), event], ("STOP",), asynchronous)), REQUEST)
    assert response.text == "é" and response.logprobs == (a, b)
    assert response.provider_data is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_unalignable_scores_are_not_attached_to_different_text(asynchronous):
    event = text_event("hiSTOP", score("unrelated"))
    response = materialize_response(iter(filtered([StreamStartEvent(), event], ("STOP",), asynchronous)), REQUEST)
    assert response.text == "hi" and response.logprobs is None
    assert not response.logprobs_complete
    assert response.provider_data is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_stop_discards_intervening_events_after_the_cut(asynchronous):
    events = [StreamStartEvent(), text_event("hi S", score("hi "), score("S")),
              StreamDeltaEvent(CitationDelta(url="https://example.org", part_index=1)),
              text_event("TOP", score("TOP"), index=2)]
    response = materialize_response(iter(filtered(events, ("STOP",), asynchronous)), REQUEST)
    assert response.text == "hi " and response.citations == []
    assert response.logprobs == (score("hi "),)


def test_complete_response_helper_trims_scores_and_keeps_provider_data():
    original = Response(id=None, model="gpt-5", message=Message.assistant("hi preSTOPtail"),
                        finish_reason="stop", usage=Usage(output_tokens=2),
                        logprobs=(score("hi "), score("preSTOPtail")), provider_data={"id": "r"})
    assert apply_client_side_stop(original, ("NEVER",)) is original
    response = apply_client_side_stop(original, ("STOP",))
    assert response.text == "hi pre" and response.logprobs == (score("hi "),)
    assert response.provider_data == {"id": "r"}
    assert not response.logprobs_complete
    assert response.usage == original.usage
    assert original.logprobs == (score("hi "), score("preSTOPtail"))


@pytest.mark.parametrize("complete", [False, True])
def test_score_coverage_survives_serialization_and_materialization(complete):
    from lm15.serde import delta_from_dict, delta_to_dict, response_from_dict, response_to_dict
    from lm15.result import response_to_events

    delta = TextDelta(text="hi", logprobs=(score("h"),), logprobs_complete=complete)
    encoded_delta = delta_to_dict(delta)
    assert delta_from_dict(encoded_delta) == delta
    assert ("logprobs_complete" in encoded_delta) is (not complete)
    response = Response(id=None, model="gpt-5", message=Message.assistant("hi"),
                        finish_reason="stop", usage=Usage(), logprobs=delta.logprobs,
                        logprobs_complete=complete)
    encoded_response = response_to_dict(response)
    assert response_from_dict(encoded_response) == response
    assert ("logprobs_complete" in encoded_response) is (not complete)
    assert materialize_response(response_to_events(response), REQUEST) == response


@pytest.mark.parametrize("bad", [None, 0, 1, "false"])
def test_score_coverage_requires_a_boolean(bad):
    with pytest.raises(TypeError, match="logprobs_complete"):
        TextDelta(text="hi", logprobs_complete=bad)
    with pytest.raises(TypeError, match="logprobs_complete"):
        Response(id=None, model="gpt-5", message=Message.assistant("hi"),
                 finish_reason="stop", usage=Usage(), logprobs_complete=bad)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("stop", ["NEVER", "STOP"])
def test_scores_survive_real_provider_parser_and_stop_filter(asynchronous, streaming, stop):
    from tests.test_async_adapters import FakeAsyncTransport

    frames = [
        {"type": "response.created", "response": {"id": "r", "model": "gpt-5"}},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "hi preSTOPtail",
         "logprobs": [{"token": t, "logprob": -0.2, "bytes": list(t.encode()), "top_logprobs": []}
                      for t in ("hi ", "preSTOPtail")]},
        {"type": "response.completed", "response": {"status": "completed", "output": [],
                                                    "usage": {"input_tokens": 1, "output_tokens": 2}}},
    ]
    body = "".join(f"event: {f['type']}\ndata: {json.dumps(f)}\n\n" for f in frames).encode()
    request = replace(REQUEST, config=Config(stop=(stop,), logprobs=0))
    if asynchronous:
        lm = AsyncOpenAILM(api_key="fake", transport=FakeAsyncTransport(body))

        async def run():
            if streaming:
                return await amaterialize_response(lm.stream(request), request)
            return await lm.complete(request)

        response = asyncio.run(run())
    else:
        lm = OpenAILM(api_key="fake", transport=FakeTransport([FakeResponse(status=200, body=body)]))
        response = materialize_response(lm.stream(request), request) if streaming else lm.complete(request)
    assert response.text == ("hi pre" if stop == "STOP" else "hi preSTOPtail")
    assert tuple(s.token for s in response.logprobs) == (("hi ",) if stop == "STOP" else ("hi ", "preSTOPtail"))
    if stop == "STOP":
        assert not response.logprobs_complete and response.usage == Usage()
    else:
        assert response.usage.output_tokens == 2
        assert response.logprobs_complete
