"""The connection budget: timeouts, pool size, shared transport, close,
and the two "a network fault that is really an input or reply problem"
errors.  Every case here traces to a finding of the DSPy gauntlet
(cmpnd-ai/breaka-your-lm, 2026-09-13) against lm15 1.0.0a1.
"""
from __future__ import annotations

import gc
import json
import warnings

import pytest

import lm15
from lm15 import LMRouter, AsyncLMRouter, Message, Request, RouterConfig, Timeouts
from lm15.errors import NotConfiguredError, ProviderError, LM15Error
from lm15.providers.base import HttpResponse
from lm15.providers.common import json_dumps
from lm15.providers import AnthropicLM
from lm15.testing import FakeResponse, FakeTransport
from lm15.transports import StdlibAsyncTransport, StdlibTransport, TransportRequest


class TestTimeouts:
    def test_defaults_follow_the_provider_sdks(self) -> None:
        # OpenAI, Anthropic and litellm wait 600 s; a 60 s read timeout
        # turned a thinking model into a "network failure" (gauntlet #1).
        t = Timeouts()
        assert (t.connect, t.read, t.write, t.pool) == (10.0, 600.0, 600.0, 600.0)
        assert StdlibTransport()._read_timeout == 600.0
        assert StdlibAsyncTransport()._read_timeout == 600.0

    def test_default_pool_is_wide(self) -> None:
        # Ten connections made 16 evaluation threads fail on a local
        # server (gauntlet #2); 100 is the httpx/aiohttp norm.
        assert StdlibTransport().max_connections == 100

    @pytest.mark.parametrize("field", ["connect", "read", "write", "pool"])
    @pytest.mark.parametrize("bad", [0, -1, True, "5"])
    def test_non_positive_is_refused(self, field: str, bad) -> None:
        with pytest.raises(ValueError, match=f"Timeouts.{field}"):
            Timeouts(**{field: bad})

    def test_pool_may_be_unbounded(self) -> None:
        assert Timeouts(pool=None).pool is None

    def test_exported_at_top_level(self) -> None:
        assert lm15.Timeouts is Timeouts


class TestRouterConfig:
    def test_timeouts_and_max_connections_reach_the_transport(self) -> None:
        router = LMRouter(RouterConfig(env={}, timeouts=Timeouts(read=1800, pool=None), max_connections=3))
        try:
            t = router._shared_transport()
            assert isinstance(t, StdlibTransport)
            assert t._read_timeout == 1800 and t._pool_timeout is None and t.max_connections == 3
        finally:
            router.close()

    def test_async_router_builds_an_async_transport(self) -> None:
        router = AsyncLMRouter(RouterConfig(env={}, timeouts=Timeouts(read=1800)))
        t = router._shared_transport()
        assert isinstance(t, StdlibAsyncTransport) and t._read_timeout == 1800

    def test_knobs_with_a_supplied_transport_are_refused(self) -> None:
        # They would silently not apply to the transport you passed.
        with pytest.raises(NotConfiguredError, match="cannot be combined"):
            RouterConfig(transport=FakeTransport(), timeouts=Timeouts())
        with pytest.raises(NotConfiguredError, match="cannot be combined"):
            RouterConfig(transport=FakeTransport(), max_connections=5)

    def test_wrong_type_for_timeouts_is_named(self) -> None:
        with pytest.raises(TypeError, match="lm15.Timeouts"):
            RouterConfig(timeouts=30)  # type: ignore[arg-type]

    def test_bad_max_connections_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_connections"):
            RouterConfig(max_connections=0)

    def test_client_keyword_hint_names_timeouts(self) -> None:
        from lm15.router import _CLIENT_KEYWORDS

        assert "Timeouts(read=" in _CLIENT_KEYWORDS["timeout"]


# Exercise provider-built requests, not only the transport's stored defaults:
# fixed 60/120-second values here used to override the caller's read timeout.
_INFERENCE_PROVIDERS = ("openai", "openai-chat", "anthropic", "gemini")


@pytest.mark.parametrize("provider", _INFERENCE_PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("read_seconds", [None, 0.2, 1800.0])
def test_inference_request_inherits_transport_timeout(provider, streaming, read_seconds):
    timeouts = None if read_seconds is None else Timeouts(read=read_seconds)
    with LMRouter(RouterConfig(env={}, api_keys={provider: "fake"}, timeouts=timeouts)) as router:
        lm = router.lm(f"{provider}:m")
        request = Request(model="m", messages=(Message.user("hi"),))
        wire = lm.build_request(request, stream=streaming)
        assert wire.read_timeout is None
        assert lm.transport._read_timeout == (600.0 if read_seconds is None else read_seconds)


@pytest.fixture
def stalled_reply(transport_server):
    import threading

    release = threading.Event()
    finished = threading.Event()

    def handler(req, client):
        try:
            release.wait(1.0)
            # Bound the regression test even if the old 60/120-second override
            # returns: a non-timeout error must fail, not satisfy the assertion.
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
        finally:
            finished.set()

    transport_server.ctx.handler = handler
    try:
        yield transport_server.base_url()
    finally:
        release.set()
        if transport_server.ctx.requests:
            finished.wait(2.0)


@pytest.mark.parametrize("provider", _INFERENCE_PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
def test_provider_call_obeys_read_timeout(provider, streaming, stalled_reply, monkeypatch):
    from lm15.errors import TransportError

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    config = RouterConfig(env={}, api_keys={provider: "fake"},
                          base_urls={provider: stalled_reply}, timeouts=Timeouts(read=0.2))
    with LMRouter(config) as router:
        request = Request(model=f"{provider}:m", messages=(Message.user("hi"),))
        with pytest.raises(TransportError, match="read timed out"):
            if streaming:
                list(router.stream(request))
            else:
                router.complete(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", _INFERENCE_PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_async_provider_call_obeys_read_timeout(provider, streaming, stalled_reply, monkeypatch):
    from lm15.errors import TransportError

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    config = RouterConfig(env={}, api_keys={provider: "fake"},
                          base_urls={provider: stalled_reply}, timeouts=Timeouts(read=0.2))
    async with AsyncLMRouter(config) as router:
        request = Request(model=f"{provider}:m", messages=(Message.user("hi"),))
        with pytest.raises(TransportError, match="read timed out"):
            if streaming:
                async for _ in router.stream(request):
                    pass
            else:
                await router.complete(request)


class TestSharedTransport:
    def test_every_lm_of_a_router_shares_one_transport(self) -> None:
        router = LMRouter(RouterConfig(env={}, api_keys={"anthropic": "k", "openai": "k", "openai-chat": "k"}))
        try:
            lms = [router.lm(m) for m in ("anthropic:m", "openai:m", "openai-chat:m")]
            transports = {id(lm.transport) for lm in lms}
            assert len(transports) == 1
            assert lms[0].transport is router._shared_transport()
        finally:
            router.close()

    def test_close_closes_the_transport_and_the_router_is_reusable(self) -> None:
        router = LMRouter(RouterConfig(env={}, api_keys={"anthropic": "k"}))
        first = router.lm("anthropic:m").transport
        router.close()
        assert first._closed
        second = router.lm("anthropic:m").transport
        assert second is not first and not second._closed
        router.close()

    def test_context_manager(self) -> None:
        with LMRouter(RouterConfig(env={}, api_keys={"anthropic": "k"})) as router:
            t = router.lm("anthropic:m").transport
        assert t._closed

    @pytest.mark.asyncio
    async def test_async_close(self) -> None:
        async with AsyncLMRouter(RouterConfig(env={}, api_keys={"anthropic": "k"})) as router:
            t = router.lm("anthropic:m").transport
        assert t._closed

    def test_reopened_transport_keeps_its_configuration(self) -> None:
        # An interactive runner closed the transport between cells; the
        # adapter rebuilds it with the SAME limits, not the defaults.
        lm = AnthropicLM(api_key="k", transport=StdlibTransport(read_timeout=1234, max_connections=2))
        lm.transport.close()
        lm._ensure_transport_open()
        assert lm.transport._read_timeout == 1234 and lm.transport.max_connections == 2
        lm.close()


class TestNoLeakOnCollection:
    def test_dropped_sync_transport_closes_its_idle_socket(self, transport_server) -> None:
        # Under `-W error` the gauntlet's offline suite failed on an
        # unclosed socket from a collected pool (gauntlet #4).
        def use():
            t = StdlibTransport()
            with t.stream(TransportRequest(method="GET", url=f"{transport_server.base_url()}/")) as resp:
                resp.read()
            (conn,) = next(iter(t._pool._idle.values()))
            return conn.sock

        sock = use()
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            gc.collect()
        assert sock.fileno() == -1


@pytest.fixture
def transport_server():
    from .transports.conftest import _TestServer

    s = _TestServer(use_tls=False)
    s.start()
    try:
        yield s
    finally:
        s.stop()


class TestNonJsonReply:
    def test_html_behind_a_200_is_a_provider_error(self) -> None:
        resp = HttpResponse(
            status=200, reason="OK", headers=[("content-type", "text/html"), ("x-request-id", "req_9")],
            body=b"<html><body>Gateway timeout</body></html>", provider="openai-chat",
        )
        with pytest.raises(ProviderError) as info:
            resp.json()
        err = info.value
        assert err.code == "provider" and err.status == 200 and err.provider == "openai-chat"
        assert err.request_id == "req_9"
        assert "not JSON" in err.message and "text/html" in err.message and "<html>" in err.message
        assert isinstance(err, LM15Error)

    def test_reaches_complete_through_the_adapter(self) -> None:
        transport = FakeTransport([FakeResponse(status=200, body=b"<html>oops</html>")])
        lm = AnthropicLM(api_key="k", transport=transport)
        with pytest.raises(ProviderError, match="not JSON") as info:
            lm.complete(Request(model="m", messages=(Message.user("hi"),)))
        assert info.value.provider == "anthropic" and info.value.status == 200

    def test_valid_json_unchanged(self) -> None:
        resp = HttpResponse(status=200, reason="OK", headers=[], body=json.dumps({"a": 1}).encode())
        assert resp.json() == {"a": 1}


class TestLoneSurrogate:
    def test_refused_before_the_wire_as_a_value_error(self) -> None:
        with pytest.raises(ValueError, match=r"lone surrogate U\+D800"):
            json_dumps({"text": "a\ud800b"})

    def test_valid_non_ascii_passes(self) -> None:
        assert json_dumps({"t": "héllo 🌍"}) == b'{"t":"h\xc3\xa9llo \xf0\x9f\x8c\x8d"}'

    def test_adapter_raises_without_a_request_on_the_wire(self) -> None:
        transport = FakeTransport([])
        lm = AnthropicLM(api_key="k", transport=transport)
        with pytest.raises(ValueError, match="not valid Unicode"):
            lm.complete(Request(model="m", messages=(Message.user("bad \udfff"),)))
        assert transport.requests == []


class TestReadTimeoutMessage:
    def test_names_the_client_limit_and_the_knob(self, transport_server) -> None:
        import time

        def handler(req, client):
            client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n")
            time.sleep(1.0)

        transport_server.ctx.handler = handler
        t = StdlibTransport(read_timeout=0.2)
        try:
            with pytest.raises(Exception, match=r"lm15's read timeout.*Timeouts\(read=") as info:
                with t.stream(TransportRequest(method="GET", url=f"{transport_server.base_url()}/")) as resp:
                    resp.read()
            assert "0.2s" in str(info.value)
        finally:
            t.close()

    def test_pool_wait_message_names_max_connections(self, transport_server) -> None:
        import threading, time

        started = threading.Event()

        def handler(req, client):
            # Headers now, body never: the first response holds its slot open.
            client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n")
            started.set()
            time.sleep(1.0)

        transport_server.ctx.handler = handler
        t = StdlibTransport(max_connections=1, pool_timeout=0.1)
        try:
            first = t.stream(TransportRequest(method="GET", url=f"{transport_server.base_url()}/"))
            started.wait(2.0)
            with pytest.raises(Exception, match=r"all 1 connections were busy for 0.1s.*max_connections"):
                t.stream(TransportRequest(method="GET", url=f"{transport_server.base_url()}/"))
            first.close()
        finally:
            t.close()
