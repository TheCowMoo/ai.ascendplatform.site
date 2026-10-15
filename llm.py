"""
Multi-provider LLM routing for the agent platform.

Maps a provider name to an AutoGen ``ChatCompletionClient`` so agents can be
"designated" to different models from the admin UI (e.g. Gemini = image artist,
Claude = planner, DeepSeek = coder).

AutoGen is imported lazily inside :func:`get_model_client`, so the rest of the
app (the widget + Meta chat path, which uses the plain OpenAI SDK) keeps working
even if the agent dependencies are not installed yet.

Environment:
  DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / MODEL
  ANTHROPIC_API_KEY / ANTHROPIC_MODEL
  GEMINI_API_KEY / GEMINI_BASE_URL / GEMINI_MODEL
"""

import os


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


# Metadata for every provider an agent can be designated to.
# ``env_key`` is the credential the provider needs; ``base_url_env`` is an
# optional OpenAI-compatible endpoint override. ``native`` marks providers that
# ship a dedicated client in ``autogen-ext`` (the rest use the OpenAI-compatible
# client).
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url": "https://api.deepseek.com",
        "model_env": "MODEL",
        "default_model": "deepseek-chat",
        "family": "deepseek",
        "vision": False,
        "tools": True,
        "native": True,
    },
    "anthropic": {
        "label": "Claude (Anthropic)",
        "env_key": "ANTHROPIC_API_KEY",
        "base_url_env": "ANTHROPIC_BASE_URL",
        "base_url": "",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-4-5",
        "family": "anthropic",
        "vision": True,
        "tools": True,
        "native": True,
    },
    "gemini": {
        "label": "Google Gemini",
        "env_key": "GEMINI_API_KEY",
        "base_url_env": "GEMINI_BASE_URL",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",
        "family": "gemini",
        "vision": True,
        "tools": True,
        "native": False,
    },
}

DEFAULT_PROVIDER = "deepseek"

# ---------------------------------------------------------------------------
# LiteLLM gateway (optional)
#
# When LLM_GATEWAY_URL points at a LiteLLM proxy, every provider is reached
# through that one OpenAI-compatible endpoint. That gives a single code path for
# all providers plus per-key spend tracking, retries and fallbacks, and means the
# app no longer needs each provider's key locally. Leave it unset to talk to the
# providers directly (the default).
# ---------------------------------------------------------------------------
LLM_GATEWAY_URL = _env("LLM_GATEWAY_URL").rstrip("/")
LLM_GATEWAY_KEY = _env("LLM_GATEWAY_KEY", "sk-cmgrowthai")


def gateway_enabled():
    return bool(LLM_GATEWAY_URL)


def gateway_info():
    return {"enabled": gateway_enabled(), "url": LLM_GATEWAY_URL}


def _gateway_model(provider, model_name):
    """LiteLLM model ids are 'provider/model' (e.g. anthropic/claude-sonnet-4-5)."""
    if not model_name or "/" in model_name:
        return model_name
    return "%s/%s" % (provider, model_name)


def provider_names():
    """All known provider identifiers."""
    return list(PROVIDERS.keys())


def _resolve_api_key(spec):
    return _env(spec["env_key"])


def _resolve_model(spec, model=None):
    if model:
        return model
    return _env(spec["model_env"], spec["default_model"]) or spec["default_model"]


def _resolve_base_url(spec):
    if spec["base_url_env"]:
        return _env(spec["base_url_env"], spec["base_url"])
    return spec["base_url"]


def is_configured(provider):
    """True when the provider is usable (a local key, or the gateway is on)."""
    if gateway_enabled():
        return provider in PROVIDERS
    spec = PROVIDERS.get(provider)
    return bool(spec and _resolve_api_key(spec))


def _gateway_client(provider, spec, model_name, temperature, extra):
    """Build an OpenAI-compatible client pointed at the LiteLLM gateway."""
    try:
        from autogen_core.models import ModelInfo
        from autogen_ext.models.openai import OpenAIChatCompletionClient
    except ImportError as exc:
        raise ValueError(
            "AutoGen is not installed or incomplete (%s). Install "
            "autogen-agentchat and autogen-ext[anthropic,openai]." % (exc,)
        )

    options = {
        "model": _gateway_model(provider, model_name),
        "api_key": LLM_GATEWAY_KEY,
        "base_url": LLM_GATEWAY_URL,
        "model_info": ModelInfo(
            vision=spec["vision"],
            function_calling=spec["tools"],
            json_output=True,
            family=provider,
            structured_output=True,
        ),
    }
    if temperature is not None:
        options["temperature"] = temperature
    options.update(extra)
    return OpenAIChatCompletionClient(**options)


def list_providers():
    """Serialisable provider status for the admin UI / ``/api/providers``.

    Never includes secrets — only whether a key is present.
    """
    result = []
    for name, spec in PROVIDERS.items():
        result.append({
            "name": name,
            "label": spec["label"],
            "model": _resolve_model(spec),
            "configured": is_configured(name),
            "requires": spec["env_key"],
            "supports_tools": spec["tools"],
            "vision": spec["vision"],
            "native_client": spec["native"],
            "via_gateway": gateway_enabled(),
        })
    return result


def configured_providers():
    """Names of the providers that are ready to use."""
    return [name for name in PROVIDERS if is_configured(name)]


def get_model_client(provider, model=None, temperature=None, **kwargs):
    """Build an AutoGen ``ChatCompletionClient`` for ``provider``.

    Raises ``ValueError`` when the provider is unknown, its API key is missing,
    or AutoGen is not installed.
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(
            "Unknown provider %r (expected one of: %s)"
            % (provider, ", ".join(PROVIDERS))
        )

    model_name = _resolve_model(spec, model)

    if gateway_enabled():
        # The gateway holds the provider keys, so no local key is required.
        return _gateway_client(provider, spec, model_name, temperature, kwargs)

    api_key = _resolve_api_key(spec)
    if not api_key:
        raise ValueError(
            "Provider %r is not configured: set %s" % (provider, spec["env_key"])
        )

    try:
        if provider == "anthropic":
            # autogen-ext ships a dedicated Claude client.
            from autogen_ext.models.anthropic import AnthropicChatCompletionClient

            opts = {"model": model_name, "api_key": api_key}
            if temperature is not None:
                opts["temperature"] = temperature
            opts.update(kwargs)
            return AnthropicChatCompletionClient(**opts)

        # OpenAI-compatible path: DeepSeek and Gemini (no native Gemini client).
        from autogen_core.models import ModelInfo
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        opts = {
            "model": model_name,
            "api_key": api_key,
            "model_info": ModelInfo(
                vision=spec["vision"],
                function_calling=spec["tools"],
                json_output=True,
                family=spec["family"],
                structured_output=True,
            ),
        }
        base_url = _resolve_base_url(spec)
        if base_url:
            opts["base_url"] = base_url
        if temperature is not None:
            opts["temperature"] = temperature
        opts.update(kwargs)
        return OpenAIChatCompletionClient(**opts)

    except ImportError as exc:
        raise ValueError(
            "AutoGen is not installed or incomplete (%s). Install "
            "autogen-agentchat and autogen-ext[anthropic,openai] "
            "(plus paramiko/google-genai for the action tools)." % (exc,)
        )
