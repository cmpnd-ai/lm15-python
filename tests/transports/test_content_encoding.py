"""Content-Encoding: a gateway that compresses despite `Accept-Encoding:
identity` must not turn into a utf-8 decode error far from the cause
(found by the DSPy gauntlet, 2026-09-13, `test_8_transport.py::test_gzip_body`).
"""
from __future__ import annotations

import gzip
import zlib

import pytest

from lm15.transports import (
    ProtocolError,
    StdlibAsyncTransport,
    StdlibTransport,
    TransportRequest,
)
from lm15.transports._http11 import ResponseHeadParser, content_codings

from .conftest import reply_bytes, reply_chunked


PAYLOAD = b'{"answer": "Reference text. "}' * 200


def _head(*headers: str) -> ResponseHeadParser:
    p = ResponseHeadParser()
    p.feed(("HTTP/1.1 200 OK\r\n" + "".join(h + "\r\n" for h in headers) + "\r\n").encode())
    return p


class TestCodingList:
    def test_identity_is_dropped(self) -> None:
        assert content_codings(["identity"]) == []
        assert content_codings([]) == []

    def test_order_is_reversed_for_undoing(self) -> None:
        # RFC 9110 §8.4: listed in the order applied; undone outermost first.
        assert content_codings(["deflate, gzip"]) == ["gzip", "deflate"]
        assert content_codings(["deflate", "gzip"]) == ["gzip", "deflate"]

    @pytest.mark.parametrize("coding", ["br", "zstd", "compress"])
    def test_unknown_coding_is_refused_by_name(self, coding: str) -> None:
        with pytest.raises(ProtocolError, match=coding):
            content_codings([coding])


class TestDecoderOffline:
    def test_gzip_over_content_length_streams_in_small_pieces(self) -> None:
        comp = gzip.compress(PAYLOAD)
        d = _head("Content-Encoding: gzip", f"Content-Length: {len(comp)}").body_decoder("POST")
        out = b""
        for i in range(0, len(comp), 5):
            out += b"".join(d.feed(comp[i : i + 5]))
        assert d.complete
        assert out == PAYLOAD

    def test_x_gzip_alias(self) -> None:
        comp = gzip.compress(PAYLOAD)
        d = _head("Content-Encoding: x-gzip", f"Content-Length: {len(comp)}").body_decoder("POST")
        assert b"".join(d.feed(comp)) == PAYLOAD

    def test_zlib_wrapped_deflate(self) -> None:
        comp = zlib.compress(PAYLOAD)
        d = _head("Content-Encoding: deflate", f"Content-Length: {len(comp)}").body_decoder("POST")
        assert b"".join(d.feed(comp)) == PAYLOAD

    def test_raw_deflate_fallback(self) -> None:
        # IIS and some CDNs send raw deflate; browsers and curl accept it.
        c = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        comp = c.compress(PAYLOAD) + c.flush()
        d = _head("Content-Encoding: deflate", f"Content-Length: {len(comp)}").body_decoder("POST")
        assert b"".join(d.feed(comp)) == PAYLOAD

    def test_eof_framed_body_drains_at_close(self) -> None:
        comp = gzip.compress(PAYLOAD)
        d = _head("Content-Encoding: gzip").body_decoder("POST")
        out = b"".join(d.feed(comp))
        assert not d.complete
        d.eof()
        out += d.drain()
        assert d.complete and out == PAYLOAD

    def test_truncated_gzip_is_a_protocol_error(self) -> None:
        comp = gzip.compress(PAYLOAD)
        d = _head("Content-Encoding: gzip").body_decoder("POST")
        b"".join(d.feed(comp[: len(comp) // 2]))
        with pytest.raises(ProtocolError, match="truncated"):
            d.eof()

    def test_garbage_gzip_is_a_protocol_error(self) -> None:
        d = _head("Content-Encoding: gzip", "Content-Length: 12").body_decoder("POST")
        with pytest.raises(ProtocolError, match="malformed gzip"):
            b"".join(d.feed(b"not gzip at all"[:12]))

    def test_stacked_codings(self) -> None:
        inner = zlib.compress(PAYLOAD)
        comp = gzip.compress(inner)
        d = _head("Content-Encoding: deflate, gzip", f"Content-Length: {len(comp)}").body_decoder("POST")
        assert b"".join(d.feed(comp)) == PAYLOAD

    def test_no_body_status_ignores_the_header(self) -> None:
        p = ResponseHeadParser()
        p.feed(b"HTTP/1.1 204 No Content\r\nContent-Encoding: gzip\r\n\r\n")
        assert p.body_decoder("POST").complete


class TestSyncTransport:
    def test_gzip_json_body(self, server) -> None:
        comp = gzip.compress(PAYLOAD)
        server.ctx.handler = lambda req, client: reply_bytes(
            client, 200, comp, headers=[("Content-Encoding", "gzip")]
        )
        t = StdlibTransport()
        try:
            with t.stream(TransportRequest(method="GET", url=f"{server.base_url()}/")) as resp:
                assert resp.read() == PAYLOAD
            # The connection is reusable: framing, not compression, decides.
            assert t.pool_stats()["idle"] == 1
        finally:
            t.close()

    def test_gzip_chunked_sse_yields_events_incrementally(self, server) -> None:
        events = [b"data: one\n\n", b"data: two\n\n", b"data: [DONE]\n\n"]
        c = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
        # One compressed chunk per event, each flushed so it is decodable alone.
        chunks = [c.compress(e) + c.flush(zlib.Z_SYNC_FLUSH) for e in events]
        chunks[-1] += c.flush()
        server.ctx.handler = lambda req, client: reply_chunked(
            client, chunks, headers=[("Content-Encoding", "gzip")], chunk_delay=0.05
        )
        t = StdlibTransport()
        try:
            with t.stream(TransportRequest(method="GET", url=f"{server.base_url()}/")) as resp:
                got = list(resp.iter_lines())
            assert b"".join(got) == b"".join(events)
            assert len(got) >= 3  # arrived as lines, not one buffered blob
        finally:
            t.close()

    def test_brotli_is_refused_with_its_name(self, server) -> None:
        server.ctx.handler = lambda req, client: reply_bytes(
            client, 200, b"\x0b\x00\x80", headers=[("Content-Encoding", "br")]
        )
        t = StdlibTransport()
        try:
            with pytest.raises(ProtocolError, match="'br'"):
                with t.stream(TransportRequest(method="GET", url=f"{server.base_url()}/")) as resp:
                    resp.read()
        finally:
            t.close()


class TestAsyncTransport:
    @pytest.mark.asyncio
    async def test_gzip_json_body(self, server) -> None:
        comp = gzip.compress(PAYLOAD)
        server.ctx.handler = lambda req, client: reply_bytes(
            client, 200, comp, headers=[("Content-Encoding", "gzip")]
        )
        t = StdlibAsyncTransport()
        try:
            async with t.stream(TransportRequest(method="GET", url=f"{server.base_url()}/")) as resp:
                assert await resp.read() == PAYLOAD
        finally:
            await t.aclose()

    @pytest.mark.asyncio
    async def test_gzip_eof_body_drains(self, server) -> None:
        comp = gzip.compress(PAYLOAD)

        def handler(req, client):
            client.sendall(b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nConnection: close\r\n\r\n" + comp)
            client.close()

        server.ctx.handler = handler
        t = StdlibAsyncTransport()
        try:
            async with t.stream(TransportRequest(method="GET", url=f"{server.base_url()}/")) as resp:
                assert await resp.read() == PAYLOAD
        finally:
            await t.aclose()
