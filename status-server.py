#!/usr/bin/env python3
"""Field Agent kit UI: status, settings, help (no SSH after first save)."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import ssl
import http.client
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/var/lib/leocastra"))
CONFIG_PATH = CONFIG_DIR / "config.json"
UPLINKS_PATH = Path(os.environ.get("UPLINKS_FILE", str(CONFIG_DIR / "uplinks")))
SKIP_PATH = CONFIG_DIR / "uplinks.skip"
SKIP_SECONDS = 180
LOSSY_SKIP_SECONDS = 600
RESTART_FLAG = CONFIG_DIR / "restart.flag"
WEB_ROOT = Path(
    os.environ.get(
        "WEB_ROOT",
        str(Path(__file__).resolve().parent.parent / "share" / "leocastra-field-agent" / "web"),
    )
)
if not (WEB_ROOT / "index.html").exists():
    alt = Path(__file__).resolve().parent.parent.parent / "web"
    if (alt / "index.html").exists():
        WEB_ROOT = alt
    elif (Path("/usr/local/share/leocastra-field-agent/web") / "index.html").exists():
        WEB_ROOT = Path("/usr/local/share/leocastra-field-agent/web")

METRICS_BIND = os.environ.get("METRICS_BIND", "127.0.0.1:9099")
STATUS_PORT = int(os.environ.get("STATUS_PORT", "8088"))

METRIC_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{?(?P<labels>[^}]*)\}?\s+(?P<value>\S+)"
)
LABEL_PAIR = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')
_LAST_PATH_STATS_ERR = 0.0
_LAST_PATH_STATS_OK = False
_LAST_PATH_STATS_DETAIL = "idle"
_PATH_STATS_LOCK = threading.Lock()
FIELD_OB_ID = re.compile(
    r"/field-ob/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    UPLINKS_PATH.parent.mkdir(parents=True, exist_ok=True)


def env_defaults() -> dict:
    return {
        "leocastraHost": os.environ.get("LEOCASTRA_HOST", ""),
        "bondedPort": int(os.environ.get("BONDED_PORT", "10180") or 10180),
        "listenPort": int(os.environ.get("SRT_LISTEN_PORT", "4001") or 4001),
        "studioUrl": os.environ.get("STUDIO_URL", ""),
        "uplinkMode": "auto",
        "uplinkIps": [],
        "schedulerMode": os.environ.get("SRTLA_MODE", "enhanced") or "enhanced",
        "latencyMs": int(os.environ.get("LATENCY_MS", "8000") or 8000),
        "qualityScoring": True,
    }


def load_config() -> dict:
    cfg = env_defaults()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            cfg.update({k: raw[k] for k in raw if k in cfg or k == "uplinkIps"})
    except (OSError, json.JSONDecodeError):
        pass
    if str(cfg.get("uplinkMode", "auto")).lower() != "manual":
        cfg["uplinkMode"] = "auto"
    else:
        cfg["uplinkMode"] = "manual"
    if not isinstance(cfg.get("uplinkIps"), list):
        cfg["uplinkIps"] = []
    cfg["bondedPort"] = int(cfg.get("bondedPort") or 10180)
    cfg["listenPort"] = int(cfg.get("listenPort") or 4001)
    cfg["latencyMs"] = int(cfg.get("latencyMs") or 4000)
    return cfg


def conn_timeout_ms(cfg: dict) -> int:
    latency = max(1500, int(cfg.get("latencyMs") or 4000))
    # srtla_send: silence past this tears a path and re-registers. An outage
    # the SRT buffer can absorb should resume warm (upstream: >= 2x window).
    # Floor 60s so Ghana 8s NAK recovery cannot look like a dead path.
    return max(60000, min(180000, latency * 8))


def srt_loss_max_ttl(latency_ms: int) -> int:
    """Packets to wait after a gap before NAK. Keep in sync with studio field-ob-srt.ts."""
    latency = max(1500, min(8000, int(latency_ms or 4000)))
    return max(80, min(400, round(latency / 20)))


def save_config(cfg: dict) -> dict:
    ensure_dirs()
    cleaned = {
        "leocastraHost": str(cfg.get("leocastraHost") or "").strip(),
        "bondedPort": int(cfg.get("bondedPort") or 0),
        "listenPort": int(cfg.get("listenPort") or 0),
        "studioUrl": str(cfg.get("studioUrl") or "").strip(),
        "uplinkMode": "manual" if cfg.get("uplinkMode") == "manual" else "auto",
        "uplinkIps": [str(x).strip() for x in (cfg.get("uplinkIps") or []) if str(x).strip()],
        "schedulerMode": "classic" if cfg.get("schedulerMode") == "classic" else "enhanced",
        "latencyMs": int(cfg.get("latencyMs") or 4000),
        "qualityScoring": bool(cfg.get("qualityScoring", True)),
    }
    if not cleaned["leocastraHost"]:
        raise ValueError("Ingest host is required")
    if not 1 <= cleaned["bondedPort"] <= 65535:
        raise ValueError("Bonded port must be 1–65535")
    if not 1 <= cleaned["listenPort"] <= 65535:
        raise ValueError("Listen port must be 1–65535")
    if not 1500 <= cleaned["latencyMs"] <= 8000:
        raise ValueError("Contribution window must be 1500–8000 ms")
    CONFIG_PATH.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
    if cleaned["uplinkMode"] == "auto":
        UPLINKS_PATH.write_text("AUTO\n", encoding="utf-8")
    else:
        if not cleaned["uplinkIps"]:
            raise ValueError("Manual mode needs at least one source IP")
        UPLINKS_PATH.write_text("\n".join(cleaned["uplinkIps"]) + "\n", encoding="utf-8")
    return cleaned


def request_restart(*, clear_skips: bool = False) -> None:
    ensure_dirs()
    if clear_skips:
        try:
            SKIP_PATH.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        RESTART_FLAG.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not request reconnect: {exc}") from exc


NOISE_IFACE_PREFIXES = ("docker", "br-", "veth", "cni", "flannel", "virbr")


def iface_kind(name: str) -> str:
    n = name.lower()
    if n.startswith(("wlan", "wl", "wifi", "wlp")):
        return "wifi"
    if n.startswith(("wwan", "usb", "cdc", "rmnet", "ppp", "qmi", "mbim")):
        return "cellular"
    if n.startswith(("eth", "en", "ens", "enp", "eno")):
        return "ethernet"
    return "other"


def is_noise_iface(name: str) -> bool:
    n = (name or "").lower()
    return n == "lo" or n.startswith(NOISE_IFACE_PREFIXES)


def is_docker_bridge_ip(ip: str) -> bool:
    parts = str(ip or "").split(".")
    if len(parts) != 4:
        return False
    try:
        return int(parts[0]) == 172 and int(parts[1]) == 17
    except ValueError:
        return False


def manual_uplink_ips() -> set[str]:
    cfg = load_config()
    if cfg.get("uplinkMode") != "manual":
        return set()
    return {str(ip).strip() for ip in (cfg.get("uplinkIps") or []) if str(ip).strip()}


def is_hidden_bond_address(ip: str, iface: str = "") -> bool:
    """Docker/bridge addresses are never operator uplinks unless Manual pins that IP."""
    if str(ip or "").strip() in manual_uplink_ips():
        return False
    return is_noise_iface(iface) or is_docker_bridge_ip(ip)


def _ips_from(path: Path) -> list[str]:
    ips: list[str] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line == "AUTO":
                continue
            ips.append(line)
    except OSError:
        pass
    return ips


def read_uplink_ips() -> list[str]:
    for path in (
        CONFIG_DIR / "uplinks.routable",
        CONFIG_DIR / "uplinks.resolved",
        UPLINKS_PATH,
    ):
        ips = _ips_from(path)
        if ips:
            return [ip for ip in ips if not is_hidden_bond_address(ip)]
    return []


def list_link_ifaces() -> list[dict]:
    """Non-noise interfaces from `ip -br link`, including those with no IPv4 yet."""
    rows: list[dict] = []
    try:
        out = subprocess.check_output(
            ["ip", "-br", "link"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return rows
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        iface = parts[0].split("@", 1)[0]
        state = parts[1].upper()
        if is_noise_iface(iface):
            continue
        rows.append(
            {
                "iface": iface,
                "address": "",
                "kind": iface_kind(iface),
                "bonded": False,
                "oper": state,
            }
        )
    return rows


def list_networks() -> list[dict]:
    bonded = set(read_uplink_ips())
    auto = False
    try:
        auto = "AUTO" in {
            ln.strip()
            for ln in UPLINKS_PATH.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
    except OSError:
        auto = True
    by_iface: dict[str, list[dict]] = {}
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        out = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        addr = parts[3].split("/")[0]
        if addr.startswith("127.") or is_hidden_bond_address(addr, iface):
            continue
        kind = iface_kind(iface)
        by_iface.setdefault(iface, []).append(
            {
                "iface": iface,
                "address": addr,
                "kind": kind,
                "bonded": addr in bonded if bonded else auto,
            }
        )
    rows: list[dict] = []
    seen = set()
    for link in list_link_ifaces():
        iface = link["iface"]
        seen.add(iface)
        addrs = by_iface.get(iface) or []
        if addrs:
            rows.extend(addrs)
        else:
            rows.append(link)
    for iface, addrs in by_iface.items():
        if iface not in seen:
            rows.extend(addrs)
    return rows


def scrape_metrics() -> dict[str, list[tuple[dict[str, str], float]]]:
    url = f"http://{METRICS_BIND}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            text = resp.read().decode("utf-8", "replace")
    except OSError:
        return {}
    parsed: dict[str, list[tuple[dict[str, str], float]]] = {}
    for raw in text.splitlines():
        if not raw or raw.startswith("#"):
            continue
        match = METRIC_LINE.match(raw)
        if not match:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        labels = dict(LABEL_PAIR.findall(match.group("labels") or ""))
        parsed.setdefault(match.group("name"), []).append((labels, value))
    return parsed


def gauge(metrics: dict, name: str, default: float = 0.0) -> float:
    rows = metrics.get(name) or []
    if not rows:
        return default
    return rows[0][1]


def kind_for_ip(ip: str, networks: list[dict]) -> str:
    for row in networks:
        if row.get("address") == ip:
            return str(row.get("kind") or "other")
    return "other"


def snapshot() -> dict:
    cfg = load_config()
    metrics = scrape_metrics()
    networks = list_networks()
    iface_by_ip = {str(n.get("address") or ""): str(n.get("iface") or "") for n in networks}
    live_addrs = {str(n.get("address") or "") for n in networks if n.get("address")}
    uplink_order = [
        ip
        for ip in read_uplink_ips()
        if ip in live_addrs and not is_hidden_bond_address(ip, iface_by_ip.get(ip, ""))
    ]
    labels = ["Link A", "Link B", "Satellite"]
    by_ip: dict[str, dict] = {}
    for name, rows in metrics.items():
        if not name.startswith("srtla_send_link_"):
            continue
        key = name[len("srtla_send_link_") :]
        for labels_map, value in rows:
            ip = labels_map.get("ip") or labels_map.get("addr") or labels_map.get("link") or ""
            if not ip or is_hidden_bond_address(ip, iface_by_ip.get(ip, "")):
                continue
            slot = by_ip.setdefault(ip, {"ip": ip})
            slot[key] = value
    links = []
    ordered_ips = list(uplink_order)
    if not ordered_ips:
        ordered_ips = [
            ip
            for ip in by_ip
            if ip in live_addrs
            and not is_hidden_bond_address(ip, iface_by_ip.get(ip, ""))
        ]
    extra = [
        ip
        for ip in by_ip
        if ip not in ordered_ips
        and ip in live_addrs
        and not is_hidden_bond_address(ip, iface_by_ip.get(ip, ""))
    ]
    for index, ip in enumerate(ordered_ips + extra):
        row = by_ip.get(ip, {"ip": ip})
        up = bool(row.get("up", 0))
        bitrate = float(row.get("bitrate_bytes_per_second", 0) or 0)
        links.append(
            {
                "label": labels[index] if index < len(labels) else f"Link {index + 1}",
                "ip": ip,
                "kind": kind_for_ip(ip, networks),
                "up": up,
                "state": "up" if up else ("waiting" if not metrics else "down"),
                "rttMs": round(float(row.get("rtt_ms", 0) or 0)),
                "bitrateKbps": round(bitrate * 8 / 1000),
                "inFlight": int(row.get("in_flight", 0) or 0),
                "naks": int(row.get("nak_total", 0) or 0),
                "quality": round(float(row.get("quality_multiplier", 0) or 0), 2),
            }
        )
    total_bitrate = sum(link["bitrateKbps"] for link in links)
    active = sum(1 for link in links if link.get("up"))
    total_links = len(links)
    in_flight = int(gauge(metrics, "srtla_send_total_in_flight")) or sum(
        int(link.get("inFlight") or 0) for link in links
    )
    # Bond keepalives leave in_flight > 0 with 0 kbps. That is not an encoder.
    encoder_receiving = total_bitrate > 8
    ingest = (
        f"{cfg['leocastraHost']}:{cfg['bondedPort']}" if cfg.get("leocastraHost") else ""
    )
    up_links = [link for link in links if link.get("up")]
    rtt_values = [int(link.get("rttMs") or 0) for link in up_links]
    naks_total = sum(int(link.get("naks") or 0) for link in links)
    lan_ip = next(
        (
            n.get("address") or ""
            for n in networks
            if n.get("kind") == "ethernet" and n.get("address")
        ),
        next(
            (
                n.get("address") or ""
                for n in networks
                if str(n.get("address") or "").startswith("192.168.0.")
            ),
            next((n.get("address") or "" for n in networks if n.get("address")), ""),
        ),
    )
    mode = "enhanced" if int(gauge(metrics, "srtla_send_mode", 1)) == 1 else "classic"
    return {
        "listenPort": str(cfg.get("listenPort") or ""),
        "ingest": ingest,
        "studioUrl": cfg.get("studioUrl") or "",
        "hostname": socket.gethostname(),
        "lanIp": lan_ip,
        "windowMs": int(cfg.get("latencyMs") or 4000),
        "lossMaxTtl": srt_loss_max_ttl(int(cfg.get("latencyMs") or 4000)),
        "oheadBw": 50,
        "metricsOk": bool(metrics),
        "encoder": {
            "receiving": encoder_receiving,
            "bitrateKbps": total_bitrate,
            "label": "Live" if encoder_receiving else "No Feed",
        },
        "bond": {
            "active": active,
            "configured": total_links,
            "mode": mode,
            "label": f"{active}/{total_links}" if metrics else "Restarting",
        },
        "path": {
            "rttMs": max(rtt_values) if rtt_values else None,
            "inFlight": in_flight,
            "naks": naks_total,
        },
        "studioSync": {
            "ok": _LAST_PATH_STATS_OK,
            "detail": _LAST_PATH_STATS_DETAIL,
        },
        "links": links,
        "networks": networks,
        "connTimeoutMs": conn_timeout_ms(cfg),
    }


def wifi_scan(iface: str) -> dict:
    if not iface:
        ifaces = [n["iface"] for n in list_networks() if n.get("kind") == "wifi"]
        iface = ifaces[0] if ifaces else ""
    if not iface:
        return {"ssids": [], "error": "No Wi-Fi interface on this kit. Use host networking on Linux."}
    ssids: list[dict] = []
    try:
        out = subprocess.check_output(
            ["iw", "dev", iface, "scan"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=12,
        )
        current = ""
        signal = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("SSID:"):
                current = line.split(":", 1)[1].strip()
                if current:
                    ssids.append({"ssid": current, "signal": signal})
            if "signal:" in line:
                signal = line.split("signal:", 1)[-1].strip()
    except FileNotFoundError:
        return {"ssids": [], "error": "iw is not installed on this image"}
    except subprocess.CalledProcessError as exc:
        return {"ssids": [], "error": exc.output[-400:] if exc.output else "Scan failed (needs host networking / NET_ADMIN)"}
    except subprocess.TimeoutExpired:
        return {"ssids": [], "error": "Scan timed out"}
    # unique preserve order
    seen = set()
    uniq = []
    for row in ssids:
        if row["ssid"] in seen:
            continue
        seen.add(row["ssid"])
        uniq.append(row)
    return {"ssids": uniq}


def wifi_connect(iface: str, ssid: str, psk: str) -> None:
    if not iface or not ssid:
        raise ValueError("Wi-Fi interface and SSID are required")
    env = os.environ.copy()
    try:
        subprocess.check_output(
            ["nmcli", "device", "wifi", "connect", ssid, "password", psk, "ifname", iface],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=30,
            env=env,
        )
        return
    except FileNotFoundError:
        pass
    except subprocess.CalledProcessError as exc:
        raise ValueError(exc.output[-400:] if exc.output else "nmcli connect failed") from exc
    raise ValueError(
        "NetworkManager (nmcli) is not on this kit. On Ubuntu Server, connect Wi-Fi on the host, then this Agent will use wlan0 as an uplink."
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send(self, code: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 65536:
            return {}
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON object required")
        return data

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            self._json(200, snapshot())
            return
        if path == "/api/config":
            self._json(200, load_config())
            return
        if path == "/api/wifi/scan":
            qs = urllib.parse.parse_qs(parsed.query)
            iface = (qs.get("iface") or [""])[0]
            self._json(200, wifi_scan(iface))
            return
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            target = (WEB_ROOT / rel).resolve()
            if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
                self._json(404, {"error": "not found"})
                return
            data = target.read_bytes()
            self._send(200, data, MIME.get(target.suffix, "application/octet-stream"))
            return
        index = WEB_ROOT / "index.html"
        if index.is_file():
            self._send(200, index.read_bytes(), "text/html; charset=utf-8")
            return
        self._json(500, {"error": "Web UI missing"})

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/config":
            self._json(404, {"error": "not found"})
            return
        try:
            saved = save_config(self._read_json())
            self._json(200, saved)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/restart":
            try:
                request_restart(clear_skips=True)
                self._json(200, {"ok": True})
            except ValueError as exc:
                self._json(500, {"error": str(exc)})
            return
        if parsed.path == "/api/wifi/connect":
            try:
                body = self._read_json()
                wifi_connect(
                    str(body.get("iface") or ""),
                    str(body.get("ssid") or ""),
                    str(body.get("psk") or ""),
                )
                self._json(200, {"ok": True})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        self._json(404, {"error": "not found"})


def auto_candidate_ips() -> list[str]:
    """One global IPv4 per real iface — same AUTO rule as the bond sender.

    Cellular/Wi-Fi first so a LAN NIC that cannot reach the internet does not
    stall the watchdog (each failed ping is a 2s wait).
    """
    seen_iface: set[str] = set()
    ranked: list[tuple[int, str]] = []
    for row in list_networks():
        iface = str(row.get("iface") or "")
        addr = str(row.get("address") or "").strip()
        if not addr or iface in seen_iface:
            continue
        if is_hidden_bond_address(addr, iface):
            continue
        seen_iface.add(iface)
        kind = str(row.get("kind") or "other")
        rank = 0 if kind in ("cellular", "wifi") else 1
        ranked.append((rank, addr))
    ranked.sort(key=lambda item: item[0])
    return [addr for _, addr in ranked]


def iface_for_ip(ip: str) -> str:
    for row in list_networks():
        if str(row.get("address") or "") == ip:
            return str(row.get("iface") or "")
    return ""


def ping_internet(ip: str) -> bool:
    """Probe via the NIC, not only the source IP.

    Dual-default tethers (usb0+usb1) send `ping -I <usb1-ip>` out usb0, so a
    phone with internet still looks unroutable and never joins the bond.
    """
    iface = iface_for_ip(ip)
    ident = iface or ip
    try:
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "-I", ident, "1.1.1.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
        )
        if proc.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if iface:
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET,
                    getattr(socket, "SO_BINDTODEVICE", 25),
                    (iface + "\0").encode(),
                )
            except OSError:
                sock.bind((ip, 0))
        else:
            sock.bind((ip, 0))
        sock.settimeout(3)
        sock.connect(("1.1.1.1", 443))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def bring_tether_ifaces_up() -> None:
    """RNDIS/CDC gadgets often appear DOWN until something sets the admin flag."""
    for link in list_link_ifaces():
        iface = str(link.get("iface") or "")
        if not iface.startswith(("usb", "enx", "wwan", "wlan")):
            continue
        if str(link.get("oper") or "").upper() not in ("DOWN", "LOWERLAYERDOWN"):
            continue
        try:
            subprocess.run(
                ["ip", "link", "set", iface, "up"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def live_addresses() -> set[str]:
    return {str(n.get("address") or "") for n in list_networks() if n.get("address")}


def read_skips() -> dict[str, float]:
    kept: dict[str, float] = {}
    try:
        for raw in SKIP_PATH.read_text(encoding="utf-8").splitlines():
            parts = raw.split()
            if not parts:
                continue
            ip = parts[0].strip()
            exp = float(parts[1]) if len(parts) > 1 else time.time() + SKIP_SECONDS
            if ip and exp > time.time():
                kept[ip] = exp
    except (OSError, ValueError):
        pass
    return kept


def write_skips(skips: dict[str, float]) -> None:
    ensure_dirs()
    now = time.time()
    lines = [
        f"{ip} {exp:.0f}\n"
        for ip, exp in skips.items()
        if ip and exp > now
    ]
    SKIP_PATH.write_text("".join(lines), encoding="utf-8")


def skip_ip_set() -> set[str]:
    return set(read_skips())


def remember_skip(ip: str, seconds: int | None = None, reason: str = "") -> None:
    ip = str(ip or "").strip()
    if not ip:
        return
    seconds = int(seconds or SKIP_SECONDS)
    skips = read_skips()
    skips[ip] = time.time() + seconds
    write_skips(skips)
    why = reason or "SRTLA stayed down on that path"
    print(f"Skipping {ip} for {seconds}s — {why}", flush=True)


def unique_ips(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ip in seq:
        if ip and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def sender_pid() -> int | None:
    try:
        pid = int((CONFIG_DIR / "srtla.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 1:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def write_routable(ips: list[str]) -> None:
    ensure_dirs()
    (CONFIG_DIR / "uplinks.routable").write_text(
        "".join(f"{ip}\n" for ip in ips),
        encoding="utf-8",
    )


def sighup_sender() -> bool:
    """Reload BIND_IPS_FILE in-process. Encoder SRT listen port stays up."""
    pid = sender_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGHUP)
        return True
    except OSError as exc:
        print(f"SIGHUP srtla_send failed: {exc}", flush=True)
        return False


def gateway_for_iface(iface: str) -> str:
    try:
        out = subprocess.check_output(
            ["ip", "-4", "route", "show", "default", "dev", iface],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        out = ""
    parts = out.split()
    if "via" in parts:
        idx = parts.index("via")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


def set_rp_filter_loose(iface: str = "") -> None:
    paths = ["/proc/sys/net/ipv4/conf/all/rp_filter"]
    if iface:
        paths.append(f"/proc/sys/net/ipv4/conf/{iface}/rp_filter")
    for path in paths:
        try:
            Path(path).write_text("2\n", encoding="ascii")
        except OSError:
            pass


POLICY_TABLES = range(110, 120)
POLICY_FROM = re.compile(r"from\s+(\d+\.\d+\.\d+\.\d+)\s+lookup\s+(\d+)")
ADD_STABLE_S = 8.0
DROP_GONE_S = 2.0
SIGHUP_COOLDOWN_S = 8.0


def _ip(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["ip", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(["ip", *args], 1)


def list_policy_rules() -> list[tuple[str, int]]:
    """Bond policy rules: (from_ip, table) for tables 110–119."""
    try:
        out = subprocess.check_output(
            ["ip", "rule", "list"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    rows: list[tuple[str, int]] = []
    for match in POLICY_FROM.finditer(out):
        table = int(match.group(2))
        if table in POLICY_TABLES:
            rows.append((match.group(1), table))
    return rows


def drop_policy_for_ip(ip: str) -> None:
    for _ in range(8):
        if _ip("rule", "del", "from", ip).returncode != 0:
            break


def ensure_source_route(ip: str) -> None:
    """Add a from-IP policy route so a newly plugged tether is not sent via another NIC."""
    iface = iface_for_ip(ip)
    if not iface:
        return
    rules = list_policy_rules()
    if any(from_ip == ip for from_ip, _ in rules):
        set_rp_filter_loose(iface)
        return
    used = {table for _, table in rules}
    table = next((n for n in POLICY_TABLES if n not in used), 119)
    gw = gateway_for_iface(iface)
    set_rp_filter_loose(iface)
    try:
        if gw:
            _ip("route", "replace", "default", "via", gw, "dev", iface, "table", str(table))
        else:
            _ip("route", "replace", "default", "dev", iface, "table", str(table))
        _ip("rule", "add", "from", ip, "lookup", str(table), "pref", str(table))
    except OSError:
        pass


def reconcile_source_routes(desired: list[str]) -> None:
    """One policy route per live bond IP. Drop leftovers from unplug/DHCP so tables 110–119 do not fill."""
    wanted = set(desired)
    stale_tables: set[int] = set()
    for from_ip, table in list_policy_rules():
        if from_ip not in wanted:
            drop_policy_for_ip(from_ip)
            stale_tables.add(table)
    for table in stale_tables:
        _ip("route", "flush", "table", str(table))
    for ip in desired:
        ensure_source_route(ip)


def is_bondable_ip(ip: str, networks: list[dict]) -> bool:
    return bool(ip) and kind_for_ip(ip, networks) != "ethernet"


def _https_post_ipv4(
    url: str,
    body: bytes,
    timeout: float = 15,
    source_ip: str | None = None,
) -> int:
    """POST JSON over IPv4. Bind to a bonded uplink so studio HTTPS does not
    take the LAN default route (that path times out on this kit)."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise OSError("missing host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    source = (source_ip, 0) if source_ip else None
    sock = socket.create_connection(addr, timeout, source)
    try:
        if parsed.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.sock = sock
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Host": host if port in (80, 443) else f"{host}:{port}",
                "Content-Length": str(len(body)),
            },
        )
        resp = conn.getresponse()
        resp.read()
        status = int(resp.status)
        conn.close()
        return status
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def path_stats_source_ips(snap: dict) -> list[str]:
    """Prefer cellular/Wi-Fi. LAN ethernet to studio HTTPS times out on this kit."""
    ips: list[str] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        value = str(ip or "").strip()
        if value and value not in seen:
            seen.add(value)
            ips.append(value)

    for link in snap.get("links") or []:
        kind = str(link.get("kind") or "")
        if link.get("up") and link.get("ip") and kind in ("cellular", "wifi"):
            add(str(link.get("ip")))
    for net in snap.get("networks") or []:
        kind = str(net.get("kind") or "")
        if net.get("address") and kind in ("cellular", "wifi"):
            add(str(net.get("address")))
    for link in snap.get("links") or []:
        kind = str(link.get("kind") or "")
        if link.get("up") and link.get("ip") and kind != "ethernet":
            add(str(link.get("ip")))
    for net in snap.get("networks") or []:
        if net.get("address") and str(net.get("kind") or "") != "ethernet":
            add(str(net.get("address")))
    return ips


def post_path_stats(snap: dict) -> None:
    """Push bond RTT/NAKs to the studio Field OB page (localhost FFmpeg has no path RTT)."""
    global _LAST_PATH_STATS_ERR, _LAST_PATH_STATS_OK, _LAST_PATH_STATS_DETAIL
    cfg = load_config()
    studio = str(cfg.get("studioUrl") or "").strip()
    match = FIELD_OB_ID.search(studio)
    if not match:
        _LAST_PATH_STATS_OK = False
        _LAST_PATH_STATS_DETAIL = "no studio URL"
        return
    parsed = urllib.parse.urlparse(studio)
    if not parsed.scheme or not parsed.netloc:
        _LAST_PATH_STATS_OK = False
        _LAST_PATH_STATS_DETAIL = "bad studio URL"
        return
    ingest = str(snap.get("ingest") or "")
    port = int(cfg.get("bondedPort") or 0)
    if ":" in ingest:
        try:
            port = int(ingest.rsplit(":", 1)[-1])
        except ValueError:
            pass
    if port < 1:
        _LAST_PATH_STATS_OK = False
        _LAST_PATH_STATS_DETAIL = "no bonded port"
        return
    path = snap.get("path") or {}
    enc = snap.get("encoder") or {}
    body = json.dumps(
        {
            "bondedPort": port,
            "rttMs": int(path.get("rttMs") or 0),
            "naks": int(path.get("naks") or 0),
            "bitrateKbps": int(enc.get("bitrateKbps") or 0),
            "windowMs": int(cfg.get("latencyMs") or 0),
        }
    ).encode("utf-8")
    url = f"{parsed.scheme}://{parsed.netloc}/api/v1/field-ob/{match.group(1)}/path-stats"
    sources = path_stats_source_ips(snap)
    last_error = "no uplink"
    for source_ip in sources or [None]:
        try:
            status = _https_post_ipv4(url, body, source_ip=source_ip or None)
            if status >= 400:
                last_error = f"HTTP {status} via {source_ip or 'default'}"
                continue
            _LAST_PATH_STATS_OK = True
            _LAST_PATH_STATS_DETAIL = f"ok via {source_ip or 'default'}"
            return
        except Exception as exc:
            last_error = f"{exc} via {source_ip or 'default'}"
    _LAST_PATH_STATS_OK = False
    _LAST_PATH_STATS_DETAIL = last_error
    now = time.time()
    if now - _LAST_PATH_STATS_ERR >= 30:
        print(f"path-stats post failed: {last_error}", flush=True)
        _LAST_PATH_STATS_ERR = now


def post_path_stats_bg(snap: dict) -> None:
    if not _PATH_STATS_LOCK.acquire(blocking=False):
        return
    def run() -> None:
        try:
            post_path_stats(snap)
        finally:
            _PATH_STATS_LOCK.release()
    threading.Thread(target=run, daemon=True, name="path-stats").start()


def watchdog_loop() -> None:
    """Reconcile uplinks onto the live sender. Topology never restarts srtla_send.

    Unplug / carrier down: drop the vanished IP after a short debounce so a dead
    bind (typical on usb0 DHCP) cannot stall packet forwarding on the remaining
    path. Replug / new DHCP: wait until the address is stable, then SIGHUP add.
    Remaining Up IPs stay in the bind file on every reload.
    """
    pending_add: dict[str, float] = {}
    gone_since: dict[str, float] = {}
    last_sighup = 0.0
    last_path_post = 0.0
    while True:
        time.sleep(2)
        try:
            bring_tether_ifaces_up()
            cfg = load_config()
            now = time.time()
            snap = snapshot()
            if now - last_path_post >= 4:
                post_path_stats_bg(snap)
                last_path_post = now
            if str(cfg.get("uplinkMode") or "auto") == "manual":
                continue
            networks = snap.get("networks") or list_networks()
            live = {
                str(n.get("address") or "")
                for n in networks
                if n.get("address")
            }
            current = [
                ip for ip in read_uplink_ips() if is_bondable_ip(ip, networks) or ip not in live
            ]
            current = unique_ips(current)

            for ip in list(gone_since):
                if ip in live:
                    gone_since.pop(ip, None)
            for ip in current:
                if ip not in live:
                    gone_since.setdefault(ip, now)

            keep: list[str] = []
            for link in snap.get("links") or []:
                ip = str(link.get("ip") or "")
                if link.get("up") and ip in live and is_bondable_ip(ip, networks):
                    keep.append(ip)
            for ip in current:
                if ip in live and is_bondable_ip(ip, networks):
                    keep.append(ip)
                    continue
                missing_for = now - gone_since.get(ip, now)
                if ip not in live and missing_for < DROP_GONE_S:
                    keep.append(ip)
            keep = unique_ips(keep)

            added: list[str] = []
            seen_candidates: set[str] = set()
            for ip in auto_candidate_ips():
                seen_candidates.add(ip)
                if ip in keep or not is_bondable_ip(ip, networks):
                    pending_add.pop(ip, None)
                    continue
                if not ping_internet(ip):
                    pending_add.pop(ip, None)
                    continue
                pending_add.setdefault(ip, now)
                if now - pending_add[ip] >= ADD_STABLE_S:
                    added.append(ip)
            for stale in list(pending_add):
                if stale not in seen_candidates:
                    pending_add.pop(stale, None)

            desired = unique_ips(
                [ip for ip in current if ip in keep] + [ip for ip in keep if ip not in current] + added
            )
            if not desired:
                desired = [ip for ip in current if ip in live] or current

            reconcile_source_routes([ip for ip in desired if ip in live])

            if (
                set(desired) != set(current)
                and desired
                and now - last_sighup >= SIGHUP_COOLDOWN_S
                and sender_pid() is not None
            ):
                dropped = [ip for ip in current if ip not in desired]
                joined = [ip for ip in desired if ip not in current]
                encoder_live = int((snap.get("encoder") or {}).get("bitrateKbps") or 0) > 8
                in_flight = int((snap.get("path") or {}).get("inFlight") or 0)
                if joined and not dropped and (encoder_live or in_flight > 0):
                    continue
                write_routable(desired)
                if sighup_sender():
                    last_sighup = now
                    print(
                        "Bond reconcile (sender kept): "
                        f"drop [{' '.join(dropped) or '-'}] add [{' '.join(joined) or '-'}] "
                        f"-> {' '.join(desired)}",
                        flush=True,
                    )
                else:
                    print("Bond reconcile skipped: srtla_send pid missing", flush=True)
        except Exception as extra:
            print(f"watchdog error: {extra}", flush=True)


def main() -> None:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        try:
            save_config(env_defaults())
        except ValueError:
            pass
    import threading

    threading.Thread(target=watchdog_loop, name="bond-watchdog", daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", STATUS_PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
