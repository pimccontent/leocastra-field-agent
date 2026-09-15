# Ubuntu Field Agent kit — implementation guide

This is the supported way to run LeoCastra Field Agent on a mini PC. It is written from the live Windows Docker lab (which **did** bond) and from the Ubuntu kit failures that followed (no LAN IP, DNS, `docker0` in the bond, `usb0` looking Down while ping worked, Windows agent holding the live bonded port).

Docker Desktop on Windows is a **lab**. Ubuntu Server with **host networking** is the **kit**. They are not the same topology. Copying the Windows habit onto Ubuntu is what made the kit look broken.

---

## 1. What “working” actually means

The kit is working when **all** of these are true:

| Check | Pass |
| --- | --- |
| Container `leocastra-field-agent` is Up | `sudo docker ps` |
| Web UI answers on the kit | `curl -I http://127.0.0.1:8088` → HTTP 200 |
| Another device on the LAN can open `http://<kit-lan-ip>:8088` | Not `docker0`, not `127.0.0.1` from the laptop |
| Ingest host + bonded port match the studio Field OB channel | Status line shows `host:port` |
| That Field OB is **listening** in studio | After a studio restart you must start listening again |
| **No other** Field Agent is sending to the same bonded port | Stop the Windows lab agent first |
| Bond shows **N/N Up** (or 1/1) with metrics live | Not “Sender offline” / restart loop |
| Encoder publishes **SRT caller / MPEG-TS** to `srt://<kit-ip>:4001` | Input goes from No Feed to Live |

Ping, HTTPS to the studio website, and DNS can all succeed while the bond stays Down. The bond is **UDP SRTLA** to `ingest-host:bonded-port`, not ICMP and not TCP 443.

---

## 2. Why Windows Docker worked and Ubuntu did not

### Windows lab (proven)

- Compose file: `docker-compose.yml` only (published ports `4001/udp` and `8088`).
- The container has **one** address on Docker’s NAT bridge.
- `srtla_send` binds that single IP. There is no real Wi-Fi/LTE bonding.
- Ingest was the studio Field OB host and bonded UDP port (for example `ingest.example.com:10180`).
- Studio was listening. Bond went **1/1**.

That proves: image, UI, SRTLA registration, and the live ingest path can work.

### Ubuntu kit (required for a real bond)

- Compose files: `docker-compose.yml` **and** `docker-compose.kit.yml`.
- `network_mode: host` so Ethernet, Wi-Fi, and USB LTE keep their own IPv4 addresses.
- `srtla_send` binds **each** source IP and sends REG packets to ingest.
- Linux will **not** send those packets out the matching NIC unless **source routing** exists. Bind() only sets the source address.
- Host networking also exposes `docker0` (`172.17.0.1`). That must never be an uplink.

### Failures we actually saw on the mini PC

| Symptom | Real cause |
| --- | --- |
| Install “finished” but no UI URL | Console has no clickable link. URL is `http://<lan-ip>:8088`. |
| `ip addr` shows only `docker0` | No DHCP/LAN on a real NIC (direct cable without static IPs, or cable unplugged). |
| `curl http://127.0.0.1:8088` connection refused | Container never created (`docker ps -a` empty). Build/pull needs internet + DNS. |
| `ping 8.8.8.8` works, `github.com` does not | No DNS. Installer cannot clone or pull. |
| UI Up, bond 0/3, Link A/B/Satellite Down | `docker0` + Ethernet + `usb0` all passed to `srtla_send`. Sender crash-loop. |
| `usb0` has internet, UI shows Down | Ping is ICMP. UI Down means SRTLA REG failed or sender exited. |
| `Bond sender exited; restarting in 2s` | `srtla_send` exits when **no** uplink completes SRTLA registration (studio not listening, UDP blocked, or packets leaving the wrong NIC). |
| Live ingest “captured” on 10180 | Windows Field Agent still sending to that port. One sender per bonded port. |
| Bond flaps / Agent looks offline on a USB blip | Watchdog used to drop a vanished IP after 2s and SIGHUP the whole bind file. Current image keeps that IP ~15s so `srtla_send` can recover the path. Rebuild the kit after pulling this change. |

The current kit image skips Docker bridges, installs source routes, sets loose `rp_filter`, and shows **Waiting / Restarting** instead of pretending `usb0` is a dead modem.

---

## 3. Choose the OS (do this before anything else)

**Use Ubuntu Server LTS, 64-bit (amd64), no desktop.**

| Version | Verdict |
| --- | --- |
| **22.04 LTS (Jammy)** | **Recommended.** Docker CE, netplan, OpenSSH, host networking all proven for this kit. |
| **24.04 LTS (Noble)** | Supported. Same install path. |
| **20.04 LTS (Focal)** | Can run. Past standard support (April 2025). DNS/`resolvectl` is fussier. Prefer a reinstall on 22.04 if you are still fighting the box. |
| Ubuntu Desktop | Do not use. Extra NetworkManager / GUI networking fights the kit. |
| Raspberry Pi OS / Debian without this installer | Not the documented path. |

ISO: [Ubuntu Server 22.04 LTS](https://ubuntu.com/download/server) for amd64. Flash with Raspberry Pi Imager, balenaEtcher, or Rufus (DD image mode).

Hardware floor that matches how the image builds:

- x86_64 mini PC (Intel NUC class is fine)
- 4 GB RAM (2 GB can build, but the Rust `srtla_send` compile is tight)
- 32 GB disk (Docker image build uses several GB of cache)
- Ethernet NIC (required for first install and Web UI)
- Optional: USB LTE / phone tether (`usb0`), Wi-Fi

---

## 4. Ubuntu installer (pre-work)

During **Ubuntu Server** install, set:

1. Language / keyboard.
2. **Ethernet: DHCP** on the NIC that goes to a router with internet. Do not skip network. The first install must reach `github.com` and `download.docker.com`.
3. **Do not** use a direct PC-to-PC Ethernet cable as the only link unless you also set static IPs (see appendix). A cheap router or the venue LAN is the path that works.
4. Mirror: default.
5. Disk: use entire disk, no LVM required.
6. Profile: a normal user with a password you will use for SSH (example `kit` / your password). This user is in `sudo`.
7. **Install OpenSSH server: ON** (checkbox on the Ubuntu Server installer).
8. Featured snaps: **none**. Do not install the Docker snap. This installer uses Docker CE.
9. Reboot. Remove USB installer.

First login on the console (or via SSH if the installer already gave you an IP):

```bash
hostnamectl
ip -br link
ip -4 -o addr show scope global
ping -c 3 8.8.8.8
getent ahostsv4 github.com
```

You need a **non-`docker0`** IPv4 (for example `192.168.0.101` on `enp0s25`) **and** DNS. If ping works and `getent` fails:

```bash
sudo resolvectl dns enp0s25 8.8.8.8 1.1.1.1
getent ahostsv4 github.com
```

Replace `enp0s25` with the name from `ip -br link`.

Enable SSH if you skipped it in the installer:

```bash
sudo apt-get update
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh
```

From Windows (same LAN):

```powershell
ssh kit@192.168.0.101
```

Use the Ubuntu username and the kit LAN IP. After this you can finish the rest without a monitor.

---

## 5. One-command Field Agent install

Still as a user with `sudo`, on the kit:

```bash
curl -fsSL https://raw.githubusercontent.com/pimccontent/leocastra-field-agent/main/install.sh | sudo bash
```

From a git clone of this repo:

```bash
sudo ./install.sh
```

What the script does on Ubuntu:

1. Warns if the OS is not 22.04/24.04.
2. Installs OpenSSH, `iproute2`, git, curl.
3. Writes `/etc/sysctl.d/99-leocastra-field-agent.conf` (`rp_filter=2`) so Ethernet + `usb0` can receive SRTLA replies.
4. Fixes DNS if `github.com` does not resolve.
5. Installs Docker CE + Compose plugin (not the snap), enables Docker on boot.
6. Clones this repo to `/opt/leocastra-field-agent` if needed.
7. Starts `docker compose -f docker-compose.yml -f docker-compose.kit.yml up -d --build` (**host network**, privileged, `NET_ADMIN`).
8. Prints `http://<lan-ip>:8088` and `ssh <user>@<lan-ip>`.
9. Waits until `http://127.0.0.1:8088` returns 200.

The first `--build` compiles `srtla_send` inside Docker. That needs internet and several minutes. Do not unplug the WAN for this step.

Confirm:

```bash
sudo docker ps --filter name=leocastra-field-agent
curl -I http://127.0.0.1:8088
sudo docker logs --tail 50 leocastra-field-agent
```

You want the container **Up** and HTTP **200**. Logs should **not** spin `Bond sender exited` until ingest + listening are configured; after Settings are saved, a short restart is normal.

---

## 6. First Web UI setup (from a laptop on the same LAN)

There is **no** URL on the Ubuntu console to click. On the laptop browser:

**`http://<kit-lan-ip>:8088`**

Example: `http://192.168.0.101:8088`

1. **Settings → Ingest host**: the UDP ingest hostname from the Field OB page (example `ingest.example.com`). Not an HTTPS-only marketing site. Copy it exactly.
2. **Bonded port**: the UDP port on that same Field OB (example `10180`). Must match studio. A kit left on `10182` will not use a channel that is listening on `10180`.
3. **Window (ms)**: match studio (usually `4000`).
4. **Uplink mode**: Auto. The kit ignores `docker0`.
5. **Save and reconnect**.

Optional: Studio URL is a bookmark only. It is not the bond.

---

## 7. Studio must listen, and only one kit may send

SRTLA registration (`REG1/REG2/REG3`) only completes if `srtla_rec` is listening on that UDP port.

1. Open the Field OB in studio.
2. **Start listening**.
3. If studio or the ingest host restarted, start listening **again**.
4. Stop any other Field Agent aimed at the same `host:port`.

On the Windows lab PC that previously held live **10180**:

```powershell
docker update --restart=no leocastra-field-agent
docker stop leocastra-field-agent
```

`http://127.0.0.1:8088` on Windows must fail to connect. Then only the Ubuntu kit should send.

If the live channel still looks busy, stop listening and start listening once so the receiver accepts a new sender group.

---

## 8. Read the kit Status page correctly

| UI | Meaning |
| --- | --- |
| Bond **Restarting** / links **Waiting** | `srtla_send` is down or just restarted. Check logs. |
| Bond **0/2** and links **Down**, metrics live | Sender is up; SRTLA REG has not succeeded (not listening, wrong port, UDP blocked). |
| Bond **1/1** or **2/2**, links **Up** | Bond is registered. This is success **before** an encoder is required. |
| Input **No Feed** | Normal until the encoder calls `:4001`. |
| Input **Live** | Encoder packets are arriving. |

`usb0` can ping the ingest hostname and still show Down if UDP on the bonded port never gets REG2 from `srtla_rec`.

---

## 9. Encoder (after the bond is Up)

On the encoder PC (same LAN as the kit, or on the kit itself). **OBS/FFmpeg use microseconds.** Ghana 8000 ms window → `latency=8000000`. vMix uses milliseconds (`latency=8000`). Copy **OBS** from the kit Status page: Settings → Stream → Service Custom → Server, Stream Key empty.

```text
srt://192.168.0.101:4001?mode=caller&latency=8000000&rcvlatency=8000000&peerlatency=8000000&pkt_size=1316&transtype=live&tlpktdrop=0&oheadbw=50&lossmaxttl=400
```

Use the kit LAN IP. On the kit itself use `127.0.0.1`. Use **SRT caller / MPEG-TS**. Do not paste this URL into an RTMP box. Studio must be **Listening** (Start listening). Bond Up with FFmpeg down still fails OBS handshake.

Match `latency` to the contribution window on kit, studio Field OB, and encoder. Paste the studio Field OB page URL into kit Settings so RTT/Drops stay in sync.

---

## 10. Remote SSH (day-2)

```bash
ssh kit@192.168.0.101
sudo docker logs -f leocastra-field-agent
sudo docker compose -f /opt/leocastra-field-agent/docker-compose.yml -f /opt/leocastra-field-agent/docker-compose.kit.yml ps
curl -s http://127.0.0.1:8088/api/status
ip -4 addr
ip rule
ip route
```

Useful log lines:

- `Source route 10.x.x.x -> <ingest-ip> via … dev usb0` — cellular path is steered.
- `Bonding 192.168.0.101 10.x.x.x -> host:port` — Docker bridge IPs must not appear.
- `Failed to establish any initial connections` — REG never completed (listen / UDP / routing).
- `listening for SRT on [::]:4001` — encoder port is bound.

If `ufw` is active:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 8088/tcp
sudo ufw allow 4001/udp
```

The installer only adds those rules when `ufw` is already active. It does not enable the firewall by default.

---

## 11. Update the kit after a code fix

```bash
cd /opt/leocastra-field-agent
sudo git fetch origin
sudo git checkout main
sudo git pull
sudo docker compose -f docker-compose.yml -f docker-compose.kit.yml up -d --build
```

Script/UI-only changes reuse the cached `srtla_send` compile. Changing the Dockerfile Rust stage rebuilds it.

---

## 12. Firewall and DNS on the ingest side (studio)

The kit must send **UDP** to the ingest host’s public IPv4 on the bonded port (LeoCastra Field OB uses `10180`–`10189`). HTTPS 443 succeeding through Caddy does not prove that UDP is open.

- Ingest hostname must resolve to the machine that runs `srtla_rec`, not a CDN that only proxies HTTP.
- Studio cloud/security group must allow UDP on that bonded port from the field.

---

## Appendix A — Direct Ethernet with no router

Only if you cannot use a router. Pick a pair of static addresses:

| Machine | IPv4 |
| --- | --- |
| Ubuntu kit | `192.168.50.1/24` |
| Windows laptop | `192.168.50.2/24` |

Ubuntu:

```bash
sudo ip link set enp0s25 up
sudo ip addr add 192.168.50.1/24 dev enp0s25
```

Make it persistent in netplan (`/etc/netplan/60-direct.yaml`) with `dhcp4: false` and that address. The Web UI is then `http://192.168.50.1:8088`. The kit still needs **some** path to ingest (USB LTE, or ICS from the laptop). A copper cable with no default route will not register SRTLA to the public studio.

---

## Appendix B — Compose modes (do not mix)

| Where | Command |
| --- | --- |
| Ubuntu mini server (kit) | `docker compose -f docker-compose.yml -f docker-compose.kit.yml up -d --build` |
| Windows/macOS Docker Desktop (lab) | `docker compose up -d --build` |

Never run the kit overlay on Docker Desktop. Never run the lab-only published-port compose as the production mini server if you need Wi-Fi/LTE bonding.

---

## Appendix C — Quick recovery checklist

```bash
ip -4 -o addr show scope global          # must include Ethernet and/or usb0, not only docker0
getent ahostsv4 github.com               # DNS
getent ahostsv4 ingest.example.com       # ingest DNS
sudo docker ps -a                        # container exists and is Up
curl -I http://127.0.0.1:8088            # UI
curl -s http://127.0.0.1:8088/api/status # ingest host:port, bond, links
sudo docker logs --tail 80 leocastra-field-agent
```

Then in studio: start listening. Then on any Windows lab agent: stop it. Then Save and reconnect on the kit.
