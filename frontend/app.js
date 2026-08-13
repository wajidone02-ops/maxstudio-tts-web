const API = "/api";
let TOKEN = localStorage.getItem("mx_token") || null;
let AUTH_MODE = "login";
let jobsPollTimer = null;

window.addEventListener("DOMContentLoaded", async () => {
  await checkSession();
});

// ─── Session ────────────────────────────────────────────────────────────

async function checkSession() {
  if (!TOKEN) { showAuth(); return; }
  try {
    const res = await fetch(`${API}/session?token=${encodeURIComponent(TOKEN)}`);
    const data = await res.json();
    if (data.ok) { await enterApp(data); return; }
  } catch (e) {}
  TOKEN = null;
  localStorage.removeItem("mx_token");
  showAuth();
}

function showAuth() {
  document.getElementById("authOverlay").classList.remove("hidden");
  document.getElementById("mainApp").classList.add("hidden");
}

async function enterApp(sessionData) {
  document.getElementById("authOverlay").classList.add("hidden");
  document.getElementById("mainApp").classList.remove("hidden");
  renderUserInfo(sessionData);
  await loadVoices();
  await loadJobs();
  if (jobsPollTimer) clearInterval(jobsPollTimer);
  jobsPollTimer = setInterval(loadJobs, 5000);
}

function renderUserInfo(data) {
  document.getElementById("userEmail").textContent = data.email || "";
  document.getElementById("userInfo").classList.remove("hidden");
  document.getElementById("logoutBtn").classList.remove("hidden");
  renderUsage(data.chars_used_today || 0, data.daily_char_limit || 100000);

  if (data.agent_status && data.agent_status !== "ready") {
    document.getElementById("generateMsg").textContent =
      data.agent_status === "pending_agent_login"
        ? "Aapka HeyGen account abhi connect nahi hua — admin se contact karo."
        : "";
    document.getElementById("generateMsg").style.color = "#fbbf24";
  }
}

function renderUsage(used, limit) {
  const pct = Math.min(100, (used / limit) * 100);
  const fill = document.getElementById("usageBarFill");
  fill.style.width = `${pct}%`;
  fill.className = "usage-bar-fill" + (pct > 90 ? " danger" : pct > 70 ? " warn" : "");
  document.getElementById("usageText").textContent =
    `${used.toLocaleString()} / ${limit.toLocaleString()} characters aaj istemal hue`;
}

async function doLogout() {
  if (TOKEN) {
    try { await fetch(`${API}/logout?token=${encodeURIComponent(TOKEN)}`, { method: "POST" }); } catch (e) {}
  }
  TOKEN = null;
  localStorage.removeItem("mx_token");
  if (jobsPollTimer) clearInterval(jobsPollTimer);
  document.getElementById("userInfo").classList.add("hidden");
  document.getElementById("logoutBtn").classList.add("hidden");
  document.getElementById("mainApp").classList.add("hidden");
  showAuth();
}

// ─── Auth (login/register) ─────────────────────────────────────────────

function switchAuthTab(mode) {
  AUTH_MODE = mode;
  document.getElementById("tabLogin").classList.toggle("active", mode === "login");
  document.getElementById("tabRegister").classList.toggle("active", mode === "register");
  document.getElementById("authSubmit").textContent = mode === "login" ? "Login" : "Register";
  document.getElementById("registerExtra").classList.toggle("hidden", mode !== "register");
  document.getElementById("authMsg").textContent = "";
}

async function submitAuth() {
  const email = document.getElementById("authEmail").value.trim();
  const password = document.getElementById("authPassword").value;
  const msg = document.getElementById("authMsg");
  const btn = document.getElementById("authSubmit");

  if (!email || !password) {
    msg.style.color = "#ff6b6b"; msg.textContent = "Email aur password dono daalo."; return;
  }

  let body = { email, password };
  if (AUTH_MODE === "register") {
    const hgEmail = document.getElementById("hgEmail").value.trim();
    const hgPassword = document.getElementById("hgPassword").value;
    if (!hgEmail || !hgPassword) {
      msg.style.color = "#ff6b6b"; msg.textContent = "HeyGen email/password bhi zaroori hai."; return;
    }
    body.heygen_email = hgEmail;
    body.heygen_password = hgPassword;
  }

  btn.disabled = true;
  btn.textContent = AUTH_MODE === "login" ? "Login ho raha hai..." : "Register ho raha hai...";
  msg.textContent = "";

  try {
    const ep = AUTH_MODE === "login" ? "/login" : "/register";
    const res = await fetch(`${API}${ep}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (AUTH_MODE === "register") {
      if (data.ok) {
        switchAuthTab("login");
        msg.style.color = "#4ade80";
        msg.textContent = "Request bhej di gayi! Admin approval ke baad login karo.";
      } else {
        msg.style.color = "#ff6b6b";
        msg.textContent = data.error || "Register nahi ho paya.";
      }
    } else {
      if (data.ok) {
        TOKEN = data.token;
        localStorage.setItem("mx_token", TOKEN);
        await enterApp(data);
        return;
      }
      msg.style.color = "#ff6b6b";
      msg.textContent = data.error || "Login nahi ho paya.";
    }
  } catch (e) {
    msg.style.color = "#ff6b6b";
    msg.textContent = "Server se connect nahi ho paya.";
  } finally {
    btn.disabled = false;
    btn.textContent = AUTH_MODE === "login" ? "Login" : "Register";
  }
}

// ─── Voices ─────────────────────────────────────────────────────────────

async function loadVoices() {
  const res = await fetch(`${API}/voices?token=${encodeURIComponent(TOKEN)}`);
  const data = await res.json();
  const voices = data.voices || [];

  const listEl = document.getElementById("voiceList");
  const selectEl = document.getElementById("voiceSelect");
  listEl.innerHTML = "";
  selectEl.innerHTML = "";

  if (voices.length === 0) {
    listEl.innerHTML = `<span class="hint">Koi voice nahi bani abhi tak.</span>`;
  }

  voices.forEach((v) => {
    const chip = document.createElement("div");
    chip.className = "voice-chip";
    chip.textContent = v.name;
    listEl.appendChild(chip);

    const opt = document.createElement("option");
    opt.value = v.voice_id;
    opt.textContent = v.name;
    selectEl.appendChild(opt);
  });
}

function openCloneModal() {
  document.getElementById("cloneName").value = "";
  document.getElementById("cloneAudio").value = "";
  document.getElementById("cloneProgress").classList.add("hidden");
  document.getElementById("cloneProgress").innerHTML = "";
  document.getElementById("engineChoice").classList.add("hidden");
  document.getElementById("engineChoice").innerHTML = "";
  const btn = document.getElementById("cloneSubmitBtn");
  btn.disabled = false;
  btn.classList.remove("hidden");
  btn.textContent = "Clone";
  document.getElementById("cloneModal").classList.remove("hidden");
}

function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

async function submitClone() {
  const name = document.getElementById("cloneName").value.trim();
  const audio = document.getElementById("cloneAudio").files[0];
  const progressEl = document.getElementById("cloneProgress");
  const btn = document.getElementById("cloneSubmitBtn");

  if (!name || !audio) { alert("Naam aur audio file dono chahiye."); return; }

  const form = new FormData();
  form.append("token", TOKEN);
  form.append("name", name);
  form.append("audio", audio);
  form.append("remove_background", document.getElementById("cloneRemoveBg").checked);

  btn.disabled = true;
  btn.textContent = "Cloning...";
  progressEl.classList.remove("hidden");
  progressEl.innerHTML = "Shuru ho raha hai...";

  try {
    const res = await fetch(`${API}/voices/clone`, { method: "POST", body: form });
    const data = await res.json();
    if (!data.ok || !data.task_id) {
      progressEl.innerHTML = data.error || "Fail ho gaya.";
      btn.disabled = false;
      btn.textContent = "Clone";
      return;
    }
    pollCloneProgress(data.task_id, progressEl, btn);
  } catch (e) {
    progressEl.innerHTML = "Server se connect nahi ho paya.";
    btn.disabled = false;
    btn.textContent = "Clone";
  }
}

async function pollCloneProgress(taskId, progressEl, btn) {
  const res = await fetch(`${API}/voices/clone/status/${taskId}`);
  const data = await res.json();

  if (data.messages && data.messages.length) {
    progressEl.innerHTML = data.messages.map((m) => `<div>${m}</div>`).join("");
    progressEl.scrollTop = progressEl.scrollHeight;
  }

  if (data.status === "awaiting_engine_choice") {
    progressEl.innerHTML += `<div>✓ Clone ban gayi — ab ek engine choose karo.</div>`;
    btn.classList.add("hidden");
    renderEngineChoice(data.result);
    return;
  }
  if (data.status === "failed") {
    progressEl.innerHTML += `<div style="color:#ff6b6b">✗ ${data.error}</div>`;
    btn.disabled = false;
    btn.textContent = "Clone";
    return;
  }
  setTimeout(() => pollCloneProgress(taskId, progressEl, btn), 2000);
}

function renderEngineChoice(result) {
  const box = document.getElementById("engineChoice");
  box.classList.remove("hidden");
  box.innerHTML = `
    <p class="hint" style="margin-top:10px;">Ek engine choose karo:</p>
    ${result.engines.map((eng) => `
      <div class="engine-row" data-engine="${eng}">
        <span class="engine-name">${eng}</span>
        <button class="small" onclick="finalizeVoice('${result.voice_id}','${eng}','${result.name}')">Use this voice</button>
      </div>
    `).join("")}
  `;
}

async function finalizeVoice(voiceId, engine, name) {
  try {
    const res = await fetch(`${API}/voices/finalize`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: TOKEN, voice_id: voiceId, voice_engine: engine, name }),
    });
    const data = await res.json();
    if (!data.ok) { alert(data.error || "Save nahi ho paya."); return; }
    await loadVoices();
    closeModal("cloneModal");
  } catch (e) {
    alert("Server se connect nahi ho paya.");
  }
}

// ─── Generate ───────────────────────────────────────────────────────────

async function doGenerate() {
  const voiceId = document.getElementById("voiceSelect").value;
  const text = document.getElementById("genText").value.trim();
  const enhanced = document.getElementById("enhancedToggle").checked;
  const msg = document.getElementById("generateMsg");
  const btn = document.getElementById("generateBtn");

  if (!voiceId) { alert("Pehle koi voice clone karo."); return; }
  if (!text) { alert("Text likho."); return; }

  btn.disabled = true;
  btn.textContent = "Submit ho raha hai...";
  msg.style.color = "#22d3ee";
  msg.textContent = "";

  try {
    const res = await fetch(`${API}/submit-job`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: TOKEN, text, voice_id: voiceId, enhanced }),
    });
    const data = await res.json();
    if (!data.ok) {
      msg.style.color = "#ff6b6b";
      msg.textContent = data.error || "Submit nahi ho paya.";
      return;
    }
    msg.style.color = "#4ade80";
    msg.textContent = "Queue mein chala gaya — neeche 'Aapki Requests' mein status dikhega.";
    document.getElementById("genText").value = "";
    renderUsage(data.chars_used_today, data.daily_char_limit);
    await loadJobs();
  } catch (e) {
    msg.style.color = "#ff6b6b";
    msg.textContent = "Server se connect nahi ho paya.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate";
  }
}

// ─── Jobs list ──────────────────────────────────────────────────────────

const STATUS_LABELS = {
  pending: "Queue mein",
  processing: "Ban rahi hai...",
  done: "Ready ✓",
  failed: "Fail ho gaya ✗",
};

async function loadJobs() {
  if (!TOKEN) return;
  try {
    const res = await fetch(`${API}/jobs?token=${encodeURIComponent(TOKEN)}`);
    const data = await res.json();
    if (!data.ok) return;

    const listEl = document.getElementById("jobsList");
    const jobs = data.jobs || [];
    if (jobs.length === 0) {
      listEl.innerHTML = `<span class="hint">Koi request nahi bani abhi tak.</span>`;
      return;
    }

    listEl.innerHTML = jobs.map((j) => {
      const label = STATUS_LABELS[j.status] || j.status;
      const cls = j.status === "done" ? "ok" : j.status === "failed" ? "danger" : "pending";
      const preview = (j.text_preview || "").slice(0, 60);
      const dl = j.status === "done" && j.output_url
        ? `<a href="${j.output_url}" target="_blank" class="job-download">Download</a>` : "";
      const err = j.status === "failed" && j.error_message
        ? `<div class="job-error">${j.error_message}</div>` : "";
      return `
        <div class="job-row">
          <div class="job-info">
            <span class="job-status ${cls}">${label}</span>
            <span class="job-chars">${j.char_count} chars</span>
          </div>
          ${dl}
          ${err}
        </div>`;
    }).join("");
  } catch (e) {}
}
