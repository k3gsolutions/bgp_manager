# Adaptado de netops_netbox_sync/app/parsers/vrfs.py

from __future__ import annotations

import re


def parse_vrfs(output: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Total") or line.startswith("VPN-Instance Name"):
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 1:
            name = parts[0].strip()
            if not name:
                continue
            rd = parts[1].strip() if len(parts) >= 2 else None
            if rd and not re.match(r"^\d+:\d+$", rd):
                rd = None
            if name not in seen:
                seen[name] = {"name": name, "rd": rd or None}
    return list(seen.values())
