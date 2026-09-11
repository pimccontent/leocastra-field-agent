# LeoCastra Field Agent

Headless contribution kit for live field-to-studio transmission.

The encoder on this machine (or on the same LAN) sends SRT to the Agent. The Agent bonds Ethernet, Wi-Fi, and cellular uplinks and forwards a single resilient path to studio ingest. It starts when the mini server powers on. There is no monitor and no SSH after the first save.

Open the kit from a phone or laptop on the same network:

**`http://<kit-ip>:8088`**

Status · Settings · Help

---

## What you need

- Ubuntu Server on a mini PC (no desktop)
- One or more uplinks: Ethernet, Wi-Fi, USB LTE, or a mix
- An encoder that can publish **SRT caller**
- The **ingest host** and **bonded port** from your studio contribution channel

Docker is the supported install. Host networking is required so each uplink has its own address.

---

## One-command install (Ubuntu mini server)

On a fresh Ubuntu Server box (no monitor). This installs Docker if needed, starts the Agent on boot, and uses host networking so Ethernet, Wi-Fi, and LTE can each bind.

```bash
curl -fsSL https://raw.githubusercontent.com/pimccontent/leocastra-field-agent/main/install.sh | sudo bash
```

From a cloned folder:

```bash
sudo ./install.sh
```

Then open **`http://<kit-ip>:8088`** from a phone or laptop on the same LAN. Save ingest host and bonded port. You will not need SSH again.

`install-kit.sh` is the same installer (wrapper).

Manual compose (same result, Docker already installed):

```bash
cp env.example .env
docker compose -f docker-compose.yml -f docker-compose.kit.yml up -d --build
```

### Docker Desktop (lab only)

Windows and macOS cannot expose host Wi-Fi or LTE as separate uplinks. Use published ports and point ingest at `host.docker.internal` only when studio runs on the same PC.

```bash
cp env.example .env
docker compose up -d --build
```

Then open `http://localhost:8088`.

---

## First setup (Web UI)

1. Connect a laptop or phone to the same LAN as the kit.
2. Open **Settings**.
3. Enter **Ingest host** and **Bonded port** from the studio contribution channel.
4. Match **Contribution window** to that channel.
5. Leave **Uplink mode** on Auto unless you must pin source IPs.
6. **Save and reconnect**.
7. In studio, start listening on the contribution channel.
8. Point the encoder at this kit: `srt://127.0.0.1:4001` with **caller** mode and the same latency as the window.

If the encoder runs on another PC, use the kit LAN IP instead of `127.0.0.1`.

---

## Networks

| Kind | Role |
|------|------|
| Ethernet | Wired uplink |
| Wi-Fi | Normal bonded uplink on a Linux kit |
| Cellular | USB LTE / modem address |

Auto mode bonds every global IPv4 address. Connect Wi-Fi from **Settings** when the kit has a wireless radio. Two SIMs should be different radio cores.

The bond is **UDP** to the ingest host. An HTTPS reverse proxy or CDN in front of the studio website cannot carry this path. The ingest hostname must resolve to the machine that accepts the bonded UDP port, and that port must be open on the firewall.

---

## Power-on

The Agent is meant to come up with the mini server:

- Docker kits: `restart: always` and Docker enabled on boot (`install.sh`)
- Bare metal: `systemctl enable --now leocastra-field-agent`

Leave the kit running. Do not send the encoder past the Agent to studio while the Agent is in the path.

---

## Link Down / 0 of N up

1. Studio contribution must be **listening**. After a studio restart, start listening again.
2. Confirm ingest host and bonded port in Settings.
3. Use **Reconnect now**. The kit also reconnects on its own if every uplink stays down.
4. Two-path bonding needs a Linux kit with host networking. Docker Desktop shows one NAT path.

Full operator steps live on the kit **Help** page.

---

## Without Docker

Build `srtla_send`, place it on `PATH` or at `/usr/local/bin/srtla_send`, then run `./install.sh` (systemd) or `./leocastra-field-agent.sh`.

---

## Brand

LeoCastra Field Agent is a field contribution appliance. Configure it from the Web UI. Runtime state stays on the kit under `/var/lib/leocastra` and is not committed to git.
