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
    ${result.engines.map((eng, i) => `
      <div class="engine-row" data-engine="${eng}">
        <span class="engine-name">${eng}</span>
        <button class="small" id="finalizeBtn_${i}" onclick="finalizeVoice('${result.voice_id}','${eng}','${result.name}', ${i}, ${result.engines.length})">Use this voice</button>
      </div>
    `).join("")}
  `;
}

async function finalizeVoice(voiceId, engine, name, clickedIndex, totalCount) {
  // Jis button pe click hua sirf usi pe "Saving..." — baaki SAARE turant
  // disable, taaki 2 second ki loading-window mein koi aur engine na choose
  // ho sake (race-condition fix).
  for (let i = 0; i < totalCount; i++) {
    const btn = document.getElementById(`finalizeBtn_${i}`);
    if (!btn) continue;
    btn.disabled = true;
    if (i === clickedIndex) btn.textContent = "Saving...";
  }

  try {
    const res = await fetch(`${API}/voices/finalize`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: TOKEN, voice_id: voiceId, voice_engine: engine, name }),
    });
    const data = await res.json();
    if (!data.ok) {
      alert(data.error || "Save nahi ho paya.");
      // Fail hua to sab wapas enable karo, dobara try kar sake
      for (let i = 0; i < totalCount; i++) {
        const btn = document.getElementById(`finalizeBtn_${i}`);
        if (!btn) continue;
        btn.disabled = false;
        if (i === clickedIndex) btn.textContent = "Use this voice";
      }
      return;
    }
    await loadVoices();
    closeModal("cloneModal");
  } catch (e) {
    alert("Server se connect nahi ho paya.");
    for (let i = 0; i < totalCount; i++) {
      const btn = document.getElementById(`finalizeBtn_${i}`);
      if (!btn) continue;
      btn.disabled = false;
      if (i === clickedIndex) btn.textContent = "Use this voice";
    }
  }
}

// ─── Direct Voice presets (exact text, HeyGen UI se) ────────────────────

const DIRECTOR_PRESETS = {
  excited: `Voice: Highly enthusiastic, exuberant, and vibrant, projecting genuine excitement and joyful surprise.
  
Tone: Energetic, uplifting, and celebratory, clearly expressing delight and overwhelming happiness.

Punctuation: Quick, fluid sentences with brief pauses, strategically placed to enhance excitement and emphasize thrilling points.

Delivery: Rapid and lively, with rising pitch and animated intonation, allowing authentic enthusiasm and surprise to prominently resonate throughout.`,

  casual: `Voice: Relaxed, conversational, and authentic, conveying warmth and genuine friendliness.
  
Tone: Casual, approachable, and lightly playful, fostering connection and comfort.

Punctuation: Natural phrasing with varied sentence lengths, incorporating brief pauses and occasional laughter to enhance authenticity and relatability.

Delivery: Varied pitch and rhythm reflecting spontaneous speech patterns, allowing slight emphasis and gentle pacing to maintain listener engagement.

Phrasing: Informal and personable, using everyday language to evoke the sense of a comfortable, friendly conversation.`,

  calm: `Voice: Soft, gentle, and reassuring, conveying warmth and comfort.
  
Tone: Tender, warm, and soothing, designed to instill calm and reassurance.

Punctuation: Fluid, natural phrases with minimal pauses to maintain gentle momentum and continuous flow.

Delivery: Moderately quick, smoothly paced, with consistent softness throughout, subtly emphasizing words that convey care and comfort.`,

  cool: `Voice: Smooth, relaxed, and confidently resonant, projecting a sense of effortless charisma and poise.
  
Tone: Cool, subtly mysterious, and engaging, creating intrigue through nuanced inflection and understated authority.

Punctuation: Leisurely paced, employing deliberate pauses and measured rhythm to enhance the sense of intrigue and ease.

Delivery: Calm and assured, with a gentle, flowing cadence, emphasizing select words to deepen impact and enhance allure.

Phrasing: Effortlessly stylish and slightly enigmatic, crafted to captivate attention and maintain a sophisticated charm.`,

  serious: `Voice: Calm, measured, and authoritative, conveying professionalism and seriousness.
  
Tone: Formal, direct, and informative, with a controlled emotional register.

Punctuation: Deliberate pauses and clear sentence structures, emphasizing precision and clarity.

Delivery: Steady, confident pace, using subtle emphasis to underscore critical information.

Phrasing: Concise and purposeful, prioritizing clarity and impact in communication.`,

  funny: `Voice: Warm, lively, and confident, like someone who enjoys telling a great story and knows the punchline will land. 
Tone: Playful, clever, and inviting, building light anticipation while keeping the mood fun and upbeat. 

Punctuation: Smooth and natural, with pauses before the punchline to create rhythm and let the joke hit clearly. 

Delivery: Relaxed and clear, with slight emphasis or playful tone shifts to highlight the setup and payoff. Avoid overacting — just enough energy to make it entertaining. 

Phrasing: Simple and expressive. Use clear setups and crisp punchlines, and sound like you're having fun sharing the joke.`,

  angry: `Voice: Sharp, tense, and forceful, projecting anger and frustration clearly through intensity and firmness.
  
Tone: Harsh, impatient, and strained, conveying a strong sense of irritation and exasperation.

Punctuation: Short, abrupt sentences punctuated with pronounced pauses, reflecting agitation and urgency.

Delivery: Quickened and intense, emphasizing key words and phrases strongly to express heightened emotional stress.

Phrasing: Direct and accusatory, with rising and falling intonation to underscore irritation and dissatisfaction.`,

  sarcastic: `Tone: Sarcastic, disinterested, and melancholic, with a hint of passive-aggressiveness.
  
Emotion: Apathy mixed with reluctant engagement.

Delivery: Monotone with occasional sighs, drawn-out words, and subtle disdain, evoking a classic emo teenager attitude.`,

  laughing: `Voice: Warm, lively, and genuinely amused, naturally conveying enjoyment and humor.

Tone: Playful, friendly, and engaging, effortlessly evoking a sense of genuine amusement and joy.

Punctuation: Casual and rhythmic, incorporating strategic pauses to allow natural laughter and chuckles to blend smoothly into the delivery.

Delivery: Relaxed and spontaneous, letting soft laughter and gentle chuckles emerge organically at the beginning, middle, or end of sentences.

Phrasing: Lighthearted and expressive, emphasizing phrases with a smile or subtle laugh to convey authentic amusement and contagious cheerfulness.`,

  flirty: `Voice: Playful, confident, and inviting, with a touch of cheekiness, designed to create an alluring presence.
  
Tone: Lighthearted, teasing, and slightly mysterious, giving the impression of fun with just a hint of charm.

Punctuation: Quick, lively sentences with a mix of playful pauses, designed to emphasize teasing moments and build tension.

Delivery: Animated and dynamic, with a gentle rise in pitch toward the end of phrases, adding a flirtatious and inviting tone.

Phrasing: Smooth and confident, with certain words emphasized to evoke an enticing and mischievous energy, making it clear you're enjoying the moment.`,
};

function onModeChange(e) {
  const enhanced = document.getElementById("enhancedToggle");
  const direct = document.getElementById("directVoiceToggle");
  const box = document.getElementById("directVoiceBox");

  // Mutually exclusive — dono ek saath ON nahi rehne chahiye
  const source = e && e.target ? e.target.id : null;
  if (source === "enhancedToggle" && enhanced.checked) direct.checked = false;
  if (source === "directVoiceToggle" && direct.checked) enhanced.checked = false;

  box.classList.toggle("hidden", !direct.checked);
}

function applyPreset(key) {
  document.getElementById("directVoiceText").value = DIRECTOR_PRESETS[key];
  document.querySelectorAll(".preset-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.preset === key);
  });
}

// ─── Generate ───────────────────────────────────────────────────────────

async function doGenerate() {
  const voiceId = document.getElementById("voiceSelect").value;
  const text = document.getElementById("genText").value.trim();
  const enhanced = document.getElementById("enhancedToggle").checked;
  const directOn = document.getElementById("directVoiceToggle").checked;
  const directorStyle = directOn ? document.getElementById("directVoiceText").value.trim() : null;
  const msg = document.getElementById("generateMsg");
  const btn = document.getElementById("generateBtn");

  if (!voiceId) { alert("Pehle koi voice clone karo."); return; }
  if (!text) { alert("Text likho."); return; }
  if (directOn && !directorStyle) { alert("Direct Voice ke liye emotion/style likho ya suggestion choose karo."); return; }

  btn.disabled = true;
  btn.textContent = "Submit ho raha hai...";
  msg.style.color = "#22d3ee";
  msg.textContent = "";

  try {
    const res = await fetch(`${API}/submit-job`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: TOKEN, text, voice_id: voiceId, enhanced, director_style: directorStyle }),
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
