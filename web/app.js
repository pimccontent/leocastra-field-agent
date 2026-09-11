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

async function api(path, options) {
  const r = await fetch(path, { cache: "no-store", ...options });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText || "Request failed");
  return data;
}

function renderStatus(s) {
  const bondKind = s.bond.active > 0 ? "ok" : s.metricsOk ? "wait" : "down";
  const bondText = s.bond.active > 0
    ? `${s.bond.active} of ${s.bond.configured} up`
    : "Reconnecting to studio ingest";
  return `
    <div class="grid">
      <div class="card">
        ${label("Encoder", "Your encoder must publish SRT caller to this kit listen port. Programme appears here once packets arrive.")}
        <div id="encoder" class="metric ${cls(s.encoder.receiving)}">${esc(s.encoder.label)}</div>
        <div class="muted">${s.encoder.bitrateKbps ? s.encoder.bitrateKbps + " kbps" : "No programme yet"}</div>
      </div>
      <div class="card">
        ${label("Bond", "Paths from this kit to studio ingest. Down usually means the studio channel is not listening, or this kit cannot reach the ingest host on UDP.")}
        <div class="metric ${cls(bondKind)}">${esc(bondText)}</div>
        <div class="muted">${esc(s.bond.mode)} scheduler</div>
      </div>
      <div class="card">
        ${label("Listen", "Local SRT port for the encoder. On this box use 127.0.0.1; from another PC use this kit???s LAN IP.")}
        <div class="metric">0.0.0.0:${esc(s.listenPort)}</div>
        <div class="muted">SRT caller from the encoder</div>
      </div>
      <div class="card">
        ${label("Studio ingest", "UDP host and bonded port from the studio contribution channel. This is not the HTTPS studio website unless that name is an A record to the ingest host.")}
        <div class="metric" style="font-size:1rem">${esc(s.ingest || "Not set")}</div>
        <div class="muted">UDP bonded ingest</div>
      </div>
    </div>
    <div class="section card">
      <h2>Uplinks</h2>
      <table>
        <thead><tr><th>Link</th><th>Bind</th><th>Kind</th><th>State</th><th>Bitrate</th><th>RTT</th><th>NAKs</th></tr></thead>
        <tbody>
          ${(s.links || []).map((l) => `
            <tr>
              <td>${esc(l.label)}</td>
              <td><code>${esc(l.ip)}</code></td>
              <td>${esc(l.kind || "???")}</td>
              <td class="${l.up ? "ok" : "down"}">${l.up ? "Up" : "Down"}</td>
              <td>${l.bitrateKbps} kbps</td>
              <td>${l.rttMs} ms</td>
              <td>${l.naks}</td>
            </tr>`).join("") || `<tr><td colspan="7" class="muted">No uplinks</td></tr>`}
        </tbody>
      </table>
    </div>
    <div class="section card">
      <h2>Networks</h2>
      <table>
        <thead><tr><th>Interface</th><th>Address</th><th>Kind</th><th>Bond</th></tr></thead>
        <tbody>
          ${(s.networks || []).map((n) => `
            <tr>
              <td>${esc(n.iface)}</td>
              <td><code>${esc(n.address)}</code></td>
              <td>${esc(n.kind)}</td>
              <td>${n.bonded ? "Yes" : "No"}</td>
            </tr>`).join("") || `<tr><td colspan="4" class="muted">No addresses</td></tr>`}
        </tbody>
      </table>
      <p class="muted" style="margin:0.8rem 0 0">${s.metricsOk ? "Live ?? 2 s refresh" : "Bond metrics unavailable"}</p>
    </div>
  `;
}

function renderSettings(c, nets, message) {
  const wifiIfaces = (nets || []).filter((n) => n.kind === "wifi");
  return `
    ${message ? `<div class="banner ${message.ok ? "ok" : "err"}">${esc(message.text)}</div>` : ""}
    <form id="settingsForm" class="card">
      <h2>Studio ingest</h2>
      <div class="field">
        ${label("Ingest host", "Hostname or IP from the studio contribution channel. On Docker Desktop toward a local studio use host.docker.internal.")}
        <input name="leocastraHost" value="${esc(c.leocastraHost)}" required/>
      </div>
      <div class="field">
        ${label("Bonded port", "UDP port from the same studio endpoint.")}
        <input name="bondedPort" type="number" min="1" max="65535" value="${esc(c.bondedPort)}" required/>
      </div>
      <div class="field">
        ${label("Local SRT listen port", "Port the encoder calls. Must match the Field Agent URL shown in studio.")}
        <input name="listenPort" type="number" min="1" max="65535" value="${esc(c.listenPort)}" required/>
      </div>
      <div class="field">
        ${label("Studio URL", "Optional studio page shown as a shortcut on this kit. Not used for the bond.")}
        <input name="studioUrl" value="${esc(c.studioUrl)}" placeholder="https://studio.example.com/???"/>
      </div>
      <div class="field">
        ${label("Contribution window (ms)", "Match the studio contribution window. The Agent scales link timeout so a drop inside this budget can resume without a full handshake.")}
        <input name="latencyMs" type="number" min="1500" max="8000" value="${esc(c.latencyMs)}" required/>
      </div>
      <div class="field">
        ${label("Scheduler", "Enhanced scores links in real time. Classic round-robins. Use Enhanced unless a lab asks otherwise.")}
        <select name="schedulerMode">
          <option value="enhanced" ${c.schedulerMode === "enhanced" ? "selected" : ""}>Enhanced</option>
          <option value="classic" ${c.schedulerMode === "classic" ? "selected" : ""}>Classic</option>
        </select>
      </div>
      <div class="field">
        ${label("Uplink mode", "Auto binds every global IPv4 (Ethernet, Wi-Fi, LTE). Manual pins specific source IPs.")}
        <select name="uplinkMode" id="uplinkMode">
          <option value="auto" ${c.uplinkMode === "auto" ? "selected" : ""}>Auto (all interfaces)</option>
          <option value="manual" ${c.uplinkMode === "manual" ? "selected" : ""}>Manual IPs</option>
        </select>
      </div>
      <div class="field" id="manualIps" style="${c.uplinkMode === "manual" ? "" : "display:none"}">
        ${label("Source IPs", "One IPv4 per uplink, one per line. Mix independent radio cores. Wi-Fi is valid as a path.")}
        <textarea name="uplinkIps" rows="4">${esc((c.uplinkIps || []).join("\n"))}</textarea>
      </div>
      <div class="actions">
        <button class="primary" type="submit">Save and reconnect</button>
        <button class="ghost" type="button" id="restartOnly">Reconnect now</button>
      </div>
    </form>
    <div class="section card">
      <h2>Wi-Fi ${info("Wi-Fi is a normal bonded uplink on a Linux kit using host networking. Docker Desktop only shows one NAT path.")}</h2>
      <div class="field">
        ${label("Interface", "wlan0 or similar. Requires host networking on the mini server.")}
        <select id="wifiIface">
          ${wifiIfaces.length
            ? wifiIfaces.map((n) => `<option value="${esc(n.iface)}">${esc(n.iface)} ?? ${esc(n.address)}</option>`).join("")
            : `<option value="">No Wi-Fi interface visible</option>`}
        </select>
      </div>
      <div class="field">
        ${label("SSID", "Network name. Scan if this kit can see wireless radios.")}
        <input id="wifiSsid"/>
      </div>
      <div class="field">
        ${label("Password", "WPA passphrase. Stored only for the connect attempt on this kit.")}
        <input id="wifiPsk" type="password"/>
      </div>
      <div class="actions">
        <button class="ghost" type="button" id="wifiScan">Scan</button>
        <button class="primary" type="button" id="wifiConnect">Connect Wi-Fi</button>
      </div>
      <div id="wifiScanResult" class="muted" style="margin-top:0.75rem"></div>
    </div>
  `;
}

function renderHelp() {
  return `
    <article class="help card">
      <h2>Help</h2>
      <h3>What this kit does</h3>
      <p>LeoCastra Field Agent is a field contribution appliance. Your encoder sends SRT to this kit. The Agent bonds Ethernet, Wi-Fi, and cellular uplinks and forwards a single low-latency path to studio ingest. Studio pulls that programme as an SRT caller. The kit does not transcode video.</p>
      <h3>Power-on</h3>
      <p>The Agent starts with the mini server. You do not need a monitor. Docker kits use <code>restart: always</code> (enable Docker on boot with <code>install-kit.sh</code>). Bare-metal kits: <code>systemctl enable --now leocastra-field-agent</code>.</p>
      <h3>First setup (no SSH after this)</h3>
      <ol>
        <li>On a laptop or phone on the same LAN open <code>http://&lt;kit-ip&gt;:8088</code>.</li>
        <li>Settings: enter ingest host and bonded port from the studio contribution channel.</li>
        <li>Match the contribution window to studio (4000 ms is the usual start).</li>
        <li>Save and reconnect. Start listening on the studio channel.</li>
        <li>Encoder: SRT caller to this kit, port 4001 (or the listen port you set), same latency as the window.</li>
      </ol>
      <h3>Link A Down / 0 of 1 up</h3>
      <ol>
        <li>The studio channel must be listening. After a studio restart, start listening again.</li>
        <li>Ingest host must be the UDP ingest machine, not an HTTPS-only website proxy.</li>
        <li>Use Reconnect now on Settings. The kit also reconnects on its own if every uplink stays down.</li>
        <li>Two-path bonding needs a Linux kit with host networking. Docker Desktop shows one NAT path.</li>
      </ol>
      <h3>Wi-Fi</h3>
      <p>On Ubuntu Server with host networking, <code>wlan0</code> is an uplink the same way Ethernet or LTE is. Auto mode includes it when it has an IPv4 address. Connect SSID from Settings, then Save if you pin IPs manually.</p>
      <h3>Smooth contribution</h3>
      <ul>
        <li>Encoder latency must match the studio window.</li>
        <li>Two SIMs must be different radio cores.</li>
        <li>Studio pull is SRT caller.</li>
        <li>Leave the Agent running. Do not send the encoder past this kit to studio while the Agent is in the path.</li>
      </ul>
    </article>
  `;
}

let statusTimer = 0;

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
    const form = ev.target;
    const body = {
      leocastraHost: form.leocastraHost.value.trim(),
      bondedPort: Number(form.bondedPort.value),
      listenPort: Number(form.listenPort.value),
      studioUrl: form.studioUrl.value.trim(),
      latencyMs: Number(form.latencyMs.value),
      schedulerMode: form.schedulerMode.value,
      uplinkMode: form.uplinkMode.value,
      uplinkIps: form.uplinkIps.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean),
    };
    try {
      await api("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await api("/api/restart", { method: "POST" });
      await showSettings({ ok: true, text: "Saved. Bond is reconnecting." });
    } catch (err) {
      await showSettings({ ok: false, text: err.message });
    }
  });
  document.getElementById("restartOnly").addEventListener("click", async () => {
    try {
      await api("/api/restart", { method: "POST" });
      await showSettings({ ok: true, text: "Reconnect requested." });
    } catch (err) {
      await showSettings({ ok: false, text: err.message });
    }
  });
  document.getElementById("wifiScan").addEventListener("click", async () => {
    const iface = document.getElementById("wifiIface").value;
    const box = document.getElementById("wifiScanResult");
    try {
      const data = await api("/api/wifi/scan?iface=" + encodeURIComponent(iface || ""));
      box.innerHTML = (data.ssids || []).length
        ? data.ssids.map((x) => `<div><button class="ghost" type="button" data-ssid="${esc(x.ssid)}">${esc(x.ssid)}</button> ${esc(x.signal || "")}</div>`).join("")
        : esc(data.error || "No networks found");
      box.querySelectorAll("[data-ssid]").forEach((btn) => {
        btn.addEventListener("click", () => {
          document.getElementById("wifiSsid").value = btn.getAttribute("data-ssid");
        });
      });
    } catch (err) {
      box.textContent = err.message;
    }
  });
  document.getElementById("wifiConnect").addEventListener("click", async () => {
    try {
      await api("/api/wifi/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          iface: document.getElementById("wifiIface").value,
          ssid: document.getElementById("wifiSsid").value,
          psk: document.getElementById("wifiPsk").value,
        }),
      });
      await showSettings({ ok: true, text: "Wi-Fi connect requested. Confirm the interface has an address, then Save if you use manual IPs." });
    } catch (err) {
      await showSettings({ ok: false, text: err.message });
    }
  });
}

async function showSettings(message) {
  const [c, s] = await Promise.all([api("/api/config"), api("/api/status")]);
  document.getElementById("app").innerHTML = renderSettings(c, s.networks, message);
  bindSettings();
}

async function tickStatus() {
  if (route() !== "status") return;
  const s = await api("/api/status");
  document.getElementById("kitHost").textContent = s.hostname || "Contribution kit";
  const pill = document.getElementById("bondPill");
  const up = s.bond.active > 0;
  pill.className = "pill " + (up ? "ok" : s.metricsOk ? "wait" : "down");
  document.getElementById("bondPillText").textContent = up ? "BOND UP" : "BOND DOWN";
  document.getElementById("app").innerHTML = renderStatus(s);
}

async function draw() {
  const page = route();
  setNav(page);
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
  document.getElementById("app").innerHTML = `<div class="banner err">${esc(err.message)}</div>`;
});
