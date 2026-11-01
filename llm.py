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


def _gateway_client(provider, spec, model_name, temperature, extra, label=""):
    """Build a metered, OpenAI-compatible client pointed at the LiteLLM gateway."""
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
    client = _metered_openai_class()(**options)
    capture = _instrument_openai_client(client)
    return _decorate_metered(client, provider, model_name, label, capture)


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
    """Rebuild an outgoing payload so no message can carry an empty text block.

    Anthropic rejects the whole request with 400 "messages: text content blocks must be
    non-empty" for a block of ``""``, for a message whose content is ``""``, and for a
    message whose content is an **empty list** - which is what AutoGen sends after a
    turn that produced no text (a bare tool result, for instance). Filtering those out
    was not enough, so each message is rebuilt from known-good parts and any that ends
    up empty is dropped.
    """
    cleaned = []
    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "user"
        content = message.get("content")

        # The rule is adjacency, not existence: a tool_result must reference a tool_use
        # in the *immediately previous* message. Checking the whole payload let through
        # results whose tool_use sat somewhere else, which the API rejects with
        # "unexpected tool_use_id found in tool_result blocks".
        previous = messages[index - 1] if index else None
        usable = set()
        if isinstance(previous, dict) and isinstance(previous.get("content"), list):
            for block in previous["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                    usable.add(block["id"])

        if isinstance(content, str):
            cleaned.append({"role": role, "content": content.strip() or "(no content)"})
            continue

        if isinstance(content, list):
            blocks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text":
                    if str(block.get("text") or "").strip():
                        blocks.append(block)
                elif kind == "tool_result":
                    if block.get("tool_use_id") in usable:
                        blocks.append(block)
                else:
                    blocks.append(block)  # tool_use, image
            # Never drop a message: a tool_result must sit in the message immediately
            # after the tool_use that produced it, so removing an "empty" turn in
            # between orphans the result.
            cleaned.append({"role": role, "content": blocks or "(no content)"})

    if not cleaned:
        raise RuntimeError("No usable messages: every message was empty.")
    return cleaned


def _shapes(messages):
    """A short description of a request's messages, for an error that diagnoses itself."""
    parts = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        role = message.get("role") if isinstance(message, dict) else type(message).__name__
        if isinstance(content, list):
            kinds = ",".join(
                str(block.get("type")) for block in content if isinstance(block, dict)
            )
            parts.append("%s[%s]" % (role, kinds))
        else:
            parts.append("%s:%r" % (role, str(content)[:24]))
    return " ".join(parts)[:600]


# ---------------------------------------------------------------------------
# Usage capture
#
# AutoGen folds a provider's usage into RequestUsage(prompt_tokens, completion_tokens)
# and throws away cache reads, cache writes, reasoning tokens and the model name the
# provider actually returned. Those are exactly the fields that decide the price, so the
# SDK resource is wrapped here and the raw figures are kept on the client until the
# metered subclass has recorded them.
# ---------------------------------------------------------------------------
class RawUsage:
    """One request's raw token split, before it is priced."""

    def __init__(self):
        self.split = {}

    def record(self, split):
        if not split:
            return
        merged = dict(self.split)
        merged.update(split)
        # A later, seen split wins; an empty streaming chunk never erases a real one.
        if split.get("seen") or not self.split.get("seen"):
            self.split = merged

    def take(self):
        split = self.split
        self.split = {}
        return split


def _split_openai(usage_obj, model, capture):
    import usage as usage_service

    capture.record(usage_service.normalise_openai(usage_obj, model))


def _split_anthropic(usage_obj, model, capture):
    import usage as usage_service

    capture.record(usage_service.normalise_anthropic(usage_obj, model))


async def _close_quietly(stream):
    close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # pragma: no cover - closing an already-closed stream
        pass


async def _tee_openai_stream(stream, capture):
    """Pass chunks through, recording the final usage and the real model name."""
    try:
        async for chunk in stream:
            model = getattr(chunk, "model", None)
            payload = getattr(chunk, "usage", None)
            if payload is not None:
                _split_openai(payload, model or "", capture)
            elif model and not capture.split.get("model"):
                capture.split["model"] = str(model)
            yield chunk
    finally:
        await _close_quietly(stream)


async def _tee_anthropic_stream(stream, capture):
    """Anthropic sends input and cache figures on message_start, output on message_delta."""
    model = ""
    try:
        async for event in stream:
            kind = getattr(event, "type", "")
            if kind == "message_start":
                message = getattr(event, "message", None)
                model = str(getattr(message, "model", "") or model)
                payload = getattr(message, "usage", None)
                if payload is not None:
                    _split_anthropic(payload, model, capture)
            elif kind == "message_delta":
                payload = getattr(event, "usage", None)
                output = int(getattr(payload, "output_tokens", 0) or 0) if payload else 0
                if output:
                    current = capture.split or {}
                    current["output"] = output
                    current["seen"] = True
                    current["model"] = model or current.get("model", "")
                    capture.split = current
            yield event
    finally:
        await _close_quietly(stream)


def _instrument_openai_client(client):
    """Wrap the OpenAI-compatible SDK resource. Returns the capture, or None."""
    capture = RawUsage()
    sdk = getattr(client, "_client", None)
    if sdk is None:
        return None
    completions = getattr(getattr(sdk, "chat", None), "completions", None)
    original = getattr(completions, "create", None) if completions is not None else None
    if original is None:
        return None

    async def create(*args, **kwargs):
        response = await original(*args, **kwargs)
        if kwargs.get("stream"):
            return _tee_openai_stream(response, capture)
        _split_openai(getattr(response, "usage", None), getattr(response, "model", ""),
                      capture)
        return response

    try:
        completions.create = create
    except (AttributeError, TypeError):  # pragma: no cover - slotted resource
        return None

    # Structured output takes the beta resource. The agent path does not use it today,
    # but a meter that silently misses one path is worse than one that is visibly partial.
    beta = getattr(getattr(sdk, "beta", None), "chat", None)
    beta_completions = getattr(beta, "completions", None) if beta is not None else None
    beta_original = (getattr(beta_completions, "parse", None)
                     if beta_completions is not None else None)
    if beta_original is not None:
        async def parse(*args, **kwargs):
            response = await beta_original(*args, **kwargs)
            _split_openai(getattr(response, "usage", None),
                          getattr(response, "model", ""), capture)
            return response

        try:
            beta_completions.parse = parse
        except (AttributeError, TypeError):  # pragma: no cover
            pass
    return capture


def _include_usage_error(exc):
    """True when the provider refused ``stream_options.include_usage`` itself."""
    text = str(exc).lower()
    return "include_usage" in text or "stream_options" in text


class _MeteredMixin:
    """Record every model call on the client AutoGen was given.

    Subclassed, not wrapped: AssistantAgent type-checks its client, which is also why the
    Anthropic cleanup client is a subclass. Accounting failures are printed by the usage
    service and never raised - a broken meter must not lose an answer.
    """

    _usage_provider = ""
    _usage_model = ""
    _usage_label = ""
    _usage_capture = None

    def _usage_guard(self):
        import usage as usage_service

        usage_service.guard()

    def _usage_record(self, result):
        import usage as usage_service

        capture = self._usage_capture
        split = capture.take() if capture is not None else {}
        source = "raw"
        if not split.get("seen"):
            raw = getattr(result, "usage", None) if result is not None else None
            if raw is not None:
                split = usage_service.normalise_autogen(raw, self._usage_model)
                source = "autogen-fallback"
            else:
                split = {"model": self._usage_model, "seen": False}
                source = "missing"
        usage_service.record_model(
            provider=self._usage_provider,
            model_configured=self._usage_model,
            model_returned=split.get("model") or "",
            label=self._usage_label,
            split=split,
            usage_source=source,
        )

    async def create(self, *args, **kwargs):
        self._usage_guard()
        result = await super().create(*args, **kwargs)
        self._usage_record(result)
        return result


def _decorate_metered(client, provider, model, label, capture):
    client._usage_provider = provider
    client._usage_model = model
    client._usage_label = label or ""
    client._usage_capture = capture
    return client


def _metered_openai_class():
    """A metered subclass of the OpenAI-compatible client, built when AutoGen is present.

    Streaming forces ``stream_options.include_usage``: without it the provider reports no
    tokens at all. A provider that rejects the flag is retried once without it, and the
    event is marked ``autogen-fallback`` rather than silently costing nothing.
    """
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    class MeteredOpenAIClient(_MeteredMixin, OpenAIChatCompletionClient):
        async def create_stream(self, *args, **kwargs):
            self._usage_guard()
            allow_retry = "include_usage" not in kwargs or kwargs.get(
                "include_usage") is True
            kwargs.setdefault("include_usage", True)
            produced = 0
            last = None
            try:
                try:
                    async for chunk in super().create_stream(*args, **kwargs):
                        produced += 1
                        last = chunk
                        yield chunk
                except Exception as exc:
                    if produced or not allow_retry or not _include_usage_error(exc):
                        raise
                    kwargs.pop("include_usage", None)
                    async for chunk in super().create_stream(*args, **kwargs):
                        last = chunk
                        yield chunk
            finally:
                self._usage_record(last)

    return MeteredOpenAIClient


def _metered_anthropic_class(base):
    """A metered subclass over the message-cleaning Anthropic client."""

    class MeteredAnthropicClient(_MeteredMixin, base):
        async def create_stream(self, *args, **kwargs):
            self._usage_guard()
            last = None
            try:
                async for chunk in super().create_stream(*args, **kwargs):
                    last = chunk
                    yield chunk
            finally:
                self._usage_record(last)

    return MeteredAnthropicClient


def _patch_anthropic_client(client):
    """Sanitise the request the SDK sends, and keep the usage it returns.

    Returns the client; ``client._usage_capture`` is set when the wrapper installed, so
    the metered subclass can attach the raw figures to the call it just made.
    """
    capture = RawUsage()
    try:
        resource = client._client.messages
        original = resource.create
    except AttributeError:  # pragma: no cover - a different autogen-ext layout
        return client

    async def create(*args, **kwargs):
        kwargs["messages"] = without_empty_blocks(kwargs.get("messages"))
        # Fail here, with the index, rather than paying for a 400 that names only a
        # message position and leaves us guessing at the shape.
        assert_valid(kwargs["messages"])
        response = await original(*args, **kwargs)
        if kwargs.get("stream"):
            return _tee_anthropic_stream(response, capture)
        _split_anthropic(getattr(response, "usage", None),
                         getattr(response, "model", ""), capture)
        return response

    try:
        resource.create = create
    except (AttributeError, TypeError):  # pragma: no cover - slotted resource
        return client
    client._usage_capture = capture
    return client


def assert_valid(messages):
    """Raise with the offending index instead of letting Anthropic answer 400.

    ``without_empty_blocks`` repairs the payload, so anything this catches is a shape it
    does not know about yet. Failing here costs nothing and names the message and block;
    the API's version of the same complaint arrives after a job has burned tokens and
    reads like "messages.0.content.3: unexpected tool_use_id" with no context.
    """
    if not messages:
        raise ValueError("payload has no messages")
    # A leading system message is legitimate - AutoGen includes one when the system
    # prompt is passed as a message rather than a client argument - so it is not checked.
    for index, message in enumerate(messages):
        content = message.get("content")
        if not content:
            raise ValueError("message %d (%s) has empty content" % (index, message.get("role")))
        if not isinstance(content, list):
            continue
        previous = messages[index - 1] if index else None
        usable = set()
        if isinstance(previous, dict) and isinstance(previous.get("content"), list):
            usable = {block.get("id") for block in previous["content"]
                      if isinstance(block, dict) and block.get("type") == "tool_use"}
        for position, block in enumerate(content):
            if not isinstance(block, dict):
                raise ValueError("message %d block %d is not an object" % (index, position))
            if block.get("type") == "text" and not str(block.get("text") or "").strip():
                raise ValueError("message %d block %d is an empty text block"
                                 % (index, position))
            if block.get("type") == "tool_result" and block.get("tool_use_id") not in usable:
                raise ValueError(
                    "message %d block %d is a tool_result for %r with no matching "
                    "tool_use in message %d"
                    % (index, position, block.get("tool_use_id"), index - 1)
                )
    return True


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

    # Which agent asked. Recorded on every usage event, so "who cost this" is answerable
    # without guessing from the job's prompt. Popped here: it is ours, not the client's.
    label = str(kwargs.pop("usage_label", "") or "")

    if gateway_enabled():
        # The gateway holds the provider keys, so no local key is required.
        return _gateway_client(provider, spec, model_name, temperature, kwargs, label)

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

            client = _metered_anthropic_class(CleanMessagesClient)(**opts)
            client = _patch_anthropic_client(client)
            return _decorate_metered(client, provider, model_name, label,
                                     getattr(client, "_usage_capture", None))

        # OpenAI-compatible path: DeepSeek and Gemini (no native Gemini client).
        from autogen_core.models import ModelInfo

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
        client = _metered_openai_class()(**opts)
        capture = _instrument_openai_client(client)
        return _decorate_metered(client, provider, model_name, label, capture)

    except ImportError as exc:
        raise ValueError(
            "AutoGen is not installed or incomplete (%s). Install "
            "autogen-agentchat and autogen-ext[anthropic,openai] "
            "(plus paramiko/google-genai for the action tools)." % (exc,)
        )
