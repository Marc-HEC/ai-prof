// Relie l'avatar, l'audio et le chat au serveur local (WebSocket).
import { Avatar } from "./avatar.js";
import { Microphone, VoicePlayer, fileToPcmBase64, imageToDataUrl } from "./audio.js";

const $ = (id) => document.getElementById(id);
const avatar = new Avatar($("scene"));
window.avatar = avatar; // pratique pour tester dans la console
let charName = "Elle";
let currentReply = null;

const STATUS = { idle: "En ligne", listening: "T'écoute…", thinking: "Réfléchit…", speaking: "Parle", offline: "Hors ligne" };
function setStatus(state) {
  $("status").dataset.state = state;
  $("status").textContent = STATUS[state];
}

const player = new VoicePlayer((lvl) => avatar.setMouth(lvl), () => setStatus("idle"));

avatar.load("avatar/model.vrm").catch(() => { $("avatar-missing").hidden = false; });

// --- chat ---------------------------------------------------------------------
function addMessage(kind, who, text, image) {
  const li = document.createElement("li");
  li.className = kind;
  if (who) {
    const w = document.createElement("span");
    w.className = "who";
    w.textContent = who;
    li.append(w);
  }
  const body = document.createElement("span");
  body.textContent = text;
  li.append(body);
  if (image) {
    const img = document.createElement("img");
    img.src = image;
    img.alt = "Image envoyée";
    li.append(img);
  }
  $("messages").append(li);
  li.scrollIntoView({ block: "end" });
  return { li, body };
}

// --- connexion -----------------------------------------------------------------
let ws;
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => setStatus("idle");
  ws.onclose = () => { setStatus("offline"); setTimeout(connect, 2000); };
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) {
      setStatus("speaking");
      player.enqueue(e.data);
      return;
    }
    handle(JSON.parse(e.data));
  };
}

function handle(m) {
  switch (m.type) {
    case "state":
      if (m.name) { charName = m.name; $("name").textContent = m.name; }
      $("mood").textContent = `humeur : ${m.state.humeur}`;
      break;
    case "user":
      if (m.source === "voix") addMessage("user", "Toi", m.text);
      setStatus("thinking");
      break;
    case "vad":
      if (m.state === "start" && !player.playing) setStatus("listening");
      if (m.state === "thinking") setStatus("thinking");
      if (m.state === "idle" && !player.playing) setStatus("idle");
      break;
    case "tag":
      if (m.kind === "emotion") avatar.setEmotion(m.value);
      if (m.kind === "action") avatar.play(m.value);
      if (m.kind === "camera") setCamera(m.value);
      break;
    case "assistant_delta":
      if (!currentReply) currentReply = addMessage("assistant", charName, "");
      currentReply.body.textContent += (currentReply.body.textContent ? " " : "") + m.text;
      currentReply.li.scrollIntoView({ block: "end" });
      break;
    case "assistant_end":
      currentReply = null;
      if (!player.playing) setStatus("idle");
      break;
    case "stop":
      player.stop();
      currentReply?.li.classList.add("cut");
      currentReply = null;
      setStatus("listening");
      break;
    case "error":
      addMessage("system", null, m.message);
      break;
  }
}

function send(obj) { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj)); }

// --- contrôles -------------------------------------------------------------------
const mic = new Microphone((frame) => { if (ws?.readyState === WebSocket.OPEN) ws.send(frame); });
$("mic").onclick = async () => {
  if (mic.active) {
    mic.stop();
  } else {
    try { await mic.start(); } catch (e) { addMessage("system", null, `Micro indisponible : ${e.message}`); }
  }
  $("mic").setAttribute("aria-pressed", String(mic.active));
  $("mic").textContent = mic.active ? "Couper le micro" : "Activer le micro";
};

$("stop").onclick = () => { player.stop(); send({ type: "interrupt" }); };

function setCamera(mode) {
  avatar.setCamera(mode);
  $("cam").textContent = mode === "buste" ? "Plein pied" : "Vue buste";
}
$("cam").onclick = () => setCamera(avatar.cameraMode === "buste" ? "plein_pied" : "buste");

$("composer").onsubmit = (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  player.stop();
  addMessage("user", "Toi", text);
  send({ type: "text", text });
  $("text").value = "";
};

$("image-input").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const url = await imageToDataUrl(file);
  const caption = $("text").value.trim();
  $("text").value = "";
  player.stop();
  addMessage("user", "Toi", caption, url);
  send({ type: "image", text: caption, images: [url] });
  e.target.value = "";
};

$("audio-input").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    player.stop();
    send({ type: "voice_note", pcm: await fileToPcmBase64(file) });
    addMessage("system", null, "Message vocal envoyé, transcription…");
  } catch (err) {
    addMessage("system", null, `Fichier audio illisible : ${err.message}`);
  }
  e.target.value = "";
};

connect();
