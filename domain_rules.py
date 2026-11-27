"""The one domain-name rule.

``tool_policy.check_domain`` and ``sites.create_site`` must agree: the UI validates a
domain before saving it, and an agent tool must not be able to store a different kind
of string. Keeping the pattern in a neutral module avoids importing the tool package
(which eagerly imports every tool) from the site service.
"""

import re

# Dot-separated labels of letters/digits/hyphens, at least two labels.
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def normalize_domain(value):
    """Lower-case and trim; the form every comparison and path builder uses."""
    return (value or "").strip().lower()


def valid_domain(domain):
    """True for a plain public hostname usable in a path and a vhost."""
    value = normalize_domain(domain)
    if not DOMAIN_RE.match(value):
        return False
    if ".." in value or "/" in value or value.startswith("-"):
        return False
    return True
