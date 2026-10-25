// Avatar 2D anime : calques PNG dont la bouche suit le volume de la voix, comme un VTuber « PNG ».
// Même interface que avatar.js (load, setMouth, setEmotion, play, setCamera), donc app.js
// choisit l'un ou l'autre sans rien changer d'autre.
//
// Fichiers attendus dans frontend/avatar/2d/, tous de la même taille, fond transparent :
//   base.png               le personnage, bouche fermée
//   bouche_0.png … bouche_3.png   de fermée à grande ouverte
//   yeux_fermes.png        paupières, pour le clignement
//   emotion_<nom>.png      facultatif : yeux et sourcils pour joie, surprise, triste…
// Sans base.png, un visage provisoire est dessiné pour que tout marche dès le premier lancement.

const EMOTIONS = ["joie", "tendresse", "taquine", "surprise", "triste", "agacee", "neutre", "pensif"];

function loadImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

export class Avatar {
  constructor(canvas) {
    this.canvas = canvas;
    this.g = canvas.getContext("2d");
    this.cameraMode = "buste";
    this.mouth = 0;
    this.mouthTarget = 0;
    this.emotion = "neutre";
    this.emotionUntil = 0;
    this.blinkUntil = 0;
    this.nextBlink = performance.now() + 2500;
    this.gesture = null; // { name, start }
    this.layers = null;
    // La colonne change de largeur quand le tableau apparaît ou en mode visio, pas
    // seulement quand la fenêtre change : on suit donc le conteneur lui-même.
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    const loop = (t) => { this.draw(t); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  async load(dir) {
    const base = await loadImage(`${dir}base.png`);
    if (!base) return; // visage provisoire
    const mouths = await Promise.all([0, 1, 2, 3].map((i) => loadImage(`${dir}bouche_${i}.png`)));
    const emotions = {};
    await Promise.all(EMOTIONS.map(async (e) => { emotions[e] = await loadImage(`${dir}emotion_${e}.png`); }));
    this.layers = { base, mouths, eyesClosed: await loadImage(`${dir}yeux_fermes.png`), emotions };
  }

  resize() {
    const el = this.canvas.parentElement;
    const ratio = Math.min(devicePixelRatio, 2);
    this.canvas.width = el.clientWidth * ratio;
    this.canvas.height = el.clientHeight * ratio;
  }

  setMouth(level) { this.mouthTarget = Math.min(1, level); }

  setEmotion(name) {
    this.emotion = EMOTIONS.includes(name) ? name : "neutre";
    this.emotionUntil = performance.now() + 6000;
  }

  play(name) { this.gesture = { name, start: performance.now() }; }

  setCamera(mode) { this.cameraMode = mode; } // une seule vue en 2D

  // Petits mouvements de tête : décalage (dx, dy) et rotation, selon le geste en cours.
  motion(t) {
    let dx = 0, dy = Math.sin(t / 900) * 0.004, rot = Math.sin(t / 2300) * 0.01;
    if (this.gesture) {
      const k = (t - this.gesture.start) / 1000;
      if (k > 1.6) this.gesture = null;
      else if (this.gesture.name === "hoche") dy += Math.sin(k * 12) * 0.012 * (1 - k / 1.6);
      else if (this.gesture.name === "rire") dy += Math.abs(Math.sin(k * 18)) * 0.01 * (1 - k / 1.6);
      else if (this.gesture.name === "reflechit") { rot += 0.05 * Math.min(1, k * 3); dx += 0.01; }
      else if (this.gesture.name === "salut" || this.gesture.name === "danse") rot += Math.sin(k * 8) * 0.04;
    }
    return { dx, dy, rot };
  }

  draw(t) {
    const g = this.g, W = this.canvas.width, H = this.canvas.height;
    this.mouth += (this.mouthTarget - this.mouth) * 0.5;
    if (t > this.nextBlink) { this.blinkUntil = t + 120; this.nextBlink = t + 2500 + Math.random() * 3000; }
    if (this.emotion !== "neutre" && t > this.emotionUntil) this.emotion = "neutre";
    const blinking = t < this.blinkUntil;
    const { dx, dy, rot } = this.motion(t);

    g.clearRect(0, 0, W, H);
    g.save();
    const size = Math.min(W, H) * 0.95;
    g.translate(W / 2 + dx * size, H - size * 0.02 + dy * size);
    g.rotate(rot);
    if (this.layers) this.drawSprites(g, size, blinking);
    else this.drawPlaceholder(g, size, blinking);
    g.restore();
  }

  drawSprites(g, size, blinking) {
    const { base, mouths, eyesClosed, emotions } = this.layers;
    const w = size * (base.width / Math.max(base.width, base.height));
    const h = size * (base.height / Math.max(base.width, base.height));
    const at = (img) => img && g.drawImage(img, -w / 2, -h, w, h);
    at(base);
    at(emotions[this.emotion]);
    const index = this.mouth < 0.08 ? 0 : this.mouth < 0.3 ? 1 : this.mouth < 0.6 ? 2 : 3;
    at(mouths[index] ?? mouths.find(Boolean));
    if (blinking) at(eyesClosed);
  }

  // Visage provisoire, dessiné au trait : juste assez pour vérifier lip-sync et émotions.
  drawPlaceholder(g, s, blinking) {
    const skin = "#ffe3d3", hair = "#3b3f6b", line = "#2a2340";
    g.lineWidth = s * 0.006; g.strokeStyle = line; g.lineJoin = "round";
    // cheveux arrière et épaules
    g.fillStyle = hair;
    g.beginPath(); g.ellipse(0, -s * 0.52, s * 0.3, s * 0.36, 0, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#5b6bd6";
    g.beginPath(); g.ellipse(0, -s * 0.02, s * 0.36, s * 0.16, 0, Math.PI, 0); g.fill();
    // visage
    g.fillStyle = skin;
    g.beginPath();
    g.moveTo(-s * 0.21, -s * 0.62);
    g.quadraticCurveTo(-s * 0.23, -s * 0.36, 0, -s * 0.27);
    g.quadraticCurveTo(s * 0.23, -s * 0.36, s * 0.21, -s * 0.62);
    g.closePath(); g.fill(); g.stroke();
    // frange
    g.fillStyle = hair;
    g.beginPath();
    g.moveTo(-s * 0.24, -s * 0.6);
    g.quadraticCurveTo(-s * 0.1, -s * 0.95, s * 0.24, -s * 0.62);
    g.lineTo(s * 0.12, -s * 0.66); g.lineTo(s * 0.04, -s * 0.6); g.lineTo(-s * 0.06, -s * 0.67); g.lineTo(-s * 0.16, -s * 0.6);
    g.closePath(); g.fill();
    // yeux
    const e = this.emotion;
    for (const side of [-1, 1]) {
      const x = side * s * 0.085, y = -s * 0.5;
      if (blinking || e === "tendresse") {
        g.beginPath(); g.arc(x, y, s * 0.035, Math.PI * 0.15, Math.PI * 0.85); g.stroke();
      } else {
        const open = e === "surprise" ? 1.25 : e === "joie" || e === "taquine" ? 0.8 : 1;
        g.fillStyle = "#fff";
        g.beginPath(); g.ellipse(x, y, s * 0.04, s * 0.05 * open, 0, 0, Math.PI * 2); g.fill(); g.stroke();
        g.fillStyle = "#4f5bd5";
        g.beginPath(); g.ellipse(x, y + s * 0.006, s * 0.027, s * 0.038 * open, 0, 0, Math.PI * 2); g.fill();
        g.fillStyle = "#fff";
        g.beginPath(); g.arc(x - s * 0.01, y - s * 0.012, s * 0.009, 0, Math.PI * 2); g.fill();
      }
      // sourcils selon l'émotion
      const tilt = e === "agacee" ? -0.35 : e === "triste" || e === "pensif" ? 0.3 : e === "surprise" ? 0 : 0.1;
      g.beginPath();
      g.moveTo(x - s * 0.04, -s * 0.575 + side * tilt * s * 0.02 - (e === "surprise" ? s * 0.015 : 0));
      g.lineTo(x + s * 0.04, -s * 0.575 - side * tilt * s * 0.02 - (e === "surprise" ? s * 0.015 : 0));
      g.stroke();
    }
    // joues
    if (["joie", "tendresse", "taquine"].includes(e)) {
      g.fillStyle = "rgba(255,120,140,.35)";
      for (const side of [-1, 1]) { g.beginPath(); g.ellipse(side * s * 0.12, -s * 0.43, s * 0.03, s * 0.015, 0, 0, Math.PI * 2); g.fill(); }
    }
    // bouche : s'ouvre avec la voix
    const open = this.mouth * s * 0.045;
    g.fillStyle = "#b8455c";
    g.beginPath();
    if (open < s * 0.004) {
      const smile = e === "triste" ? -1 : 1;
      g.moveTo(-s * 0.03, -s * 0.37); g.quadraticCurveTo(0, -s * 0.37 + smile * s * 0.015, s * 0.03, -s * 0.37); g.stroke();
    } else {
      g.ellipse(0, -s * 0.365, s * 0.028, open, 0, 0, Math.PI * 2); g.fill(); g.stroke();
    }
  }
}
