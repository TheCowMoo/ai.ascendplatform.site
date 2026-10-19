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

import dataclasses
import os


def _env(name, default=""):
    """Resolve a setting: the Settings panel first, then the environment.

    Reading ``os.environ`` directly is why the Test button said "No provider has a
    key yet" while every key was filled in: the panel writes to the *database*, not
    to the process environment. Every other tool (pexels, ssh, mail) already goes
    through ``settings.get``; this module was the one exception.

    ``settings.get`` itself resolves panel -> environment -> SPEC default, so a
    deployment that configures keys in ``.env`` keeps working unchanged.
    """
    try:
        import settings
        return str(settings.get(name, default) or "").strip()
    except Exception:
        # settings needs the database; before it is reachable (or in a bare import
        # of this module, as tests and one-off scripts do) the environment is still
        # the right answer.
        return (os.environ.get(name, default) or "").strip()


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
LLM_GATEWAY_KEY = _env("LLM_GATEWAY_KEY", "sk-ascendplatform")


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


def _clean_messages(messages):
    """Drop empty text blocks, which Anthropic rejects with a 400.

    AutoGen serialises an assistant turn that held only tool calls with a text block
    of ``""``, and the API answers "messages: text content blocks must be non-empty" -
    mid-run, after those tools have already fired. Removing the empty block changes
    nothing the model would otherwise see.
    """
    cleaned = []
    for message in messages or []:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            if content.strip():
                cleaned.append(message)
        elif isinstance(content, list):
            kept = [
                item for item in content
                if not (isinstance(item, str) and not item.strip())
            ]
            if kept:
                cleaned.append(
                    message if len(kept) == len(content)
                    else dataclasses.replace(message, content=kept)
                )
        else:
            cleaned.append(message)
    return cleaned


def without_empty_blocks(messages):
    """Drop ``{"type": "text", "text": ""}`` from an Anthropic request payload.

    autogen-ext introduces that block while *it* converts messages, after any hook we
    control, and Anthropic rejects the entire request with 400 "messages: text content
    blocks must be non-empty". The block only exists in the payload the SDK is about to
    send, so that is where it has to be removed.
    """
    cleaned = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            kept = [
                block for block in content
                if not (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and not str(block.get("text") or "").strip()
                )
            ]
            if kept != content:
                message = dict(message, content=kept)
        cleaned.append(message)
    return cleaned


def _patch_anthropic_client(client):
    """Sanitise the request the SDK actually sends, not the messages we hand over."""
    try:
        resource = client._client.messages
        original = resource.create
    except AttributeError:  # pragma: no cover - a different autogen-ext layout
        return client

    async def create(*args, **kwargs):
        kwargs["messages"] = without_empty_blocks(kwargs.get("messages"))
        return await original(*args, **kwargs)

    try:
        resource.create = create
    except (AttributeError, TypeError):  # pragma: no cover - slotted resource
        return client
    return client


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
            from autogen_core.models import ModelInfo
            from autogen_ext.models.anthropic import AnthropicChatCompletionClient

            opts = {
                "model": model_name,
                "api_key": api_key,
                # Supply model_info ourselves. Without it this client falls back to
                # autogen-ext's model registry, which answers function_calling=False
                # for *any* model name it does not recognise - and AssistantAgent then
                # refuses to attach tools at all ("The model does not support function
                # calling"). A custom model string was enough to break the whole team
                # build. Our PROVIDERS table already knows the truth. This client never
                # reads `family`, so spec["family"] is safe here.
                "model_info": ModelInfo(
                    vision=spec["vision"],
                    function_calling=spec["tools"],
                    json_output=True,
                    family=spec["family"],
                    structured_output=False,
                ),
            }
            if temperature is not None:
                opts["temperature"] = temperature
            opts.update(kwargs)

            class CleanMessagesClient(AnthropicChatCompletionClient):
                """The Claude client, with the empty-content bug papered over.

                Subclassed rather than wrapped because AssistantAgent type-checks the
                client it is given; a delegating proxy risks failing that check.
                """

                async def create(self, messages, *args, **kwargs):
                    return await super().create(_clean_messages(messages), *args, **kwargs)

                async def create_stream(self, messages, *args, **kwargs):
                    async for chunk in super().create_stream(
                        _clean_messages(messages), *args, **kwargs
                    ):
                        yield chunk

            return _patch_anthropic_client(CleanMessagesClient(**opts))

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
