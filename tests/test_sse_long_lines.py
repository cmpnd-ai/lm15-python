"""INV-056: a stream accepts any event a complete reply would.

Every lm15 SDK refused an SSE line over 64 KiB and an event over 1 MiB.
Real streams are bigger: OpenAI Responses repeats the whole response
(system prompt included) in ``response.completed``, and Gemini sends a
generated image as one data line (29.7 MB at 4K, lm15-contract
``receipts/2026-10-06-sse-long-lines``).  The shared corpus pins two live
captures (``openai.streaming_long_line``, ``gemini.streaming_image``); these
tests pin the parser and the line splitter around them.
"""

from __future__ import annotations

import asyncio
import json
import random

import pytest

from lm15 import Config, Message, Request
from lm15.errors import TransportError
from lm15.providers import OpenAILM
from lm15.sse import aparse_sse, parse_sse
from lm15.testing import FakeResponse, FakeTransport
from lm15.transports._types import LineSplitter


def _chunks(body: bytes, size: int) -> list[bytes]:
    return [body[i : i + size] for i in range(0, len(body), size)]


def _reference_lines(body: bytes) -> list[bytes]:
    """Split on ``\\n`` only, keeping it; an unterminated tail is a line."""
    parts = body.split(b"\n")
    lines = [p + b"\n" for p in parts[:-1]]
    return lines + ([parts[-1]] if parts[-1] else [])


def test_a_line_over_the_former_limits_parses_by_default() -> None:
    text = "x" * (3 * 1024 * 1024)  # over 64 KiB and over 1 MiB
    body = b"event: response.completed\n" + b"data: " + json.dumps({"text": text}).encode() + b"\n\n"
    events = list(parse_sse(LineSplitter.iterate(_chunks(body, 16 * 1024))))
    assert [e.event for e in events] == ["response.completed"]
    assert json.loads(events[0].data)["text"] == text


def test_async_parser_has_no_default_limit_either() -> None:
    body = b"data: " + b"y" * (2 * 1024 * 1024) + b"\n\n"

    async def chunks():
        for chunk in _chunks(body, 8 * 1024):
            yield chunk

    async def run():
        return [e async for e in aparse_sse(LineSplitter.aiterate(chunks()))]

    events = asyncio.run(run())
    assert len(events) == 1 and len(events[0].data) == 2 * 1024 * 1024


def test_caps_are_opt_in_and_still_refuse() -> None:
    with pytest.raises(TransportError, match="SSE line exceeds limit"):
        list(parse_sse(iter([b"data: too long\n"]), max_line_bytes=4))
    with pytest.raises(TransportError, match="SSE event exceeds limit"):
        list(parse_sse(iter([b"data: 1\n", b"data: 2\n"]), max_event_bytes=8))


def test_line_splitter_matches_a_plain_split_for_any_chunking() -> None:
    rng = random.Random(56)
    pieces = [b"a", b"\n", b"bc", b"\r\n", b"\n\n", b"data: {}\n", b"z" * 300]
    for _ in range(500):
        body = b"".join(rng.choice(pieces) for _ in range(rng.randint(0, 40)))
        cuts = sorted(rng.sample(range(len(body) + 1), min(len(body) + 1, rng.randint(0, 10))))
        chunks = [body[a:b] for a, b in zip([0, *cuts], [*cuts, len(body)])]
        assert list(LineSplitter.iterate(chunks)) == _reference_lines(body)


def test_line_splitter_never_rescans_a_pending_line() -> None:
    """Linear time: each byte is searched for ``\\n`` once, however many
    reads a long line takes (the old splitter searched the whole pending
    line again after every read)."""
    searched = 0

    class CountingBuffer(bytearray):
        def find(self, sub, start=0, *rest):  # type: ignore[override]
            nonlocal searched
            idx = super().find(sub, start, *rest)
            searched += (idx if idx >= 0 else len(self)) - start + (1 if idx >= 0 else 0)
            return idx

    splitter = LineSplitter()
    splitter._buf = CountingBuffer()
    body = b"data: " + b"q" * (4 * 1024 * 1024) + b"\n\n"
    lines = [line for chunk in _chunks(body, 4096) for line in splitter.feed(chunk)]
    assert lines == [body[:-1], b"\n"]
    assert searched <= len(body) + 1


def test_a_long_responses_stream_reaches_the_reply() -> None:
    """End to end through the adapter and ``TransportResponse``-shaped
    chunks: the 75 KB echo lines of a long system prompt (the live case
    ``openai.streaming_long_line``) no longer abort the stream."""
    system = "S" * (80 * 1024)
    response = {"id": "resp_1", "model": "gpt-4.1-mini", "instructions": system, "status": "in_progress", "output": []}

    def frame(kind: str, **fields) -> str:
        return f"event: {kind}\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n"

    done = {**response, "status": "completed",
            "output": [{"type": "message", "id": "msg_1", "role": "assistant",
                        "content": [{"type": "output_text", "text": "OK", "annotations": []}]}],
            "usage": {"input_tokens": 20000, "output_tokens": 1, "total_tokens": 20001}}
    body = (frame("response.created", response=response)
            + frame("response.output_text.delta", item_id="msg_1", output_index=0, content_index=0, delta="OK")
            + frame("response.completed", response=done)).encode()
    transport = FakeTransport([FakeResponse(200, b"", chunks=_chunks(body, 16 * 1024),
                                            headers=[("content-type", "text/event-stream")])])
    lm = OpenAILM(api_key="sk-test", transport=transport)
    request = Request(model="gpt-4.1-mini", system=system, messages=(Message.user("Reply OK."),),
                      config=Config(max_tokens=16))
    events = list(lm.stream(request))
    assert events[-1].type == "end"
    text = "".join(e.delta.text for e in events if e.type == "delta" and e.delta.type == "text")
    assert text == "OK"
