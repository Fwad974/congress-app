"""
Optional LLM backend — Anthropic Claude, Google Gemini, or OpenAI.

The app never *requires* an LLM: the AI Congress Companion answers from live
congress data with its own rules, and everything here is an enhancement for
free-form questions. That shapes the whole module:

- **Nothing is a hard dependency.** Each provider's SDK is imported lazily
  inside its call. A provider is "ready" only when its API key is set *and*
  its SDK imports; otherwise it is simply absent from the roster.
- **Failures are never fatal.** ``generate()`` returns ``None`` on a missing
  provider, a refusal, a safety block, a network error or an SDK change, and
  the caller falls back to its deterministic answer.
- **Which provider is used is configuration, not code.** ``LLM_PROVIDER``
  picks one explicitly (``anthropic`` / ``gemini`` / ``openai``), ``off``
  disables the LLM path entirely, and ``auto`` (the default) takes the first
  ready provider in ``PREFERENCE`` order.

Install whichever SDK you want to use:

    pip install anthropic          # Claude
    pip install google-genai       # Gemini  (legacy google-generativeai also works)
    pip install openai             # OpenAI, or any OpenAI-compatible endpoint
"""
import importlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "gemini", "openai")
# Order used when LLM_PROVIDER=auto.
PREFERENCE = ("anthropic", "gemini", "openai")

PROVIDER_LABELS = {
    "anthropic": "Claude",
    "gemini": "Gemini",
    "openai": "OpenAI",
}

# The SDK import path(s) each provider can use, in order of preference.
_SDK_MODULES = {
    "anthropic": ("anthropic",),
    "gemini": ("google.genai", "google.generativeai"),
    "openai": ("openai",),
}

DEFAULT_MAX_TOKENS = 900


@dataclass
class ProviderStatus:
    name: str
    label: str
    configured: bool      # an API key is set
    installed: bool       # the SDK imports
    model: str
    ready: bool           # both of the above

    def as_dict(self) -> Dict:
        return {"name": self.name, "label": self.label,
                "configured": self.configured, "installed": self.installed,
                "model": self.model, "ready": self.ready}


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str


# ─── Configuration ───────────────────────────────────────────────
def api_key_for(provider: str) -> str:
    settings = get_settings()
    return {
        "anthropic": settings.ANTHROPIC_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }.get(provider, "") or ""


def model_for(provider: str) -> str:
    settings = get_settings()
    if provider == "anthropic":
        # COMPANION_MODEL is the older name for the same setting; when it's
        # set it still wins, so existing deployments keep their model.
        return settings.COMPANION_MODEL or settings.ANTHROPIC_MODEL
    return {"gemini": settings.GEMINI_MODEL,
            "openai": settings.OPENAI_MODEL}.get(provider, "")


def _sdk_installed(provider: str) -> bool:
    for module in _SDK_MODULES.get(provider, ()):
        try:
            importlib.import_module(module)
            return True
        except ImportError:
            continue
        except Exception:  # a broken install shouldn't crash the roster
            continue
    return False


def provider_status() -> List[ProviderStatus]:
    """Every provider with its configuration and SDK state."""
    rows = []
    for name in PROVIDERS:
        configured = bool(api_key_for(name))
        installed = _sdk_installed(name)
        rows.append(ProviderStatus(
            name=name, label=PROVIDER_LABELS[name], configured=configured,
            installed=installed, model=model_for(name),
            ready=configured and installed))
    return rows


def available_providers() -> List[str]:
    return [row.name for row in provider_status() if row.ready]


def _choice() -> str:
    return (get_settings().LLM_PROVIDER or "auto").strip().lower()


def _disabled() -> bool:
    """LLM_PROVIDER=off is a kill switch: no call, whatever else is set."""
    return _choice() in ("off", "none", "disabled")


def active_provider() -> Optional[str]:
    """The provider that would serve the next call, or None when there is none."""
    choice = _choice()
    if _disabled():
        return None
    ready = available_providers()
    if choice in PROVIDERS:
        return choice if choice in ready else None
    # auto (or an unrecognized value): first ready provider by preference.
    return next((name for name in PREFERENCE if name in ready), None)


def is_available() -> bool:
    return active_provider() is not None


# ─── Backends ────────────────────────────────────────────────────
def _call_anthropic(system: str, prompt: str, model: str,
                    max_tokens: int) -> Optional[str]:
    anthropic = importlib.import_module("anthropic")
    client = anthropic.Anthropic(api_key=api_key_for("anthropic"))
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    if getattr(response, "stop_reason", None) == "refusal":
        return None
    return "".join(block.text for block in response.content
                   if getattr(block, "type", "") == "text").strip() or None


def _call_gemini(system: str, prompt: str, model: str,
                 max_tokens: int) -> Optional[str]:
    try:
        genai = importlib.import_module("google.genai")
    except ImportError:
        return _call_gemini_legacy(system, prompt, model, max_tokens)

    client = genai.Client(api_key=api_key_for("gemini"))
    try:
        types_mod = importlib.import_module("google.genai.types")
        config = types_mod.GenerateContentConfig(
            system_instruction=system, max_output_tokens=max_tokens)
    except Exception:  # older/newer SDK shape — a plain dict is accepted too
        config = {"system_instruction": system, "max_output_tokens": max_tokens}
    response = client.models.generate_content(
        model=model, contents=prompt, config=config)
    return _gemini_text(response)


def _call_gemini_legacy(system: str, prompt: str, model: str,
                        max_tokens: int) -> Optional[str]:
    """The pre-`google-genai` package, still widely installed."""
    genai = importlib.import_module("google.generativeai")
    genai.configure(api_key=api_key_for("gemini"))
    generative_model = genai.GenerativeModel(model, system_instruction=system)
    response = generative_model.generate_content(
        prompt, generation_config={"max_output_tokens": max_tokens})
    return _gemini_text(response)


def _gemini_text(response) -> Optional[str]:
    """Pull the answer out, treating a safety block as "no answer"."""
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        return None
    try:
        text = getattr(response, "text", None)
    except Exception:
        # The SDK raises when the only candidate was blocked.
        return None
    return (text or "").strip() or None


def _call_openai(system: str, prompt: str, model: str,
                 max_tokens: int) -> Optional[str]:
    openai_mod = importlib.import_module("openai")
    settings = get_settings()
    kwargs = {"api_key": api_key_for("openai")}
    # Lets the same code point at any OpenAI-compatible endpoint
    # (Azure-style gateways, vLLM, Ollama, OpenRouter…).
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    client = openai_mod.OpenAI(**kwargs)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": prompt}]
    try:
        response = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens)
    except Exception as exc:
        # Newer OpenAI models reject `max_tokens` and want
        # `max_completion_tokens`; retry once on exactly that complaint.
        if "max_tokens" not in str(exc):
            raise
        response = client.chat.completions.create(
            model=model, messages=messages, max_completion_tokens=max_tokens)
    choice = response.choices[0]
    if getattr(choice, "finish_reason", "") == "content_filter":
        return None
    return (getattr(choice.message, "content", None) or "").strip() or None


_BACKENDS = {
    "anthropic": _call_anthropic,
    "gemini": _call_gemini,
    "openai": _call_openai,
}


# ─── Public entry point ──────────────────────────────────────────
def generate(*, system: str, prompt: str, max_tokens: Optional[int] = None,
             provider: Optional[str] = None) -> Optional[LLMResult]:
    """Ask the configured LLM. Returns None whenever an answer isn't produced.

    Callers are expected to have a non-LLM answer ready: a missing key, an
    uninstalled SDK, a refusal, a safety block, a timeout and an API change
    all land here as ``None`` rather than as an exception.
    """
    if provider:
        # An explicit choice still respects the global kill switch and still
        # has to be usable — we never silently substitute another provider.
        if _disabled() or provider not in available_providers():
            return None
        name = provider
    else:
        name = active_provider()
    if not name or name not in _BACKENDS:
        return None

    model = model_for(name)
    if not model:
        logger.warning("No model configured for LLM provider '%s'", name)
        return None
    limit = max_tokens or get_settings().COMPANION_MAX_TOKENS or DEFAULT_MAX_TOKENS

    try:
        text = _BACKENDS[name](system, prompt, model, limit)
    except Exception as exc:  # network, auth, rate limit, SDK drift…
        logger.warning("LLM call to %s (%s) failed: %s", name, model, exc)
        return None
    if not text:
        return None
    return LLMResult(text=text, provider=name, model=model)
