"""AUTH-10 backend settings (amended 2026-09-30): the version a subscription
door claims is a setting — explicit, then the router's environment, then
the table — and the claude-code door's minimum-version refusal says which
setting moves it (lm15-contract changes/2026-09-30-claude-code-client-version.md)."""
from __future__ import annotations

import json

import pytest

from lm15 import LMRouter, Message, Request, RouterConfig
from lm15.access import (
    CLAUDE_CODE,
    DEFAULT_CLAUDE_CODE_VERSION,
    DEFAULT_CODEX_CLIENT_VERSION,
    OPENAI_CODEX,
    claude_code_version_guidance,
    resolve_backend_settings,
)
from lm15.doctor import explain_auth
from lm15.errors import InvalidRequestError, NotConfiguredError
from lm15.providers import AnthropicLM, ClaudeCodeLM
from lm15.providers.openai_codex import OpenAICodexLM
from lm15.testing import FakeTransport

REFUSAL = ("Claude Code 2.1.170 does not support this model; version 2.1.280 or newer is required. "
           "Run 'claude update', or update the Claude desktop app, then try again.")


def _ua(lm) -> str:
    return lm._headers()["user-agent"]


@pytest.fixture(autouse=True)
def _hermetic_logins(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A routed subscription door reads its CLI's login file; never the real one."""
    import time

    import lm15.auth

    claude = tmp_path / "claude.json"
    claude.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "refreshToken": "r",
                                                    "expiresAt": int(time.time() * 1000) + 3_600_000}}))
    codex = tmp_path / "codex.json"
    codex.write_text(json.dumps({"tokens": {"access_token": "tok", "refresh_token": "r", "account_id": "acct"}}))
    monkeypatch.setattr(lm15.auth, "CLAUDE_CODE_CREDENTIALS_PATH", claude)
    monkeypatch.setattr(lm15.auth, "CODEX_CLI_AUTH_PATH", codex)
    monkeypatch.setenv("LM15_LOCK_DIR", str(tmp_path / "locks"))


def test_the_table_default_is_the_header_and_the_option() -> None:
    assert dict(CLAUDE_CODE.headers)["user-agent"] == f"claude-cli/{DEFAULT_CLAUDE_CODE_VERSION}"
    assert CLAUDE_CODE.backend_options == {"client_version": DEFAULT_CLAUDE_CODE_VERSION}
    assert [s.name for s in CLAUDE_CODE.backend_settings] == ["client_version"]
    assert CLAUDE_CODE.backend_settings[0].env == ("LM15_CLAUDE_CODE_VERSION",)
    assert OPENAI_CODEX.backend_settings[0].env == ("LM15_CODEX_CLIENT_VERSION",)
    assert _ua(ClaudeCodeLM(api_key="k", transport=FakeTransport([]))) == f"claude-cli/{DEFAULT_CLAUDE_CODE_VERSION}"


def test_the_setting_and_the_older_keyword_move_the_header() -> None:
    by_setting = ClaudeCodeLM(api_key="k", settings={"client_version": "2.1.280"}, transport=FakeTransport([]))
    by_keyword = ClaudeCodeLM(api_key="k", claude_code_version="2.1.280", transport=FakeTransport([]))
    by_policy = AnthropicLM(api_key="k", access=CLAUDE_CODE, settings={"client_version": "2.1.280"}, transport=FakeTransport([]))
    for lm in (by_setting, by_keyword, by_policy):
        assert _ua(lm) == "claude-cli/2.1.280"
        assert lm.access.backend_options["client_version"] == "2.1.280"
    assert by_setting.claude_code_version == "2.1.280"
    with pytest.raises(ValueError, match="disagree"):
        ClaudeCodeLM(api_key="k", claude_code_version="1", settings={"client_version": "2"}, transport=FakeTransport([]))


def test_an_adapter_built_by_hand_reads_no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LM15_CLAUDE_CODE_VERSION", "9.9.9")
    assert _ua(ClaudeCodeLM(api_key="k", transport=FakeTransport([]))) == f"claude-cli/{DEFAULT_CLAUDE_CODE_VERSION}"


def test_the_router_reads_the_setting_then_the_environment() -> None:
    explicit = LMRouter(RouterConfig(api_keys={"claude-code": "k"}, env={"LM15_CLAUDE_CODE_VERSION": "2.1.282"},
                                     settings={"claude_code": {"client_version": "2.1.281"}}))
    assert _ua(explicit.lm("claude-code:claude-opus-5-5")) == "claude-cli/2.1.281"
    from_env = LMRouter(RouterConfig(api_keys={"claude-code": "k"}, env={"LM15_CLAUDE_CODE_VERSION": "2.1.282"}))
    assert _ua(from_env.lm("claude-code:claude-opus-5-5")) == "claude-cli/2.1.282"
    default = LMRouter(RouterConfig(api_keys={"claude-code": "k"}, env={}))
    assert _ua(default.lm("claude-code:claude-opus-5-5")) == f"claude-cli/{DEFAULT_CLAUDE_CODE_VERSION}"
    # plan() builds the same bytes the call would.
    request = Request(model="claude-code:claude-opus-5-5", messages=(Message.user("hi"),))
    assert explicit.plan(request) is not None


def test_codex_client_version_is_the_same_setting() -> None:
    lm = OpenAICodexLM(api_key="k", account_id="a", settings={"client_version": "0.150.0"}, transport=FakeTransport([]))
    assert lm.access.backend_options["client_version"] == "0.150.0" and lm.client_version == "0.150.0"
    assert OpenAICodexLM(api_key="k", account_id="a", transport=FakeTransport([])).client_version == DEFAULT_CODEX_CLIENT_VERSION
    routed = LMRouter(RouterConfig(api_keys={"openai-codex": "k"}, env={"LM15_CODEX_CLIENT_VERSION": "0.151.0"}))
    assert routed.lm("openai-codex:gpt-5.4-mini").access.backend_options["client_version"] == "0.151.0"


def test_a_setting_nothing_reads_is_refused_not_dropped() -> None:
    with pytest.raises(NotConfiguredError, match="known: client_version"):
        ClaudeCodeLM(api_key="k", settings={"version": "2.1.280"}, transport=FakeTransport([]))
    router = LMRouter(RouterConfig(api_keys={"anthropic": "k"}, settings={"anthropic": {"client_version": "1"}}))
    with pytest.raises(NotConfiguredError, match="this door takes no settings"):
        router.lm("anthropic:claude-opus-5-5")
    with pytest.raises(NotConfiguredError):
        resolve_backend_settings(CLAUDE_CODE, {"region": "x"})


def test_the_doctor_says_which_version_is_claimed_and_why() -> None:
    report = explain_auth("claude-code", env={"LM15_CLAUDE_CODE_VERSION": "2.1.290"}, claude_credentials_path="/nonexistent")
    assert dict(report.settings) == {"client_version": "2.1.290"}
    assert dict(report.setting_sources) == {"client_version": "env:LM15_CLAUDE_CODE_VERSION"}
    assert "setting client_version: 2.1.290 (from env $LM15_CLAUDE_CODE_VERSION)" in report.describe()
    report = explain_auth("claude-code", env={}, claude_credentials_path="/nonexistent")
    assert dict(report.setting_sources) == {"client_version": "default"}
    assert explain_auth("anthropic", env={}).settings == ()


def test_the_minimum_version_refusal_names_the_setting() -> None:
    body = json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": REFUSAL}, "request_id": "req_1"})
    error = ClaudeCodeLM(api_key="k", transport=FakeTransport([])).normalize_error(400, body)
    assert isinstance(error, InvalidRequestError)
    assert error.message == (
        REFUSAL + "\n\n  To fix:\n"
        "    - lm15 sends this version itself; updating Claude Code does not change it\n"
        "    - Set the claude-code setting client_version to 2.1.280 or newer (or LM15_CLAUDE_CODE_VERSION=2.1.280)\n"
    )
    # The API-key door never claims a Claude Code version: its message is the server's.
    assert AnthropicLM(api_key="k", transport=FakeTransport([])).normalize_error(400, body).message == REFUSAL
    assert claude_code_version_guidance("model: x") == "model: x"
    assert claude_code_version_guidance(error.message) == error.message
