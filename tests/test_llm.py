"""
Tests for the optional LLM backend (`app.core.llm`): provider discovery,
selection (auto / pinned / off), each provider's call shape, and — most
importantly — that every failure path returns None so callers can fall back.

The SDKs are optional and generally absent here, so each provider's module is
injected into ``sys.modules`` as a stub for the duration of a test.
"""
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core import llm


# ─── Config + SDK stubs ──────────────────────────────────────────
def cfg(**over):
    """A settings stub with everything off unless the test says otherwise."""
    base = dict(LLM_PROVIDER="auto", ANTHROPIC_API_KEY="", ANTHROPIC_MODEL="claude-opus-5",
                GEMINI_API_KEY="", GEMINI_MODEL="gemini-2.5-pro",
                OPENAI_API_KEY="", OPENAI_MODEL="gpt-4o", OPENAI_BASE_URL="",
                COMPANION_MODEL="", COMPANION_MAX_TOKENS=900,
                CONGRESS_NAME="Dubai Stem Cell Congress", CONGRESS_YEAR="2027")
    base.update(over)
    return SimpleNamespace(**base)


def settings(**over):
    return patch.object(llm, "get_settings", return_value=cfg(**over))


def fake_anthropic(text="Claude says hi", stop_reason="end_turn"):
    module = types.ModuleType("anthropic")
    response = SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)])
    client = MagicMock()
    client.messages.create.return_value = response
    module.Anthropic = MagicMock(return_value=client)
    module._client = client
    return module


def fake_genai(text="Gemini says hi", block_reason=None):
    google = types.ModuleType("google")
    google.__path__ = []
    genai = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    genai_types.GenerateContentConfig = GenerateContentConfig
    response = SimpleNamespace(
        text=text,
        prompt_feedback=SimpleNamespace(block_reason=block_reason))
    client = MagicMock()
    client.models.generate_content.return_value = response
    genai.Client = MagicMock(return_value=client)
    genai.types = genai_types
    google.genai = genai
    genai._client = client
    return {"google": google, "google.genai": genai,
            "google.genai.types": genai_types}


def fake_legacy_gemini(text="Legacy Gemini"):
    google = types.ModuleType("google")
    google.__path__ = []
    legacy = types.ModuleType("google.generativeai")
    model = MagicMock()
    model.generate_content.return_value = SimpleNamespace(
        text=text, prompt_feedback=None)
    legacy.configure = MagicMock()
    legacy.GenerativeModel = MagicMock(return_value=model)
    legacy._model = model
    google.generativeai = legacy
    return {"google": google, "google.generativeai": legacy}


def fake_openai(text="OpenAI says hi", finish_reason="stop", raises=None):
    module = types.ModuleType("openai")
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=text))])
    client = MagicMock()
    if raises is not None:
        client.chat.completions.create.side_effect = [raises, response]
    else:
        client.chat.completions.create.return_value = response
    module.OpenAI = MagicMock(return_value=client)
    module._client = client
    return module


def with_sdks(**modules):
    """Inject stub SDK modules for the duration of a `with` block."""
    return patch.dict(sys.modules, modules)


# ─── Discovery and selection ─────────────────────────────────────
class TestDiscovery:
    def test_nothing_configured_means_no_llm(self):
        with settings():
            assert llm.available_providers() == []
            assert llm.active_provider() is None
            assert llm.is_available() is False

    def test_key_without_sdk_is_not_ready(self):
        with settings(ANTHROPIC_API_KEY="sk-test"), \
             patch.dict(sys.modules, {"anthropic": None}):
            status = {row.name: row for row in llm.provider_status()}
            assert status["anthropic"].configured is True
            assert status["anthropic"].installed is False
            assert status["anthropic"].ready is False
            assert llm.is_available() is False

    def test_sdk_without_key_is_not_ready(self):
        with settings(), with_sdks(anthropic=fake_anthropic()):
            status = {row.name: row for row in llm.provider_status()}
            assert status["anthropic"].installed is True
            assert status["anthropic"].configured is False
            assert status["anthropic"].ready is False

    def test_status_never_leaks_keys(self):
        with settings(OPENAI_API_KEY="sk-secret"), with_sdks(openai=fake_openai()):
            payload = [row.as_dict() for row in llm.provider_status()]
            assert "sk-secret" not in str(payload)
            assert set(payload[0]) == {"name", "label", "configured",
                                       "installed", "model", "ready"}

    def test_auto_prefers_anthropic(self):
        mods = {"anthropic": fake_anthropic(), "openai": fake_openai()}
        mods.update(fake_genai())
        with settings(ANTHROPIC_API_KEY="a", GEMINI_API_KEY="g",
                      OPENAI_API_KEY="o"), with_sdks(**mods):
            assert llm.available_providers() == ["anthropic", "gemini", "openai"]
            assert llm.active_provider() == "anthropic"

    def test_auto_falls_through_to_the_ready_one(self):
        with settings(GEMINI_API_KEY="g"), with_sdks(**fake_genai()):
            assert llm.active_provider() == "gemini"

    def test_pinned_provider_wins(self):
        mods = {"anthropic": fake_anthropic(), "openai": fake_openai()}
        with settings(LLM_PROVIDER="openai", ANTHROPIC_API_KEY="a",
                      OPENAI_API_KEY="o"), with_sdks(**mods):
            assert llm.active_provider() == "openai"

    def test_pinned_but_unusable_provider_is_not_substituted(self):
        with settings(LLM_PROVIDER="gemini", ANTHROPIC_API_KEY="a"), \
             with_sdks(anthropic=fake_anthropic()):
            assert llm.active_provider() is None
            assert llm.is_available() is False

    def test_off_disables_everything(self):
        module = fake_anthropic()
        with settings(LLM_PROVIDER="off", ANTHROPIC_API_KEY="a"), \
             with_sdks(anthropic=module):
            assert llm.active_provider() is None
            assert llm.generate(system="s", prompt="p") is None
            # Even an explicit provider request respects the kill switch.
            assert llm.generate(system="s", prompt="p", provider="anthropic") is None
        module._client.messages.create.assert_not_called()

    def test_unknown_setting_behaves_like_auto(self):
        with settings(LLM_PROVIDER="banana", ANTHROPIC_API_KEY="a"), \
             with_sdks(anthropic=fake_anthropic()):
            assert llm.active_provider() == "anthropic"

    def test_companion_model_alias_still_honoured(self):
        with settings(COMPANION_MODEL="claude-legacy-1"):
            assert llm.model_for("anthropic") == "claude-legacy-1"
        with settings():
            assert llm.model_for("anthropic") == "claude-opus-5"


# ─── Anthropic ───────────────────────────────────────────────────
class TestAnthropic:
    def test_generate(self):
        module = fake_anthropic("Hall 2 is on floor 2.")
        with settings(ANTHROPIC_API_KEY="sk-a"), with_sdks(anthropic=module):
            result = llm.generate(system="sys", prompt="where is hall 2")
        assert result.text == "Hall 2 is on floor 2."
        assert result.provider == "anthropic" and result.model == "claude-opus-5"
        kwargs = module._client.messages.create.call_args.kwargs
        assert kwargs["system"] == "sys"
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["max_tokens"] == 900

    def test_refusal_returns_none(self):
        module = fake_anthropic("nope", stop_reason="refusal")
        with settings(ANTHROPIC_API_KEY="sk-a"), with_sdks(anthropic=module):
            assert llm.generate(system="s", prompt="p") is None

    def test_api_error_returns_none(self):
        module = fake_anthropic()
        module._client.messages.create.side_effect = RuntimeError("503")
        with settings(ANTHROPIC_API_KEY="sk-a"), with_sdks(anthropic=module):
            assert llm.generate(system="s", prompt="p") is None

    def test_empty_answer_returns_none(self):
        module = fake_anthropic("   ")
        with settings(ANTHROPIC_API_KEY="sk-a"), with_sdks(anthropic=module):
            assert llm.generate(system="s", prompt="p") is None


# ─── Gemini ──────────────────────────────────────────────────────
class TestGemini:
    def test_generate(self):
        mods = fake_genai("The keynote is in Hall 1.")
        with settings(GEMINI_API_KEY="g-key"), with_sdks(**mods):
            result = llm.generate(system="sys", prompt="where is the keynote")
        assert result.text == "The keynote is in Hall 1."
        assert result.provider == "gemini" and result.model == "gemini-2.5-pro"
        kwargs = mods["google.genai"]._client.models.generate_content.call_args.kwargs
        assert kwargs["model"] == "gemini-2.5-pro"
        assert kwargs["config"].kwargs["system_instruction"] == "sys"
        assert kwargs["config"].kwargs["max_output_tokens"] == 900

    def test_safety_block_returns_none(self):
        mods = fake_genai("blocked", block_reason="SAFETY")
        with settings(GEMINI_API_KEY="g-key"), with_sdks(**mods):
            assert llm.generate(system="s", prompt="p") is None

    def test_legacy_sdk_is_used_when_new_one_is_absent(self):
        mods = fake_legacy_gemini("Legacy answer")
        with settings(GEMINI_API_KEY="g-key"), with_sdks(**mods):
            result = llm.generate(system="sys", prompt="p")
        assert result.text == "Legacy answer" and result.provider == "gemini"
        mods["google.generativeai"].GenerativeModel.assert_called_once()

    def test_api_error_returns_none(self):
        mods = fake_genai()
        mods["google.genai"]._client.models.generate_content.side_effect = \
            RuntimeError("quota")
        with settings(GEMINI_API_KEY="g-key"), with_sdks(**mods):
            assert llm.generate(system="s", prompt="p") is None


# ─── OpenAI ──────────────────────────────────────────────────────
class TestOpenAI:
    def test_generate(self):
        module = fake_openai("The poster hall is on level 2.")
        with settings(OPENAI_API_KEY="sk-o"), with_sdks(openai=module):
            result = llm.generate(system="sys", prompt="poster hall?")
        assert result.text == "The poster hall is on level 2."
        assert result.provider == "openai" and result.model == "gpt-4o"
        kwargs = module._client.chat.completions.create.call_args.kwargs
        assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
        assert kwargs["messages"][1]["content"] == "poster hall?"

    def test_base_url_is_passed_through(self):
        module = fake_openai()
        with settings(OPENAI_API_KEY="sk-o",
                      OPENAI_BASE_URL="http://localhost:11434/v1"), \
             with_sdks(openai=module):
            llm.generate(system="s", prompt="p")
        assert module.OpenAI.call_args.kwargs["base_url"] == \
            "http://localhost:11434/v1"

    def test_max_completion_tokens_retry(self):
        module = fake_openai(
            "Retried fine",
            raises=TypeError("Unsupported parameter: 'max_tokens' is not "
                             "supported with this model"))
        with settings(OPENAI_API_KEY="sk-o"), with_sdks(openai=module):
            result = llm.generate(system="s", prompt="p")
        assert result.text == "Retried fine"
        calls = module._client.chat.completions.create.call_args_list
        assert "max_tokens" in calls[0].kwargs
        assert "max_completion_tokens" in calls[1].kwargs

    def test_other_errors_are_not_retried(self):
        module = fake_openai(raises=RuntimeError("invalid api key"))
        with settings(OPENAI_API_KEY="sk-o"), with_sdks(openai=module):
            assert llm.generate(system="s", prompt="p") is None
        assert module._client.chat.completions.create.call_count == 1

    def test_content_filter_returns_none(self):
        module = fake_openai("blocked", finish_reason="content_filter")
        with settings(OPENAI_API_KEY="sk-o"), with_sdks(openai=module):
            assert llm.generate(system="s", prompt="p") is None

    def test_null_content_returns_none(self):
        module = fake_openai(None)
        with settings(OPENAI_API_KEY="sk-o"), with_sdks(openai=module):
            assert llm.generate(system="s", prompt="p") is None


# ─── generate() contract ─────────────────────────────────────────
class TestGenerateContract:
    def test_no_provider_returns_none(self):
        with settings():
            assert llm.generate(system="s", prompt="p") is None

    def test_explicit_provider_must_be_ready(self):
        module = fake_anthropic()
        with settings(ANTHROPIC_API_KEY="a"), with_sdks(anthropic=module):
            assert llm.generate(system="s", prompt="p", provider="openai") is None
            assert llm.generate(system="s", prompt="p",
                                provider="anthropic").provider == "anthropic"

    def test_blank_model_is_refused(self):
        module = fake_anthropic()
        with settings(ANTHROPIC_API_KEY="a", ANTHROPIC_MODEL=""), \
             with_sdks(anthropic=module):
            assert llm.generate(system="s", prompt="p") is None

    def test_max_tokens_override(self):
        module = fake_anthropic()
        with settings(ANTHROPIC_API_KEY="a"), with_sdks(anthropic=module):
            llm.generate(system="s", prompt="p", max_tokens=42)
        assert module._client.messages.create.call_args.kwargs["max_tokens"] == 42

    def test_unknown_provider_name(self):
        with settings():
            assert llm.generate(system="s", prompt="p", provider="llama") is None


# ─── Wiring into the companion API ───────────────────────────────
class TestProvidersEndpoint:
    def test_organizer_sees_roster(self, client, db):
        from app.models.user import UserRole
        from tests.conftest import auth_cookie, make_user
        admin = make_user(db, email="llm1@t.com", role=UserRole.admin)
        r = client.get("/api/companion/providers", cookies=auth_cookie(admin))
        assert r.status_code == 200
        data = r.json()
        assert data["active"] is None          # nothing configured in tests
        assert {row["name"] for row in data["providers"]} == {
            "anthropic", "gemini", "openai"}

    def test_attendee_blocked(self, client, db):
        from app.models.user import UserRole
        from tests.conftest import auth_cookie, make_user
        att = make_user(db, email="llm2@t.com", role=UserRole.attendee)
        assert client.get("/api/companion/providers",
                          cookies=auth_cookie(att)).status_code == 403
