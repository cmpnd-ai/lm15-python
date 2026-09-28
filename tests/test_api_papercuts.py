"""1.0 API papercuts: TimeoutError builtin compatibility, bare-message coercion."""

import builtins

import pytest

import lm15.errors as errors
from lm15.types import Message, Request


class TestTimeoutErrorBuiltinCompat:
    def test_except_builtin_timeouterror_catches_lm15_timeout(self):
        with pytest.raises(builtins.TimeoutError):
            raise errors.TimeoutError("provider timed out")

    def test_subclass_of_both(self):
        assert issubclass(errors.TimeoutError, errors.ProviderError)
        assert issubclass(errors.TimeoutError, builtins.TimeoutError)

    def test_mro_prefers_provider_error_metadata(self):
        err = errors.TimeoutError("slow", provider="openai", status=408)
        assert err.code == "timeout"
        assert err.provider == "openai"
        assert err.status == 408
        # Displayed form carries provider/status context (API review A3);
        # the message FIELD is what the contract pins and stays bare.
        assert str(err) == "slow (openai, HTTP 408)"
        assert err.message == "slow"

    def test_still_caught_as_lm15_hierarchy(self):
        err = errors.TimeoutError("slow")
        assert isinstance(err, errors.LM15Error)
        assert isinstance(err, errors.ProviderError)
        assert isinstance(err, OSError)  # builtin TimeoutError is an OSError

    def test_canonical_error_code_unchanged(self):
        assert errors.canonical_error_code(errors.TimeoutError("x")) == "timeout"
        assert errors.error_class_for_code("timeout") is errors.TimeoutError

    def test_retryable_unchanged(self):
        assert errors.TimeoutError in errors.RETRYABLE_ERRORS


class TestBareMessageCoercion:
    def test_request_accepts_single_message(self):
        msg = Message.user("hi")
        req = Request(model="m", messages=msg)
        assert req.messages == (msg,)

    def test_request_still_accepts_list(self):
        msg = Message.user("hi")
        req = Request(model="m", messages=[msg])
        assert req.messages == (msg,)

    def test_request_rejects_non_message(self):
        with pytest.raises(TypeError):
            Request(model="m", messages="hi")


class TestTransportNaming:
    def test_renamed_classes_exported(self):
        from lm15 import transports

        for name in ("TransportRequest", "TransportResponse", "AsyncTransportResponse"):
            assert hasattr(transports, name)
            assert name in transports.__all__

    def test_short_names_gone(self):
        from lm15 import transports

        for name in ("Request", "Response", "AsyncResponse"):
            assert not hasattr(transports, name)
            assert name not in transports.__all__


class TestTopLevelSurface:
    FACTORY_NAMES = (
        "text", "thinking", "refusal", "citation", "image", "audio",
        "video", "document", "binary", "tool_call", "tool_result",
    )
    # Doctrine exemptions (earned, not accidental): `tool_result` is the
    # error-result spelling beginners must find (API review A1), and it is
    # not a generic word the way `text`/`image` are.
    TOP_LEVEL_EXEMPT = ("tool_result",)

    def test_factories_not_at_top_level(self):
        import lm15

        for name in self.FACTORY_NAMES:
            if name in self.TOP_LEVEL_EXEMPT:
                assert hasattr(lm15, name)
                continue
            assert not hasattr(lm15, name)
            assert name not in lm15.__all__

    def test_factories_stay_in_types(self):
        import lm15.types as types

        for name in self.FACTORY_NAMES:
            assert callable(getattr(types, name))

    @pytest.mark.parametrize("surface", ["exports", "flows"])
    def test_lazy_imports_use_the_loaded_package(self, surface):
        import subprocess
        import sys
        from pathlib import Path

        code = """
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.modules['lm15'] = None  # No unrelated top-level installation may be used.
spec = importlib.util.spec_from_file_location(
    'vendored_lm15', root / '__init__.py', submodule_search_locations=[str(root)],
)
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
assert 'vendored_lm15.login' not in sys.modules
if sys.argv[2] == 'exports':
    for name in package.__all__:
        getattr(package, name)
    from vendored_lm15.login import Auth
    assert package.Auth is Auth
    assert package.connect.__module__ == 'vendored_lm15.interactive'
else:
    from vendored_lm15.login.flows import descriptor, flow, ProviderFlow
    assert descriptor('xai').id == 'xai'
    assert isinstance(flow('xai', 'device'), ProviderFlow)
    assert type(flow('xai', 'device')).__module__ == 'vendored_lm15.login.flows.xai'
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", code,
             str(Path(__file__).resolve().parents[1] / "lm15"), surface],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == 0, result.stderr
