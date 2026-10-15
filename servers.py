"""
Server records (stage S5): the ``ssh_hosts`` rows the Operator may act on.

The private key never leaves the box — only its path is stored here. Host-key
pinning is enforced at connect time (see ``tools.ssh``).
"""

from db import get_connection, now_str

TEXT_FIELDS = (
    "label", "host", "username", "private_key_path",
    "host_key_fingerprint", "allowed_root", "sites_root",
)


def _as_dict(row):
    return {k: row[k] for k in row.keys()}


def host_out(row, mask=True):
    out = _as_dict(row)
    out["enabled"] = bool(row["enabled"])
    # There is no secret column, but the key *path* is still not useful to the UI.
    out["has_key"] = bool(row["private_key_path"])
    if mask:
        out["private_key_path"] = row["private_key_path"] or ""
    return out


def list_hosts():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM ssh_hosts ORDER BY id ASC").fetchall()
    return [host_out(r) for r in rows]


def get_host(host_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM ssh_hosts WHERE id = ?", (host_id,)).fetchone()
    return host_out(row) if row else None


def create_host(data):
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO ssh_hosts (label, host, port, username, private_key_path, "
            "host_key_fingerprint, allowed_root, sites_root, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (data.get("label") or "").strip() or (data.get("host") or "").strip(),
                (data.get("host") or "").strip(),
                int(data.get("port") or 22),
                (data.get("username") or "").strip(),
                (data.get("private_key_path") or "").strip(),
                (data.get("host_key_fingerprint") or "").strip(),
                (data.get("allowed_root") or "").strip(),
                (data.get("sites_root") or data.get("allowed_root") or "").strip(),
                1 if data.get("enabled", True) else 0,
            ),
        )
        host_id = cur.lastrowid
    return get_host(host_id)


def update_host(host_id, data):
    if not get_host(host_id):
        return None
    sets, values = [], []
    for field in TEXT_FIELDS:
        if field in data:
            value = data.get(field)
            sets.append("%s = ?" % field)
            values.append(value.strip() if isinstance(value, str) else (value or ""))
    if "port" in data:
        sets.append("port = ?")
        values.append(int(data.get("port") or 22))
    if "enabled" in data:
        sets.append("enabled = ?")
        values.append(1 if data.get("enabled") else 0)
    if not sets:
        return get_host(host_id)
    values.append(host_id)
    with get_connection() as conn:
        conn.execute("UPDATE ssh_hosts SET %s WHERE id = ?" % ", ".join(sets), values)
    return get_host(host_id)


def delete_host(host_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM ssh_hosts WHERE id = ?", (host_id,))
    return True
