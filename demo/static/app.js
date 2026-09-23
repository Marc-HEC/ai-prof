// Orchestration cote navigateur : un tour = parole -> STT -> LLM (SSE) -> TTS.
//
// L'historique vit ici, pas sur le serveur (decision D2) : il est renvoye a
// chaque tour. L'interruption annule la requete LLM en cours et jette l'audio
// pas encore joue, ce qui "separe un appel d'un chatbot qui parle".

import { Player, LipSync, createListener, encodeWav, audioContext } from "./audio.js";
import { createAvatar } from "./avatar.js";
import { Board } from "./board.js";

const $ = (id) => document.getElementById(id);
const state = {
  config: null,
  messages: [],      // {role, content} ; content = texte, ou parts si image
  materials: [],     // {name, text}
  pendingImages: [], // data URLs a joindre a la prochaine question
  turn: 0,
  abort: null,
  emotion: "neutre",
  listener: null,
  micOn: false,
  times: {},
};

// -- UI helpers ------------------------------------------------------------------

function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 4500);
}

function addMsg(role, text, image) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  if (image) {
    const img = document.createElement("img");
    img.src = image;
    el.appendChild(img);
  }
  $("chat").appendChild(el);
  $("chat").scrollTop = $("chat").scrollHeight;
  return el;
}

function setListen(text, on = false) {
  $("listen").textContent = text;
  $("listen").className = "listen" + (on ? " on" : "");
}

function renderPills(cfg) {
  const s = cfg.status || {};
  const cls = (v) => (v === "pret" ? "ok" : String(v).startsWith("erreur") ? "err" : "warn");
  const pills = [
    ["LLM", cfg.llm, "ok"],
    ["Vision", cfg.vision || "aucune", cfg.vision ? "ok" : "warn"],
    ["STT", `${cfg.stt} · ${s.stt}`, cls(s.stt)],
    ["TTS", `${cfg.tts} · ${s.tts}`, cls(s.tts)],
  ];
  $("pills").innerHTML = "";
  for (const [k, v, c] of pills) {
    const p = document.createElement("span");
    p.className = `pill ${c}`;
    p.textContent = `${k} ${v}`;
    p.title = String(v);
    $("pills").appendChild(p);
  }
  const ready = s.stt === "pret" && s.tts === "pret";
  $("liveDot").classList.toggle("live", ready);
  return ready || String(s.stt).startsWith("erreur") || String(s.tts).startsWith("erreur");
}

function renderMaterials() {
  const box = $("materials");
  box.innerHTML = "";
  const all = [
    ...state.materials.map((m, i) => ({ label: `Cours : ${m.name}`, remove: () => state.materials.splice(i, 1) })),
    ...state.pendingImages.map((_, i) => ({ label: `Image ${i + 1}, jointe à la prochaine question`, remove: () => state.pendingImages.splice(i, 1) })),
  ];
  for (const item of all) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item.label;
    const x = document.createElement("button");
    x.textContent = "×";
    x.title = "Retirer";
    x.onclick = () => { item.remove(); renderMaterials(); };
    chip.appendChild(x);
    box.appendChild(chip);
  }
}

function showTiming() {
  const t = state.times;
  const parts = [];
  if (t.stt != null) parts.push(`STT ${t.stt} ms`);
  if (t.llm != null) parts.push(`LLM 1er token ${t.llm} ms`);
  if (t.firstAudio != null) parts.push(`${t.typed ? "envoi" : "fin de parole"} → 1er son ${t.firstAudio} ms`);
  $("timing").textContent = parts.length ? "Dernier tour : " + parts.join(" · ") : "";
}

// -- Audio, avatar, tableau ----------------------------------------------------------

let player, lipsync, avatar, board;

function setupAudio() {
  player = new Player({
    onSentenceStart: (meta) => { $("caption").textContent = meta.text; state.lastHeard = meta.text; },
    onIdle: () => { $("caption").textContent = ""; updateStop(); },
    onFirstAudio: () => {
      if (state.times.t0) state.times.firstAudio = Math.round(performance.now() - state.times.t0);
      showTiming();
    },
  });
  lipsync = new LipSync(player.analyser);
}

function updateStop() {
  $("stop").disabled = !(player.speaking || state.abort);
}

async function fetchTts(text, emotion) {
  const r = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, emotion }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    toast(err.error || `TTS HTTP ${r.status}`, true);
    throw new Error(err.error || r.status);
  }
  return r.arrayBuffer();
}

function loop() {
  let last = performance.now();
  const frame = (now) => {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const vis = lipsync.compute(dt);
    avatar.update(dt, vis, player.speaking);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// -- Tour de conversation ----------------------------------------------------------

function interrupt(reason = "") {
  const wasActive = player.speaking || state.abort;
  // Le LLM a peut-etre fini d'ecrire, mais l'eleve n'a pas tout entendu : on le
  // note dans l'historique pour que le prof ne croie pas avoir tout dit.
  const last = state.messages[state.messages.length - 1];
  if (player.speaking && !state.abort && last && last.role === "assistant" && state.lastHeard) {
    last.content += ` [l'eleve t'a interrompue pendant : "${state.lastHeard}"]`;
  }
  state.lastHeard = "";
  state.turn += 1;
  if (state.abort) { state.abort.abort(); state.abort = null; }
  player.stop();
  $("caption").textContent = "";
  if (wasActive && reason) addMsg("system", reason);
  updateStop();
}

// Dans l'historique, une image deja envoyee devient une mention textuelle :
// on ne renvoie pas des megaoctets a chaque tour.
function compactHistory() {
  return state.messages.map((m) => {
    if (typeof m.content === "string") return m;
    const text = m.content.filter((p) => p.type === "text").map((p) => p.text).join(" ");
    return { role: m.role, content: text + " [l'eleve t'a montre une image]" };
  });
}

async function ask(text, fromVoice = false) {
  text = text.trim();
  if (!fromVoice) state.times = { t0: performance.now(), typed: true };
  const images = state.pendingImages.splice(0);
  renderMaterials();
  if (!text && !images.length) return;
  if (!text) text = "Regarde ce que je te montre.";
  if (player.speaking || state.abort) interrupt();

  const turn = ++state.turn;
  const history = compactHistory();
  const content = images.length
    ? [{ type: "text", text }, ...images.map((url) => ({ type: "image_url", image_url: { url } }))]
    : text;
  state.messages.push({ role: "user", content });
  addMsg("user", text, images[0]);

  const ctrl = new AbortController();
  state.abort = ctrl;
  updateStop();
  player.beginTurn();
  const bubble = addMsg("assistant", "…");
  let spoken = "";
  let history_text = "";
  let emotion = "neutre";

  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        persona: $("persona").value,
        materials: state.materials,
        messages: [...history, { role: "user", content }],
      }),
      signal: ctrl.signal,
    });
    if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (turn !== state.turn) return;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (!raw.startsWith("data:")) continue;
        const ev = JSON.parse(raw.slice(5));
        if (ev.type === "emotion") {
          emotion = ev.value;
          avatar.setEmotion(emotion);
        } else if (ev.type === "sentence") {
          spoken += (spoken ? " " : "") + ev.value;
          bubble.textContent = spoken;
          player.enqueue(fetchTts(ev.value, emotion), { text: ev.value });
          $("chat").scrollTop = $("chat").scrollHeight;
        } else if (ev.type === "board") {
          board.add(ev.value);
        } else if (ev.type === "timing") {
          state.times.llm = ev.llm_first_token_ms;
          showTiming();
        } else if (ev.type === "warning") {
          toast(ev.message);
        } else if (ev.type === "error") {
          toast("LLM : " + ev.message, true);
          bubble.textContent = spoken || "(erreur, voir le message)";
        } else if (ev.type === "done") {
          history_text = ev.history;
        }
      }
    }
    if (!spoken && !history_text) bubble.textContent = "(pas de réponse)";
    else if (!spoken) bubble.textContent = "(voir le tableau)";
  } catch (err) {
    if (err.name !== "AbortError") {
      toast("Connexion au serveur perdue : " + err.message, true);
      bubble.textContent = spoken || "(erreur)";
    }
  } finally {
    if (state.abort === ctrl) state.abort = null;
    const said = history_text || spoken;
    if (said) {
      const interrupted = turn !== state.turn;
      state.messages.push({
        role: "assistant",
        content: interrupted ? `${spoken} [interrompue par l'eleve]` : said,
      });
    }
    updateStop();
  }
}

// -- Micro --------------------------------------------------------------------------

async function toggleMic() {
  audioContext(); // debloque l'audio sur geste utilisateur
  if (state.micOn) {
    state.listener && state.listener.pause();
    state.micOn = false;
    $("mic").textContent = "Activer le micro";
    $("mic").setAttribute("aria-pressed", "false");
    setListen("");
    return;
  }
  try {
    if (!state.listener) {
      setListen("chargement du VAD…");
      state.listener = await createListener({
        onSpeechStart: () => {
          setListen("tu parles…", true);
          if ($("bargein").checked && (player.speaking || state.abort)) interrupt();
        },
        onMisfire: () => setListen("à l'écoute", true),
        onSpeechEnd: onSpeechEnd,
      });
    }
    await state.listener.start();
    state.micOn = true;
    $("mic").textContent = "Couper le micro";
    $("mic").setAttribute("aria-pressed", "true");
    setListen(`à l'écoute · ${state.listener.kind}`, true);
  } catch (err) {
    console.error(err);
    setListen("");
    toast("Micro indisponible : " + err.message, true);
  }
}

async function onSpeechEnd(samples) {
  // Sans interruption vocale, ce qui est dit pendant que le prof parle est ignore.
  if (!$("bargein").checked && player.speaking) return setListen("à l'écoute", true);
  state.times = { t0: performance.now() };
  setListen("transcription…", true);
  try {
    const r = await fetch("/api/stt", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: encodeWav(samples) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.status);
    state.times.stt = data.ms;
    showTiming();
    setListen("à l'écoute", true);
    if (data.text) ask(data.text, true);
  } catch (err) {
    setListen("à l'écoute", true);
    toast(err.message, true);
  }
}

// -- Cours et tableau --------------------------------------------------------------

async function uploadMaterial(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/material", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.status);
    if (data.kind === "image") {
      state.pendingImages.push(data.dataUrl);
      toast(state.config.vision ? "Image jointe à ta prochaine question." : "Image jointe, mais aucun modèle vision n'est configuré.");
    } else {
      state.materials.push({ name: data.name, text: data.text });
      toast(`Cours ajouté : ${data.name} (${data.text.length} caractères)`);
      addMsg("system", `Cours ajouté : ${data.name}`);
    }
    renderMaterials();
  } catch (err) {
    toast("Cours : " + err.message, true);
  }
}

// -- Demarrage ---------------------------------------------------------------------

async function pollConfig() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    state.config = cfg;
    if (!renderPills(cfg)) setTimeout(pollConfig, 1500);
  } catch (_) {
    setTimeout(pollConfig, 3000);
  }
}

async function main() {
  state.config = await (await fetch("/api/config")).json();
  renderPills(state.config);
  for (const p of state.config.personas) {
    const o = document.createElement("option");
    o.value = p; o.textContent = p;
    if (p === state.config.persona) o.selected = true;
    $("persona").appendChild(o);
  }
  pollConfig();

  setupAudio();
  board = new Board({ content: $("boardContent"), canvas: $("drawLayer"), surface: $("boardSurface") });
  board.clearWriting();
  avatar = await createAvatar($("avatar"), state.config.avatarUrl, (s) => ($("avatarStatus").textContent = s));
  $("avatarStatus").textContent = avatar.kind;
  setTimeout(() => ($("avatarStatus").textContent = ""), 4000);
  loop();

  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    audioContext();
    const v = $("text").value;
    $("text").value = "";
    ask(v);
  });
  $("mic").onclick = toggleMic;
  $("stop").onclick = () => interrupt("Tu as coupé la parole.");
  $("framing").onclick = () => toast("Cadrage : " + avatar.toggleFraming());
  $("file").onchange = (e) => { for (const f of e.target.files) uploadMaterial(f); e.target.value = ""; };
  document.querySelectorAll(".tool").forEach((b) => {
    b.onclick = () => {
      board.setTool(b.dataset.tool);
      document.querySelectorAll(".tool").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    };
  });
  $("clearDrawing").onclick = () => board.clearDrawing();
  $("clearBoard").onclick = () => board.clearWriting();
  $("showBoard").onclick = () => {
    const snap = board.snapshot();
    if (!snap) return toast("Dessine d'abord avec le stylo.");
    state.pendingImages.push(snap);
    renderMaterials();
    toast("Dessin joint : pose ta question (à l'oral ou à l'écrit).");
  };

  // Expose pour les tests automatises.
  window.__demo = { state, ask, interrupt, get player() { return player; }, get avatar() { return avatar; } };
}

main().catch((err) => {
  console.error(err);
  toast("Démarrage impossible : " + err.message, true);
});
