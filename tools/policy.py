"""
Command and path policy for every action tool.

Nothing reaches a shell or a filesystem until it has passed through here. The
defaults are **deny-by-default**: a command must be classifiable (known
read-only or known mutating) and must not match any deny pattern, and a path must
resolve inside the configured root.

This module is intentionally pure — no database, no network — so it can be
unit-tested exhaustively and reasoned about in isolation.
"""

import os
import re
import shlex
from collections import namedtuple

Decision = namedtuple("Decision", "allowed reason kind")

# Commands that only observe state. Safe to run without any approval.
READ_ONLY_COMMANDS = {
    "ls", "cat", "pwd", "whoami", "date", "df", "du", "head", "tail", "wc",
    "grep", "egrep", "find", "stat", "file", "uname", "hostname", "id", "which",
    "dig", "nslookup", "host", "getent", "tree", "test", "true", "false",
    "echo", "basename", "dirname", "realpath", "sort", "uniq", "cut", "tr",
}

# Commands allowed to change things, but only inside the site root and only
# after an approval (unless the whole action is in dry-run mode).
MUTATING_COMMANDS = {
    "mkdir", "cp", "mv", "touch", "tee", "ln", "rsync", "tar", "unzip", "zip",
    "chmod", "chown", "install", "truncate", "rmdir", "rm",
    "git", "systemctl", "service", "nginx", "caddy", "certbot", "docker",
}

# Substrings that are never allowed, whatever the command.
DENY_SUBSTRINGS = (
    ":(){", ":(){:|:&};:",
    "mkfs", "dd if=", "dd of=/dev", "> /dev/sd", "wipefs",
    "/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/ssh/",
    "> /etc/", ">> /etc/", "/etc/cron", "crontab -r",
    "shutdown", "reboot", "poweroff", "halt", "init 0", "init 6",
    "useradd", "usermod", "userdel", "groupadd", "passwd", "visudo", "su -",
    "chmod 777", "chmod -r 777", "chown -r /", "iptables -f", "nft flush",
    "history -c", "kill -9 1", "curl | sh", "curl|sh", "wget | sh", "wget|sh",
    "| bash", "| sh", "| sudo",
)

# Path-sensitive rules. Blocking these as bare substrings would also block
# legitimate work such as `rm -rf /srv/sites/example.com/old`, so they are
# matched precisely against the filesystem root and top-level system dirs.
DENY_PATTERNS = (
    re.compile(r"\brm\s+-\w*r\w*f\w*\s+/\s*($|[;&|])"),  # rm -rf /
    re.compile(r"\brm\s+[^|;&]*?\s+/\*"),  # rm ... /*
    re.compile(
        r"\brm\s+-\w*r\w*f\w*\s+/(etc|usr|var|bin|sbin|boot|lib|root|home|sys|proc|dev)\b"
    ),
    re.compile(r"\bdd\s+[^|;&]*of=/dev/"),
    re.compile(r">\s*/dev/sd[a-z]"),
)

# Command substitution and friends are an unbounded escape hatch.
_SUBSTITUTION = re.compile(r"\$\(|`|\$\{|<\(")

# Shell metacharacters used to chain commands, so each part can be inspected.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n|&")

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _segments(command):
    return [part.strip() for part in _SEGMENT_SPLIT.split(command) if part.strip()]


def _executable(segment):
    """First real token of a segment, skipping env assignments."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    for token in tokens:
        if token == "sudo":
            return "sudo"
        if token in ("cd", "env", "nohup", "time"):
            continue
        if _ENV_ASSIGNMENT.match(token):
            continue
        return token
    return None


def classify(executable):
    """'read', 'mutating' or 'deny' for a bare command name."""
    if not executable:
        return "deny"
    name = os.path.basename(executable)
    if name in READ_ONLY_COMMANDS:
        return "read"
    if name in MUTATING_COMMANDS:
        return "mutating"
    return "deny"


def check_command(command, allow_mutating=True):
    """Decide whether a shell command may run.

    Returns a ``Decision``; ``kind`` is ``read``, ``mutating`` or ``deny``.
    """
    text = (command or "").strip()
    if not text:
        return Decision(False, "empty command", "deny")

    lowered = text.lower()
    for bad in DENY_SUBSTRINGS:
        if bad in lowered:
            return Decision(False, "blocked pattern: %r" % bad, "deny")

    for pattern in DENY_PATTERNS:
        if pattern.search(lowered):
            return Decision(False, "blocked pattern: %s" % pattern.pattern, "deny")

    if _SUBSTITUTION.search(text):
        return Decision(False, "command substitution is not allowed", "deny")

    kinds = set()
    for segment in _segments(text):
        executable = _executable(segment)
        if executable is None:
            continue
        kind = classify(executable)
        if kind == "deny":
            return Decision(False, "command not allowed: %s" % executable, "deny")
        kinds.add(kind)

    if not kinds:
        return Decision(False, "command could not be classified", "deny")
    if "mutating" in kinds and not allow_mutating:
        return Decision(False, "mutating commands are not permitted here", "deny")

    return Decision(True, "ok", "mutating" if "mutating" in kinds else "read")


def check_path(path, allowed_root):
    """Decide whether a path may be written to.

    The path must resolve inside ``allowed_root``. Parent traversal is refused
    even when it would stay inside the root, because it makes auditing harder.
    """
    if not allowed_root:
        return Decision(False, "no allowed root configured", "deny")
    if not path:
        return Decision(False, "empty path", "deny")

    raw = str(path).replace("\\", "/")
    if ".." in raw.split("/"):
        return Decision(False, "parent traversal is not allowed", "deny")

    root = os.path.abspath(allowed_root)
    target = os.path.abspath(os.path.normpath(str(path)))
    if target != root and not target.startswith(root + os.sep):
        return Decision(False, "path escapes the allowed root", "deny")
    return Decision(True, "ok", "write")


def is_read_only(command):
    return check_command(command).kind == "read"


def quote(value):
    """Shell-quote a single argument."""
    return shlex.quote(str(value))


# A hostname: dot-separated labels of letters/digits/hyphens.
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def check_domain(domain):
    """Validate a domain name; also used to build file paths safely."""
    value = (domain or "").strip().lower()
    if not DOMAIN_RE.match(value):
        return Decision(False, "not a valid domain name", "deny")
    if ".." in value or "/" in value or value.startswith("-"):
        return Decision(False, "invalid characters in domain", "deny")
    return Decision(True, "ok", "domain")

