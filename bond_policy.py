"""Pure Field Agent bond policy helpers (unit-tested; no I/O)."""

from __future__ import annotations


def uplink_kind_is_bondable(kind: str, *, lan_lab: bool) -> bool:
    """Ethernet is LAN for OBS on public ingest; bond it only in offline LAN lab."""
    if kind == "ethernet":
        return lan_lab
    return True


def slash24_equal(a: str, b: str) -> bool:
    """True when both IPv4 strings share the same /24 prefix."""
    ap = str(a or "").split(".")
    bp = str(b or "").split(".")
    if len(ap) != 4 or len(bp) != 4:
        return False
    try:
        return tuple(int(x) for x in ap[:3]) == tuple(int(x) for x in bp[:3])
    except ValueError:
        return False


def path_is_wedged_dead(
    *,
    inflight: int,
    kbps: int,
    peer_ok: bool,
    inflight_min: int = 900,
    kbps_max: int = 25,
) -> bool:
    """Dead path while a peer still carries Live — congestion alone is not enough."""
    if not peer_ok:
        return False
    return int(inflight) >= int(inflight_min) and int(kbps) <= int(kbps_max)
