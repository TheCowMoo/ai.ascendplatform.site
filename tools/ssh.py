"""
SSH transport (stage S5).

Deliberately a *transport only*: it connects and runs a command, and knows
nothing about what is safe. Policy and approval live in ``provisioner``/``actions``
so there is exactly one place to audit.

Host keys are pinned. ``paramiko.RejectPolicy`` refuses unknown hosts outright,
and when a fingerprint is recorded on the host row it must match the presented
key byte-for-byte — so the app cannot be silently redirected to another machine.

The private key never leaves the server; only its path is stored.
"""

import base64
import hashlib
import os

import settings


# Timeouts and caps are read per call so the Settings tab applies without a
# restart. The defaults stay conservative: one hung command should not be able to
# tie up a worker indefinitely.
def connect_timeout():
    return settings.get_int("SSH_TIMEOUT", 15)


def command_timeout():
    return settings.get_int("SSH_CMD_TIMEOUT", 60)


def max_output():
    return settings.get_int("SSH_MAX_OUTPUT", 40000)


class SSHError(Exception):
    """Raised for connection, authentication and host-key failures."""


def _paramiko():
    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - paramiko is a hard dependency
        raise SSHError("paramiko is not installed (%s)" % exc)
    return paramiko


def fingerprint(key):
    """OpenSSH-style fingerprint of a key: 'SHA256:<base64 without padding>'."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _describe(host_row):
    return "%s@%s:%s" % (
        host_row.get("username") or "root",
        host_row.get("host") or "?",
        host_row.get("port") or 22,
    )


def connect(host_row):
    """Open an SSHClient with host-key pinning for a ``ssh_hosts`` row."""
    paramiko = _paramiko()
    host = (host_row.get("host") or "").strip()
    username = (host_row.get("username") or "").strip()
    if not host or not username:
        raise SSHError("host and username are required")

    expected = (host_row.get("host_key_fingerprint") or "").strip()
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    if expected:
        class PinnedFingerprint(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, sshclient, hostname, key):
                actual = fingerprint(key)
                if actual != expected:
                    raise SSHError(
                        "host key mismatch for %s: expected %s, got %s"
                        % (hostname, expected, actual)
                    )
                sshclient.get_host_keys().add(hostname, key.get_name(), key)

        client.set_missing_host_key_policy(PinnedFingerprint())

    options = {
        "hostname": host,
        "port": int(host_row.get("port") or 22),
        "username": username,
        "timeout": connect_timeout(),
        "banner_timeout": connect_timeout(),
        "auth_timeout": connect_timeout(),
        # Key-based auth only: never fall back to agent or ~/.ssh auto-discovery.
        "look_for_keys": False,
        "allow_agent": False,
    }
    key_path = (host_row.get("private_key_path") or "").strip()
    if key_path:
        if not os.path.exists(key_path):
            raise SSHError("private key not found: %s" % key_path)
        options["key_filename"] = key_path

    try:
        client.connect(**options)
    except Exception as exc:
        raise SSHError("could not connect to %s (%s: %s)" % (_describe(host_row), type(exc).__name__, exc))
    return client


def run(host_row, command, timeout=None):
    """Run a command over SSH and return ``{exit_code, stdout, stderr}``."""
    client = connect(host_row)
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout or command_timeout())
        out = stdout.read(max_output()).decode("utf-8", "replace")
        err = stderr.read(max_output()).decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return {"exit_code": code, "stdout": out, "stderr": err}
    finally:
        client.close()


def put_file(host_row, local_path, remote_path, timeout=None):
    """Upload a local file to the remote path (SFTP)."""
    client = connect(host_row)
    try:
        sftp = client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()
        return {"exit_code": 0, "stdout": "uploaded %s -> %s" % (local_path, remote_path), "stderr": ""}
    finally:
        client.close()


def run_script(host_row, script, timeout=None):
    """Run a multi-line script in one session (used for multi-step setup)."""
    return _run_stdin(host_row, script, timeout)


def _run_stdin(host_row, script, timeout=None):
    client = connect(host_row)
    try:
        stdin, stdout, stderr = client.exec_command("bash -s", timeout=timeout or command_timeout())
        stdin.write(script)
        stdin.channel.shutdown_write()
        out = stdout.read(max_output()).decode("utf-8", "replace")
        err = stderr.read(max_output()).decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        return {"exit_code": code, "stdout": out, "stderr": err}
    finally:
        client.close()


def check(host_row):
    """Connectivity probe used by the Servers tab. Read-only by design."""
    result = run(host_row, "whoami && hostname && uname -sr", timeout=connect_timeout())
    return {
        "ok": result["exit_code"] == 0,
        "target": _describe(host_row),
        "output": (result["stdout"] or result["stderr"]).strip()[:500],
    }
