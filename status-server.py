#!/usr/bin/env python3
"""Field Agent kit UI: status, settings, help (no SSH after first save)."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/var/lib/leocastra"))
CONFIG_PATH = CONFIG_DIR / "config.json"
UPLINKS_PATH = Path(os.environ.get("UPLINKS_FILE", str(CONFIG_DIR / "uplinks")))
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
        "latencyMs": int(os.environ.get("LATENCY_MS", "4000") or 4000),
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
    return max(8000, min(60000, latency * 2))


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
        raise ValueError("Bonded port must be 1???65535")
    if not 1 <= cleaned["listenPort"] <= 65535:
        raise ValueError("Listen port must be 1???65535")
    if not 1500 <= cleaned["latencyMs"] <= 8000:
        raise ValueError("Contribution window must be 1500???8000 ms")
    CONFIG_PATH.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
    if cleaned["uplinkMode"] == "auto":
        UPLINKS_PATH.write_text("AUTO\n", encoding="utf-8")
    else:
        if not cleaned["uplinkIps"]:
            raise ValueError("Manual mode needs at least one source IP")
        UPLINKS_PATH.write_text("\n".join(cleaned["uplinkIps"]) + "\n", encoding="utf-8")
    return cleaned


def request_restart() -> None:
    ensure_dirs()
    RESTART_FLAG.write_text(str(time.time()), encoding="utf-8")


def iface_kind(name: str) -> str:
    n = name.lower()
    if n.startswith(("wlan", "wl", "wifi", "wlp")):
        return "wifi"
    if n.startswith(("wwan", "usb", "cdc", "rmnet", "ppp", "qmi", "mbim")):
        return "cellular"
    if n.startswith(("eth", "en", "ens", "enp", "eno")):
        return "ethernet"
    return "other"


def read_uplink_ips() -> list[str]:
    ips: list[str] = []
    try:
        for raw in UPLINKS_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line == "AUTO":
                continue
            ips.append(line)
    except OSError:
        pass
    return ips


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
    rows: list[dict] = []
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return [{"iface": "unknown", "address": ip, "kind": "other", "bonded": True} for ip in bonded]
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        addr = parts[3].split("/")[0]
        if addr.startswith("127."):
            continue
        kind = iface_kind(iface)
        rows.append(
            {
                "iface": iface,
                "address": addr,
                "kind": kind,
                "bonded": auto or addr in bonded,
            }
        )
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
    uplink_order = read_uplink_ips()
    labels = ["Link A", "Link B", "Satellite"]
    by_ip: dict[str, dict] = {}
    for name, rows in metrics.items():
        if not name.startswith("srtla_send_link_"):
            continue
        key = name[len("srtla_send_link_") :]
        for labels_map, value in rows:
            ip = labels_map.get("ip") or labels_map.get("addr") or labels_map.get("link") or ""
            if not ip:
                continue
            slot = by_ip.setdefault(ip, {"ip": ip})
            slot[key] = value
    links = []
    ordered_ips = uplink_order or [n["address"] for n in networks] or list(by_ip.keys())
    extra = [ip for ip in by_ip if ip not in ordered_ips]
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
                "rttMs": round(float(row.get("rtt_ms", 0) or 0)),
                "bitrateKbps": round(bitrate * 8 / 1000),
                "inFlight": int(row.get("in_flight", 0) or 0),
                "naks": int(row.get("nak_total", 0) or 0),
                "quality": round(float(row.get("quality_multiplier", 0) or 0), 2),
            }
        )
    total_bitrate = sum(link["bitrateKbps"] for link in links)
    active = int(gauge(metrics, "srtla_send_active_links"))
    total_links = int(gauge(metrics, "srtla_send_total_links", max(len(links), 1)))
    encoder_receiving = total_bitrate > 8 or int(gauge(metrics, "srtla_send_total_in_flight")) > 0
    ingest = (
        f"{cfg['leocastraHost']}:{cfg['bondedPort']}" if cfg.get("leocastraHost") else ""
    )
    return {
        "listenPort": str(cfg.get("listenPort") or ""),
        "ingest": ingest,
        "studioUrl": cfg.get("studioUrl") or "",
        "hostname": socket.gethostname(),
        "metricsOk": bool(metrics),
        "encoder": {
            "receiving": encoder_receiving,
            "bitrateKbps": total_bitrate,
            "label": "Receiving programme" if encoder_receiving else "Waiting for encoder",
        },
        "bond": {
            "active": active,
            "configured": total_links,
            "mode": "enhanced" if int(gauge(metrics, "srtla_send_mode", 1)) == 1 else "classic",
            "label": f"{active} of {total_links} uplinks up",
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
            request_restart()
            self._json(200, {"ok": True})
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


def watchdog_loop() -> None:
    """If every uplink stays down, bounce srtla_send so REG1 can run again."""
    down_since: float | None = None
    last_restart = 0.0
    while True:
        time.sleep(5)
        snap = snapshot()
        if not snap.get("metricsOk"):
            continue
        active = int((snap.get("bond") or {}).get("active") or 0)
        now = time.time()
        if active > 0:
            down_since = None
            continue
        if down_since is None:
            down_since = now
            continue
        if now - down_since >= 20 and now - last_restart >= 20:
            request_restart()
            last_restart = now
            down_since = now


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
