"""Compression is independent of network/framing chunk boundaries."""
from __future__ import annotations

import gzip
import zlib

import pytest

from lm15.transports import ProtocolError, StdlibAsyncTransport, StdlibTransport, TransportRequest
from lm15.transports._http11 import ResponseHeadParser
from .conftest import reply_bytes, reply_chunked


TEXT = b'{"answer":"hello from both members"}'


def encoded(kind):
    if kind == "raw-deflate":
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        return "deflate", compressor.compress(TEXT) + compressor.flush()
    if kind == "deflate":
        return "deflate", zlib.compress(TEXT)
    if kind == "stacked":
        inner = zlib.compress(TEXT)
        return "deflate, gzip", gzip.compress(inner[:7]) + gzip.compress(inner[7:])
    members = gzip.compress(TEXT[:12]) + gzip.compress(b"") + gzip.compress(TEXT[12:])
    if kind == "padded-gzip":
        members += b"\x00" * 3
    return ("x-gzip" if kind == "x-gzip" else "gzip"), members


def decoder(coding, body, framing):
    head = ResponseHeadParser()
    framing_header = {"length": f"Content-Length: {len(body)}\r\n",
                      "chunked": "Transfer-Encoding: chunked\r\n", "eof": ""}[framing]
    head.feed(f"HTTP/1.1 200 OK\r\nContent-Encoding: {coding}\r\n{framing_header}\r\n".encode())
    return head.body_decoder("GET")


def wire_body(body, framing):
    if framing != "chunked":
        return body
    # The compressed data itself also crosses framing chunk boundaries.
    return b"".join(f"{len(body[i:i+3]):x}\r\n".encode() + body[i:i+3] + b"\r\n"
                    for i in range(0, len(body), 3)) + b"0\r\n\r\n"


def decode_chunks(coding, body, framing, chunks):
    d = decoder(coding, body, framing)
    output = b"".join(piece for chunk in chunks for piece in d.feed(chunk))
    d.eof()
    output += d.drain()
    assert d.complete and d.drain() == b""
    return output


@pytest.mark.parametrize("kind", ["raw-deflate", "deflate", "gzip", "x-gzip", "padded-gzip", "stacked"])
@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_every_single_split_and_bytewise_delivery(kind, framing):
    coding, body = encoded(kind)
    wire = wire_body(body, framing)
    for cut in range(len(wire) + 1):
        assert decode_chunks(coding, body, framing, [wire[:cut], wire[cut:]]) == TEXT, cut
    assert decode_chunks(coding, body, framing, [wire[i:i+1] for i in range(len(wire))]) == TEXT


@pytest.mark.parametrize("coding", ["gzip", "x-gzip"])
def test_first_member_is_delivered_before_next_member_arrives(coding):
    a, b = gzip.compress(b"first"), gzip.compress(b"second")
    d = decoder(coding, a + b, "eof")
    assert b"".join(d.feed(a)) == b"first"
    assert not d.complete
    assert b"".join(d.feed(b[:1])) == b""
    assert b"".join(d.feed(b[1:])) == b"second"
    d.eof()
    assert d.drain() == b"" and d.complete


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_every_truncated_second_member_is_rejected(framing):
    first, second = gzip.compress(b"first"), gzip.compress(b"second")
    for end in range(1, len(second)):
        body = first + second[:end]
        with pytest.raises(ProtocolError):
            decode_chunks("gzip", body, framing, [wire_body(body, framing)])


@pytest.mark.parametrize("kind", ["gzip", "deflate", "raw-deflate"])
@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_trailing_nonmember_data_is_not_silently_discarded(kind, framing):
    coding, body = encoded(kind)
    body += b"unexpected trailing data"
    with pytest.raises(ProtocolError):
        decode_chunks(coding, body, framing, [wire_body(body, framing)])


def test_deflate_header_split_and_checksum_corruption():
    body = bytearray(zlib.compress(TEXT))
    body[-1] ^= 1
    body = bytes(body)
    with pytest.raises(ProtocolError, match="deflate"):
        decode_chunks("deflate", body, "length", [body[:1], body[1:]])


def test_gzip_later_member_checksum_is_validated():
    last = bytearray(gzip.compress(b"second"))
    last[-8] ^= 1
    body = gzip.compress(b"first") + bytes(last)
    with pytest.raises(ProtocolError, match="gzip"):
        decode_chunks("gzip", body, "length", [body])


@pytest.mark.parametrize("coding,body", [("deflate", b"\x78"), ("gzip", b"\x1f")])
def test_one_byte_header_is_truncated_not_an_empty_reply(coding, body):
    with pytest.raises(ProtocolError, match="truncated"):
        decode_chunks(coding, body, "length", [body])


@pytest.mark.parametrize("kind", ["raw-deflate", "gzip", "stacked"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_transport_decodes_all_members_and_reuses_connection(server, kind, asynchronous):
    import asyncio

    coding, body = encoded(kind)
    # Framing delivers individual compressed bytes even if TCP coalesces writes.
    server.ctx.handler = lambda req, client: reply_chunked(
        client, [body[i:i+1] for i in range(len(body))], headers=[("Content-Encoding", coding)])
    request = TransportRequest(method="GET", url=server.base_url())
    if asynchronous:
        async def run():
            async with StdlibAsyncTransport(trust_env=False) as transport:
                for _ in range(2):
                    async with transport.stream(request) as response:
                        assert await response.read() == TEXT
                assert transport.pool_stats()["idle"] == 1
        asyncio.run(run())
    else:
        with StdlibTransport(trust_env=False) as transport:
            for _ in range(2):
                with transport.stream(request) as response:
                    assert response.read() == TEXT
            assert transport.pool_stats()["idle"] == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_bad_member_closes_connection_and_releases_slot(server, asynchronous):
    import asyncio

    body = gzip.compress(b"first") + b"bad member"
    server.ctx.handler = lambda req, client: reply_bytes(client, 200, body, headers=[("Content-Encoding", "gzip")])
    request = TransportRequest(method="GET", url=server.base_url())

    def check_pool(transport):
        assert transport.pool_stats()["idle"] == 0
        server.ctx.handler = lambda req, client: reply_bytes(client, 200, b"ok")

    if asynchronous:
        async def run():
            async with StdlibAsyncTransport(trust_env=False, max_connections=1, pool_timeout=0.2) as transport:
                with pytest.raises(ProtocolError):
                    async with transport.stream(request) as response:
                        await response.read()
                check_pool(transport)
                async with transport.stream(request) as response:
                    assert await response.read() == b"ok"
        asyncio.run(run())
    else:
        with StdlibTransport(trust_env=False, max_connections=1, pool_timeout=0.2) as transport:
            with pytest.raises(ProtocolError):
                with transport.stream(request) as response:
                    response.read()
            check_pool(transport)
            with transport.stream(request) as response:
                assert response.read() == b"ok"
