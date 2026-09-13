function info(text) {
  return `<span class="info" tabindex="0" aria-label="About this setting">i<span class="tip">${text}</span></span>`;
}

function label(text, tip) {
  return `<div class="label-row">${text}${tip ? info(tip) : ""}</div>`;
}

function cls(kind) {
  if (kind === true || kind === "up" || kind === "ok") return "ok";
  if (kind === "wait" || kind === "reconnecting") return "wait";
  return "down";
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function isNoiseIface(name) {
  const iface = String(name || "").toLowerCase();
  return (
    iface === "lo" ||
    iface.startsWith("docker") ||
    iface.startsWith("br-") ||
    iface.startsWith("veth") ||
    iface.startsWith("cni") ||
    iface.startsWith("flannel") ||
    iface.startsWith("virbr")
  );
}

function isDockerBridgeIp(ip) {
  return /^172\.17\./.test(String(ip || ""));
}

function visibleNetworks(list) {
  return (list || []).filter((n) => !isNoiseIface(n.iface) && !isDockerBridgeIp(n.address));
}

function visibleLinks(list) {
  return (list || []).filter((l) => !isDockerBridgeIp(l.ip) && !isNoiseIface(l.iface));
}

function route() {
  const hash = (location.hash || "#/").replace(/^#/, "");
  if (hash.startsWith("/settings")) return "settings";
  if (hash.startsWith("/help")) return "help";
  return "status";
}

function setNav(page) {
  ["status", "settings", "help"].forEach((name) => {
    document.getElementById("nav-" + name).classList.toggle("active", page === name);
  });
}

function notice(kind, text) {
  const el = document.getElementById("notice");
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.innerHTML = `<div class="banner ${kind}">${esc(text)}</div>`;
}

function settingsNotice(kind, text) {
  if (route() !== "settings") return;
  notice(kind, text);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function api(path, options) {
  const r = await fetch(path, { cache: "no-store", ...options });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText || "Request failed");
  return data;
}

function setBusy(busy, extraIds) {
  const ids = ["saveReconnect", "restartOnly", "wifiScan", "wifiConnect"].concat(extraIds || []);
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

function renderStatus(s) {
  const bondKind = s.bond.active > 0 ? "ok" : s.metricsOk ? "wait" : "down";
  const rtt = s.path && s.path.rttMs != null ? `${s.path.rttMs} ms` : "—";
  const rttKind = s.path && s.path.rttMs != null ? "ok" : s.metricsOk ? "wait" : "down";
  const buffer = s.path ? String(s.path.inFlight || 0) : "0";
  const naks = s.path ? s.path.naks || 0 : 0;
  const bitrate = s.encoder.bitrateKbps ? `${s.encoder.bitrateKbps} kbps` : "0 kbps";
  const inputLabel = s.encoder.receiving ? (s.encoder.label || "Live") : "No Feed";
  const sync = s.studioSync || {};
  const syncHint = !s.studioUrl
    ? "Paste the studio Field OB URL in Settings so RTT/Drops match studio."
    : sync.ok
      ? "Studio synced"
      : `Studio not synced: ${sync.detail || "waiting"}`;
  const linkRows =
    visibleLinks(s.links)
      .map((l) =>
        [
          "<tr>",
          `<td>${esc(l.label)}</td>`,
          `<td><code>${esc(l.ip)}</code></td>`,
          `<td>${esc(l.kind || "—")}</td>`,
          `<td class="${l.up ? "ok" : s.metricsOk ? "down" : "wait"}">${esc(l.up ? "Up" : s.metricsOk ? "Down" : "Waiting")}</td>`,
          `<td>${l.bitrateKbps} kbps</td>`,
          `<td>${l.rttMs} ms</td>`,
          `<td>${l.naks}</td>`,
          "</tr>",
        ].join(""),
      )
      .join("") || '<tr><td colspan="7" class="muted">No uplinks</td></tr>';
  const networkRows =
    visibleNetworks(s.networks)
      .map((n) =>
        [
          "<tr>",
          `<td>${esc(n.iface)}</td>`,
          `<td><code>${esc(n.address || "—")}</code></td>`,
          `<td>${esc(n.kind)}</td>`,
          `<td>${n.kind === "ethernet" ? "LAN" : n.bonded ? "Yes" : "Pending"}</td>`,
          "</tr>",
        ].join(""),
      )
      .join("") || '<tr><td colspan="4" class="muted">No addresses</td></tr>';
  return `
    <div class="grid">
      <div class="card">
        ${label("Input", "SRT caller from your encoder to this kit. Live means video bitrate is arriving. Bond keepalives alone are No Feed.")}
        <div class="metric ${cls(s.encoder.receiving ? "ok" : "wait")}">${esc(inputLabel)}</div>
        <p class="hint">${esc(bitrate)}</p>
      </div>
      <div class="card">
        ${label("Bond", "Uplinks from this kit to studio ingest. Ping/ICMP can work while this stays Down: SRTLA is UDP to the bonded port. Studio must be listening. Waiting means the sender is restarting.")}
        <div class="metric ${cls(bondKind)}">${esc(s.bond.label)}</div>
        <p class="hint">${esc(s.bond.mode)} · ${s.windowMs || "—"} ms window</p>
      </div>
      <div class="card">
        ${label("RTT", "Worst round-trip among uplinks that are currently up. Updates from srtla_send. Studio RTT must match this — not localhost FFmpeg.")}
        <div class="metric ${cls(rttKind)}">${esc(rtt)}</div>
        <p class="hint">${s.metricsOk ? "Worst live path" : "Sender offline"} · ${esc(syncHint)}</p>
      </div>
      <div class="card">
        ${label("Buffer", "Packets in flight across the bond. NAKs are SRT retransmit requests. Enhanced uses them to shift traffic off a bad path; too many NAKs on both paths usually means the window is too small.")}
        <div class="metric ${s.metricsOk ? "ok" : "down"}">${esc(buffer)}</div>
        <p class="hint">${naks} NAK${naks === 1 ? "" : "s"}</p>
      </div>
    </div>
    <div class="section card">
      <h2>Uplinks</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Link</th><th>Bind</th><th>Kind</th><th>State</th><th>Bitrate</th><th>RTT</th><th>NAKs</th></tr></thead>
          <tbody>
            ${linkRows}
          </tbody>
        </table>
      </div>
    </div>
    <div class="section card">
      <h2>Networks</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Interface</th><th>Address</th><th>Kind</th><th>Bond</th></tr></thead>
          <tbody>
            ${networkRows}
          </tbody>
        </table>
      </div>
      <p class="hint" style="margin-top:0.8rem">${
        s.metricsOk
          ? "Ethernet Bond is LAN (OBS), not an uplink. Cellular/Wi-Fi show Pending until they join, then Yes."
          : "Bond metrics unavailable"
      }</p>
    </div>
    <div class="section card">
      <h2>Encoder</h2>
      <p class="hint">OBS: Settings → Stream → Service <strong>Custom</strong>. Paste this in <strong>Server</strong>. Leave Stream Key empty. Start Streaming. OBS/FFmpeg use latency in <strong>microseconds</strong> (8000 ms window → 8000000). vMix uses milliseconds — use the second URL.</p>
      <div class="copy-row">
        <input id="encoderUrl" readonly value="${esc(encoderUrl(s))}" spellcheck="false"/>
        <button type="button" class="ghost" id="copyEncoder">Copy OBS</button>
      </div>
      <div class="copy-row" style="margin-top:0.5rem">
        <input id="encoderUrlVmix" readonly value="${esc(encoderUrlVmix(s))}" spellcheck="false"/>
        <button type="button" class="ghost" id="copyEncoderVmix">Copy vMix</button>
      </div>
    </div>
  `;
}

function encoderQuery(s, latency) {
  const ttl = s.lossMaxTtl || Math.max(80, Math.min(400, Math.round((s.windowMs || 4000) / 20)));
  const ohead = s.oheadBw || 50;
  return `mode=caller&latency=${latency}&rcvlatency=${latency}&peerlatency=${latency}&pkt_size=1316&transtype=live&tlpktdrop=0&oheadbw=${ohead}&lossmaxttl=${ttl}`;
}

function encoderHostPort(s) {
  const port = s.listenPort || "4001";
  const host = s.lanIp && s.lanIp !== "127.0.0.1" ? s.lanIp : "127.0.0.1";
  return { host, port };
}

function encoderUrl(s) {
  const { host, port } = encoderHostPort(s);
  const ms = Math.max(20, Math.round(Number(s.windowMs) || 4000));
  return `srt://${host}:${port}?${encoderQuery(s, ms * 1000)}`;
}

function encoderUrlVmix(s) {
  const { host, port } = encoderHostPort(s);
  const ms = Math.max(20, Math.round(Number(s.windowMs) || 4000));
  return `srt://${host}:${port}?${encoderQuery(s, ms)}`;
}

function renderSettings(c, nets) {
  const wifiIfaces = (nets || []).filter((n) => n.kind === "wifi");
  return `
    <form id="settingsForm" class="settings-grid">
      <section class="card">
        <h2>Studio ingest</h2>
        <div class="field">
          ${label("Ingest host", "Hostname or IP from the studio contribution channel. On Docker Desktop toward a local studio use host.docker.internal.")}
          <input name="leocastraHost" value="${esc(c.leocastraHost)}" required autocomplete="off"/>
        </div>
        <div class="field">
          ${label("Bonded port", "UDP port from the same studio endpoint.")}
          <input name="bondedPort" type="number" min="1" max="65535" value="${esc(c.bondedPort)}" required/>
        </div>
        <div class="field">
          ${label("Window (ms)", "Must match the studio Field OB window. This is the SRT reassembly budget: Ghana dual-SIM 8000 ms on kit, studio, and encoder. Stop the encoder, save, then start it again.")}
          <input name="latencyMs" type="number" min="1500" max="8000" value="${esc(c.latencyMs)}" required/>
        </div>
        <div class="field">
          ${label("Studio URL", "Paste the studio Field OB page URL. The kit posts RTT and NAKs there so the studio RTT/Drops cards fill in. The bond itself uses ingest host + bonded port above.")}
          <input name="studioUrl" value="${esc(c.studioUrl)}" placeholder="https://studio.example.com" autocomplete="off"/>
        </div>
      </section>
      <section class="card">
        <h2>This kit</h2>
        <div class="field">
          ${label("SRT listen port", "Port the encoder calls. On this box use 127.0.0.1; from another PC use the kit LAN IP.")}
          <input name="listenPort" type="number" min="1" max="65535" value="${esc(c.listenPort)}" required/>
        </div>
        <div class="field">
          ${label("Scheduler", "Enhanced splits packets by each uplink’s window and in-flight count, then shifts away from NAK-y paths. Classic is capacity-only. Keep Enhanced for bonding.")}
          <select name="schedulerMode">
            <option value="enhanced" ${c.schedulerMode === "enhanced" ? "selected" : ""}>Enhanced</option>
            <option value="classic" ${c.schedulerMode === "classic" ? "selected" : ""}>Classic</option>
          </select>
        </div>
        <div class="field">
          ${label("Uplink mode", "Auto binds every global IPv4. Manual pins specific source IPs.")}
          <select name="uplinkMode" id="uplinkMode">
            <option value="auto" ${c.uplinkMode === "auto" ? "selected" : ""}>Auto (all interfaces)</option>
            <option value="manual" ${c.uplinkMode === "manual" ? "selected" : ""}>Manual IPs</option>
          </select>
        </div>
        <div class="field" id="manualIps" style="${c.uplinkMode === "manual" ? "" : "display:none"}">
          ${label("Source IPs", "One IPv4 per uplink, one per line.")}
          <textarea name="uplinkIps" rows="4">${esc((c.uplinkIps || []).join("\n"))}</textarea>
        </div>
      </section>
      <section class="card">
        <h2>Wi-Fi ${info("A bonded uplink on Linux kits with host networking. Docker Desktop shows one NAT path.")}</h2>
        <div class="field">
          ${label("Interface", "wlan0 or similar. Requires host networking on the mini server.")}
          <select id="wifiIface">
            ${wifiIfaces.length
              ? wifiIfaces.map((n) => `<option value="${esc(n.iface)}">${esc(n.iface)} · ${esc(n.address)}</option>`).join("")
              : `<option value="">No Wi-Fi interface visible</option>`}
          </select>
        </div>
        <div class="field">
          ${label("SSID", "Network name. Scan if this kit can see wireless radios.")}
          <input id="wifiSsid" autocomplete="off"/>
        </div>
        <div class="field">
          ${label("Password", "WPA passphrase. Used only for this connect attempt.")}
          <input id="wifiPsk" type="password"/>
        </div>
        <div class="actions">
          <button class="ghost" type="button" id="wifiScan">Scan</button>
          <button class="primary" type="button" id="wifiConnect">Connect Wi-Fi</button>
        </div>
        <div id="wifiScanResult" class="scan-list"></div>
      </section>
      <section class="card apply-card">
        <h2>Apply</h2>
        <p class="apply-copy">Writes ingest and kit settings, then restarts the bond. Reconnect uses the last saved config.</p>
        <div class="actions">
          <button class="primary" type="submit" id="saveReconnect">Save and reconnect</button>
          <button class="ghost" type="button" id="restartOnly">Reconnect now</button>
        </div>
      </section>
    </form>
  `;
}

function renderHelp() {
  return `
    <section class="help-hero card">
      <h2>Field contribution kit</h2>
      <p>Encoder sends SRT here. This kit bonds Ethernet, Wi-Fi, and cellular, then forwards one low-latency path to studio ingest. Studio pulls as an SRT caller. The kit does not transcode.</p>
    </section>
    <div class="help-grid">
      <article class="help-card card">
        <h3>Power-on</h3>
        <p>Starts with the mini server. No monitor required. Docker kits restart with the host. Bare-metal kits enable the service on boot.</p>
      </article>
      <article class="help-card card">
        <h3>Encoder</h3>
        <p>Copy <strong>OBS</strong> from Status. Settings → Stream → Service Custom. Paste in Server. Leave Stream Key empty. OBS latency is microseconds (Ghana 8000 ms → 8000000 in the URL). Then Start Streaming. vMix uses the milliseconds URL.</p>
      </article>
      <article class="help-card card">
        <h3>Studio</h3>
        <p>Start listening on the contribution channel. Ingest host is the UDP machine, not an HTTPS-only website. Pull is SRT caller.</p>
      </article>
    </div>
    <section class="help-steps card">
      <h2>First setup</h2>
      <ol>
        <li>On the same LAN open <code>http://&lt;kit-ip&gt;:8088</code>.</li>
        <li>Settings: ingest host and bonded port from the studio channel.</li>
        <li>Match the window to studio (Ghana cellular: 8000 ms on both).</li>
        <li>Save and reconnect, then start listening on studio.</li>
        <li>Copy the OBS URL from Status. Settings → Stream → Custom → Server. Stream Key empty. Start Streaming. Studio must be listening on that Field OB channel.</li>
      </ol>
    </section>
    <section class="help-block card section">
      <h3>Bond 0/1 or Link A Down</h3>
      <ol>
        <li>Studio channel must be listening. After a studio restart, start listening again.</li>
        <li>Ingest host must accept UDP. Cloudflare HTTP proxy cannot carry SRTLA.</li>
        <li>Use Reconnect now only after ingest settings change. Unplugging one USB reloads that path in place — the remaining uplink must keep the encoder session.</li>
        <li>Two-path bonding needs a Linux kit with host networking. Docker Desktop is one NAT path.</li>
        <li>Ping on usb0 only proves ICMP. The bond is UDP to the ingest host:port. A cellular address still needs source routing (the kit applies that on Linux).</li>
      </ol>
    </section>
    <section class="help-block card section">
      <h3>How bonding stays smoother than one uplink</h3>
      <ol>
        <li><strong>Split</strong> — each MPEG-TS packet goes on one uplink (window ÷ in-flight). Faster links carry more.</li>
        <li><strong>Balance</strong> — Enhanced scores NAKs so a lossy USB gets less traffic within about 8 s of recovery.</li>
        <li><strong>Redundancy</strong> — a stalled path is skipped while the other keeps the encoder session. Unplug must not Restart.</li>
        <li><strong>Reassembly</strong> — studio SRT waits <em>lossmaxttl</em> packets before NAK, inside the window. Match Ghana 8000 ms on kit, studio, and encoder.</li>
      </ol>
    </section>
    <section class="help-block card section">
      <h3>Wi-Fi and bonding</h3>
      <p class="muted">On Ubuntu Server with host networking, <code>wlan0</code> is an uplink the same way Ethernet or LTE is. Auto mode includes it when it has an IPv4 address. Connect SSID from Settings, then Save if you pin IPs manually. Two SIMs must be different radio cores.</p>
    </section>
  `;
}

function readSettingsBody(form) {
  const host = form.leocastraHost.value.trim();
  const bondedPort = Number(form.bondedPort.value);
  const listenPort = Number(form.listenPort.value);
  const latencyMs = Number(form.latencyMs.value);
  if (!host) throw new Error("Ingest host is required");
  if (!Number.isInteger(bondedPort) || bondedPort < 1 || bondedPort > 65535) {
    throw new Error("Bonded port must be 1–65535");
  }
  if (!Number.isInteger(listenPort) || listenPort < 1 || listenPort > 65535) {
    throw new Error("Listen port must be 1–65535");
  }
  if (!Number.isInteger(latencyMs) || latencyMs < 1500 || latencyMs > 8000) {
    throw new Error("Contribution window must be 1500–8000 ms");
  }
  if (form.uplinkMode.value === "manual") {
    const ips = form.uplinkIps.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (!ips.length) throw new Error("Manual mode needs at least one source IP");
  }
  return {
    leocastraHost: host,
    bondedPort,
    listenPort,
    studioUrl: form.studioUrl.value.trim(),
    latencyMs,
    schedulerMode: form.schedulerMode.value,
    uplinkMode: form.uplinkMode.value,
    uplinkIps: form.uplinkIps.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
  };
}

async function waitForBond(ms, saved) {
  const prefix = saved ? "Saved. " : "";
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    await sleep(1500);
    try {
      const s = await api("/api/status");
      if (s.bond && s.bond.active > 0) {
        settingsNotice("ok", `${prefix}Bond up (${s.bond.active}/${s.bond.configured}).`);
        return;
      }
    } catch {
      /* keep waiting */
    }
  }
  settingsNotice("wait", `${prefix}Bond still down. Start listening on the studio channel, then Reconnect.`);
}

function bindSettings() {
  const mode = document.getElementById("uplinkMode");
  const manual = document.getElementById("manualIps");
  if (mode) {
    mode.addEventListener("change", () => {
      manual.style.display = mode.value === "manual" ? "" : "none";
    });
  }
  document.getElementById("settingsForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    let body;
    try {
      body = readSettingsBody(ev.target);
    } catch (err) {
      settingsNotice("err", err.message);
      return;
    }
    setBusy(true);
    settingsNotice("wait", "Saving settings…");
    try {
      await api("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (err) {
      settingsNotice("err", `Save failed: ${err.message}`);
      setBusy(false);
      return;
    }
    try {
      await api("/api/restart", { method: "POST" });
    } catch (err) {
      settingsNotice("err", `Saved, but reconnect failed: ${err.message}`);
      setBusy(false);
      return;
    }
    settingsNotice("wait", "Saved. Reconnecting bond…");
    await waitForBond(8000, true);
    setBusy(false);
  });
  document.getElementById("restartOnly").addEventListener("click", async () => {
    setBusy(true);
    settingsNotice("wait", "Reconnect requested…");
    try {
      await api("/api/restart", { method: "POST" });
    } catch (err) {
      settingsNotice("err", `Reconnect failed: ${err.message}`);
      setBusy(false);
      return;
    }
    await waitForBond(8000, false);
    setBusy(false);
  });
  document.getElementById("wifiScan").addEventListener("click", async () => {
    const iface = document.getElementById("wifiIface").value;
    const box = document.getElementById("wifiScanResult");
    setBusy(true);
    settingsNotice("wait", "Scanning Wi-Fi…");
    try {
      const data = await api("/api/wifi/scan?iface=" + encodeURIComponent(iface || ""));
      const ssids = data.ssids || [];
      if (!ssids.length) {
        box.innerHTML = `<span class="muted">${esc(data.error || "No networks found")}</span>`;
        settingsNotice("wait", data.error || "No Wi-Fi networks found.");
      } else {
        box.innerHTML = ssids
          .map((x) => `<button class="ghost" type="button" data-ssid="${esc(x.ssid)}">${esc(x.ssid)}${x.signal ? " · " + esc(x.signal) : ""}</button>`)
          .join("");
        box.querySelectorAll("[data-ssid]").forEach((btn) => {
          btn.addEventListener("click", () => {
            document.getElementById("wifiSsid").value = btn.getAttribute("data-ssid");
          });
        });
        settingsNotice("ok", `Found ${ssids.length} network${ssids.length === 1 ? "" : "s"}.`);
      }
    } catch (err) {
      box.innerHTML = `<span class="muted">${esc(err.message)}</span>`;
      settingsNotice("err", `Scan failed: ${err.message}`);
    }
    setBusy(false);
  });
  document.getElementById("wifiConnect").addEventListener("click", async () => {
    const ssid = document.getElementById("wifiSsid").value.trim();
    if (!ssid) {
      settingsNotice("err", "SSID is required.");
      return;
    }
    setBusy(true);
    settingsNotice("wait", `Connecting to ${ssid}…`);
    try {
      await api("/api/wifi/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          iface: document.getElementById("wifiIface").value,
          ssid,
          psk: document.getElementById("wifiPsk").value,
        }),
      });
      settingsNotice("ok", `Wi-Fi connect requested for ${ssid}. Confirm the interface has an address, then Save if you use manual IPs.`);
    } catch (err) {
      settingsNotice("err", `Wi-Fi connect failed: ${err.message}`);
    }
    setBusy(false);
  });
}

async function showSettings() {
  const [c, s] = await Promise.all([api("/api/config"), api("/api/status")]);
  if (route() !== "settings") return;
  applyHeader(s);
  document.getElementById("app").innerHTML = renderSettings(c, visibleNetworks(s.networks));
  bindSettings();
}

function applyHeader(s) {
  const pill = document.getElementById("bondPill");
  if (!pill) return;
  const up = s.bond.active > 0;
  const restarting = !s.metricsOk;
  pill.className = "pill " + (up ? "ok" : restarting ? "wait" : "down");
  document.getElementById("bondPillText").textContent = up
    ? "BOND UP"
    : restarting
      ? "RESTARTING"
      : "BOND DOWN";
}

async function tickStatus() {
  if (route() !== "status") return;
  const s = await api("/api/status");
  if (route() !== "status") return;
  applyHeader(s);
  document.getElementById("app").innerHTML = renderStatus(s);
  bindEncoderCopy();
}

function bindCopy(btnId, inputId, okMsg) {
  const btn = document.getElementById(btnId);
  const input = document.getElementById(inputId);
  if (!btn || !input) return;
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(input.value);
      notice("ok", okMsg);
      setTimeout(() => {
        if (route() === "status") notice("", "");
      }, 2500);
    } catch {
      input.select();
      notice("wait", "Select and copy the encoder URL.");
    }
  });
}

function bindEncoderCopy() {
  bindCopy("copyEncoder", "encoderUrl", "OBS URL copied.");
  bindCopy("copyEncoderVmix", "encoderUrlVmix", "vMix URL copied.");
}

let statusTimer = 0;

async function draw() {
  const page = route();
  setNav(page);
  if (page !== "settings") notice("", "");
  if (statusTimer) {
    clearInterval(statusTimer);
    statusTimer = 0;
  }
  if (page === "help") {
    document.getElementById("app").innerHTML = renderHelp();
    return;
  }
  if (page === "settings") {
    await showSettings();
    return;
  }
  await tickStatus();
  statusTimer = setInterval(() => tickStatus().catch(() => {}), 2000);
}

window.addEventListener("hashchange", () => draw().catch(console.error));
draw().catch((err) => {
  notice("err", err.message);
  document.getElementById("app").innerHTML = "";
});
