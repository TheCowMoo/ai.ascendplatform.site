"""Smoke test for the LiteLLM gateway routing (stage S4).

Run: .\\venv\\Scripts\\python.exe tests\\smoke_llm_gateway.py

The gateway is enabled purely through environment variables, so the enabled path
is verified by importing ``llm`` in a subprocess with ``LLM_GATEWAY_URL`` set.
No network is used — constructing a client makes no request.
"""

import json
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Direct mode is the default: make sure the ambient environment cannot enable it.
for _var in ("LLM_GATEWAY_URL", "LLM_GATEWAY_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_var, None)

import llm  # noqa: E402

GATEWAY = "http://litellm:4000"

PROBE = textwrap.dedent(
    """
    import json, os, sys
    sys.path.insert(0, r"%(root)s")
    # The gateway holds the provider keys, so none are set locally on purpose.
    for var in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        os.environ.pop(var, None)
    import llm
    result = {
        "gateway_enabled": llm.gateway_enabled(),
        "gateway": llm.gateway_info(),
        "configured": llm.configured_providers(),
        "models": {
            "deepseek": llm._gateway_model("deepseek", "deepseek-chat"),
            "anthropic": llm._gateway_model("anthropic", "claude-sonnet-4-5"),
            "prefixed": llm._gateway_model("gemini", "gemini/gemini-2.5-flash"),
            "blank": llm._gateway_model("gemini", ""),
        },
    }
    client = llm.get_model_client("deepseek")
    result["client_type"] = type(client).__name__
    result["base_url"] = str(getattr(getattr(client, "_client", None), "base_url", ""))
    print(json.dumps(result))
    """
) % {"root": ROOT}


def test_direct_mode():
    """Without LLM_GATEWAY_URL each provider is called directly and needs a key."""
    assert llm.gateway_enabled() is False, llm.gateway_info()
    assert llm.gateway_info() == {"enabled": False, "url": ""}
    try:
        llm.get_model_client("deepseek")
    except ValueError as exc:
        assert "not configured" in str(exc), exc
        print("direct mode needs a local key:", exc)
    else:
        raise AssertionError("expected a missing-key error in direct mode")
    print("DIRECT MODE OK")


def test_gateway_mode():
    env = dict(os.environ, LLM_GATEWAY_URL=GATEWAY, LLM_GATEWAY_KEY="sk-test")
    for var in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        env.pop(var, None)

    proc = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    print(json.dumps(info, indent=1))

    assert info["gateway_enabled"] is True
    assert info["gateway"]["url"] == GATEWAY
    # No local keys at all, yet every provider is usable via the gateway.
    assert set(info["configured"]) == {"deepseek", "anthropic", "gemini"}, info["configured"]
    assert info["models"]["deepseek"] == "deepseek/deepseek-chat"
    assert info["models"]["anthropic"] == "anthropic/claude-sonnet-4-5"
    assert info["models"]["prefixed"] == "gemini/gemini-2.5-flash"  # already prefixed
    assert info["models"]["blank"] == ""
    # One client class for every provider, pointed at the gateway.
    assert info["client_type"] == "OpenAIChatCompletionClient", info["client_type"]
    assert GATEWAY in info["base_url"], info["base_url"]
    print("GATEWAY MODE OK")


if __name__ == "__main__":
    test_direct_mode()
    test_gateway_mode()
