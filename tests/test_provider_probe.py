"""Provider probes must distinguish a working key from a revoked one.

Run: .\\venv\\Scripts\\python.exe tests\\test_provider_probe.py

No provider network call is made. ``llm._probe_transport`` is replaced with a fake
response so the test exercises the status-to-sentence mapping and secret redaction.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import jobs  # noqa: E402
import llm  # noqa: E402

FAILED = []
SECRET = "sk-ant-invalid-secret"


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name +
          ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def test_bad_anthropic_key_is_reported_without_leaking_it():
    original_key = llm._resolve_api_key
    original_model = llm._resolve_model
    original_transport = llm._probe_transport
    llm._resolve_api_key = lambda spec: SECRET
    llm._resolve_model = lambda spec, model=None: "claude-sonnet-4-5"
    llm._probe_transport = lambda url, headers, payload=None, timeout=12: (
        401, '{"error": {"type": "authentication_error", "message": "invalid x-api-key"}}')
    try:
        result = llm.probe_provider("anthropic")
    finally:
        llm._resolve_api_key = original_key
        llm._resolve_model = original_model
        llm._probe_transport = original_transport
    check("a revoked key fails the probe", result.get("ok"), False)
    check("the message names the provider and key rejection",
          "rejected the API key" in str(result.get("output")), True)
    check("the secret never appears in the result",
          SECRET in str(result.get("output")), False)
    print("a bad Anthropic key is a clear probe failure, not a 401 job traceback")


def test_working_key_is_accepted():
    original_key = llm._resolve_api_key
    original_model = llm._resolve_model
    original_transport = llm._probe_transport
    llm._resolve_api_key = lambda spec: "sk-good"
    llm._resolve_model = lambda spec, model=None: "deepseek-chat"
    llm._probe_transport = lambda url, headers, payload=None, timeout=12: (
        200, '{"id": "ok"}')
    try:
        result = llm.probe_provider("deepseek")
    finally:
        llm._resolve_api_key = original_key
        llm._resolve_model = original_model
        llm._probe_transport = original_transport
    check("a working key passes the probe", result.get("ok"), True)
    check("the pass message names the provider",
          "DeepSeek accepted" in str(result.get("output")), True)
    print("a working provider key passes the live probe")


def test_job_transcript_turns_auth_failure_into_action():
    class AuthenticationError(Exception):
        pass

    message = jobs._friendly_job_error(AuthenticationError(
        "401 - {'error': {'type': 'authentication_error', "
        "'message': 'invalid x-api-key'}}"))
    check("the job error names Anthropic", "Anthropic" in message, True)
    check("the job error names the settings screen",
          "Settings -> AI providers" in message, True)
    check("the job error says no work was done",
          "No work was done" in message, True)
    print("a provider 401 becomes an actionable job transcript error")


def test_a_402_becomes_a_credit_action():
    class APIStatusError(Exception):
        status_code = 402

    message = jobs._friendly_job_error(APIStatusError(
        "Error code: 402 - {'error': {'message': 'Insufficient Balance', "
        "'type': 'unknown_error'}}"))
    check("the job error names the 402", "402" in message, True)
    check("the job error says the provider has no credit",
          "no remaining credit" in message, True)
    check("the job error names the agent settings",
          "Settings -> Agents" in message, True)
    check("the 402 error says no work was done",
          "No work was done" in message, True)

    probe = llm._probe_error("DeepSeek", 402, "Insufficient Balance", "sk-secret")
    check("a 402 probe failure is actionable", "no remaining credit" in probe, True)
    check("the probe does not leak the key", "sk-secret" in probe, False)
    print("a provider 402 becomes a top-up-or-switch action")


def main():
    test_bad_anthropic_key_is_reported_without_leaking_it()
    test_working_key_is_accepted()
    test_job_transcript_turns_auth_failure_into_action()
    test_a_402_becomes_a_credit_action()
    if FAILED:
        print("\n%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("\nPROVIDER PROBE OK - invalid keys and empty balances are actionable, "
          "and secrets stay hidden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
