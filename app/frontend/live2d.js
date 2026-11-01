// Avatar Live2D : un vrai modèle 2D anime articulé (comme les VTubers), qui respire, cligne,
// bouge la tête et parle avec la voix. Même interface que avatar.js et avatar2d.js.
//
// À déposer dans frontend/avatar/live2d/ :
//   live2dcubismcore.min.js     le moteur Cubism, à télécharger chez Live2D (licence propriétaire,
//                               il n'est pas fourni avec l'app)
//   <dossier du modèle>/        le modèle exporté de Cubism Editor ou téléchargé : *.model3.json,
//                               *.moc3, textures, et si possible physics3, motions, expressions
// Le serveur trouve le .model3.json tout seul (voir /api/info).
//
// Lip-sync : ParamMouthOpenY (ouverture) et ParamMouthForm (étirée ou ronde) suivent lipsync.js.
// Émotions : une expression du modèle du même nom (joie, triste…) si elle existe, sinon les
// paramètres standards de Cubism (sourcils, yeux souriants, joues).
// Gestes : un groupe de motions du même nom (salut, hoche…) s'il existe, sinon un mouvement de tête.

const SCRIPTS = [
  "avatar/live2d/live2dcubismcore.min.js",
  "node_modules/pixi.js/dist/browser/pixi.min.js",
  "node_modules/pixi-live2d-display/dist/cubism4.min.js",
];

// Valeurs visées par émotion, sur les paramètres standards (ignorés si le modèle ne les a pas).
const EMOTION_PARAMS = {
  neutre: {},
  joie: { ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamCheek: 1, ParamBrowLY: 0.4, ParamBrowRY: 0.4, ParamMouthForm: 1 },
  tendresse: { ParamEyeLSmile: 0.6, ParamEyeRSmile: 0.6, ParamCheek: 0.8, ParamMouthForm: 0.6, ParamAngleZ: 6 },
  taquine: { ParamEyeLSmile: 0.5, ParamBrowLY: 0.3, ParamBrowRY: -0.2, ParamMouthForm: 0.8, ParamAngleZ: -8 },
  surprise: { ParamBrowLY: 1, ParamBrowRY: 1, ParamEyeLOpen: 1.2, ParamEyeROpen: 1.2, ParamMouthForm: 0 },
  triste: { ParamBrowLY: -0.6, ParamBrowRY: -0.6, ParamBrowLForm: -1, ParamBrowRForm: -1, ParamMouthForm: -0.8, ParamAngleY: -8 },
  agacee: { ParamBrowLAngle: -1, ParamBrowRAngle: -1, ParamBrowLForm: -1, ParamBrowRForm: -1, ParamMouthForm: -0.5 },
  pensif: { ParamBrowLY: -0.2, ParamEyeBallX: 0.5, ParamEyeBallY: 0.6, ParamAngleX: 10, ParamAngleZ: 6 },
};
const EMOTION_IDS = [...new Set(Object.values(EMOTION_PARAMS).flatMap(Object.keys))];

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`introuvable : ${src}`));
    document.head.append(s);
  });
}

export class Avatar {
  constructor(canvas) {
    this.canvas = canvas;
    this.cameraMode = "buste";
    this.mouth = 0;
    this.mouthTarget = 0;
    this.form = 0;
    this.emotion = "neutre";
    this.emotionUntil = 0;
    this.current = {}; // valeurs d'émotion lissées, par paramètre
    this.gesture = null;
  }

  async load(url) {
    for (const src of SCRIPTS) await loadScript(src);
    const PIXI = window.PIXI;
    PIXI.live2d.Live2DModel.registerTicker(PIXI.Ticker);
    this.app = new PIXI.Application({ view: this.canvas, backgroundAlpha: 0, antialias: true,
                                      resolution: Math.min(devicePixelRatio, 2), autoDensity: true });
    this.model = await PIXI.live2d.Live2DModel.from(url, { autoInteract: false });
    this.app.stage.addChild(this.model);
    const im = this.model.internalModel;
    this.core = im.coreModel;
    this.has = (id) => this.core.getParameterIndex(id) >= 0;
    this.expressions = new Set((im.settings.expressions ?? []).map((e) => e.Name ?? e.name));
    this.motions = new Set(Object.keys(im.settings.motions ?? {}));
    // Après les motions et avant le rendu : nos valeurs passent par-dessus l'animation d'attente.
    im.on("beforeModelUpdate", () => this.apply(performance.now()));
    this.size = { w: this.model.width, h: this.model.height };
    new ResizeObserver(() => this.layout()).observe(this.canvas.parentElement);
    this.layout();
  }

  layout() {
    if (!this.model) return;
    const el = this.canvas.parentElement, W = el.clientWidth, H = el.clientHeight;
    this.app.renderer.resize(W, H);
    const fit = Math.min(W / this.size.w, H / this.size.h) * 0.95;
    const buste = this.cameraMode === "buste";
    const scale = fit * (buste ? 2.4 : 1); // buste : tête et épaules
    this.model.scale.set(scale);
    this.model.anchor?.set(0.5, 0);
    this.model.x = W / 2;
    this.model.y = buste ? -this.size.h * scale * 0.05 : (H - this.size.h * scale) / 2;
  }

  setMouth(level, shape) {
    this.mouthTarget = Math.min(1, shape ? shape.open * 1.6 : level);
    // ParamMouthForm : 1 étirée (i, e), -1 ronde (o, u)
    if (shape && level > 0.06) this.formTarget = Math.max(-1, Math.min(1, (shape.width - 0.9) * 3));
  }

  setEmotion(name) {
    this.emotion = name in EMOTION_PARAMS ? name : "neutre";
    this.emotionUntil = performance.now() + 6000;
    if (this.expressions?.has(this.emotion)) this.model.expression(this.emotion);
  }

  play(name) {
    if (this.motions?.has(name)) this.model.motion(name);
    else this.gesture = { name, start: performance.now() };
  }

  setCamera(mode) { this.cameraMode = mode; this.layout(); }

  set(id, value) { if (this.has(id)) this.core.setParameterValueById(id, value); }
  add(id, value) { if (this.has(id)) this.core.addParameterValueById(id, value); }

  apply(t) {
    if (this.emotion !== "neutre" && t > this.emotionUntil) this.emotion = "neutre";
    // Émotion : seulement si le modèle n'a pas d'expression de ce nom.
    const target = this.expressions.has(this.emotion) ? {} : EMOTION_PARAMS[this.emotion];
    for (const id of EMOTION_IDS) {
      const cur = this.current[id] ?? 0;
      this.current[id] = cur + ((target[id] ?? 0) - cur) * 0.08;
      if (Math.abs(this.current[id]) < 0.01 || !this.has(id)) continue;
      if (id.startsWith("ParamEye") && id.endsWith("Open")) continue; // laissé au clignement
      this.add(id, this.current[id]);
    }
    // Bouche : on remplace l'animation, sinon une motion d'attente garderait la bouche fermée.
    this.mouth += (this.mouthTarget - this.mouth) * 0.5;
    this.form += ((this.mouth > 0.05 ? this.formTarget ?? 0 : 0) - this.form) * 0.3;
    this.set("ParamMouthOpenY", this.mouth);
    if (this.mouth > 0.05) this.set("ParamMouthForm", this.form + (this.current.ParamMouthForm ?? 0) * 0.5);
    // Gestes procéduraux, en degrés sur la tête et le buste.
    if (this.gesture) {
      const k = (t - this.gesture.start) / 1000, fade = 1 - k / 1.6;
      if (k > 1.6) this.gesture = null;
      else if (this.gesture.name === "hoche") this.add("ParamAngleY", Math.sin(k * 12) * 15 * fade);
      else if (this.gesture.name === "rire") { this.add("ParamAngleY", Math.abs(Math.sin(k * 18)) * 8 * fade); this.add("ParamBodyAngleZ", Math.sin(k * 9) * 4 * fade); }
      else if (this.gesture.name === "reflechit") { this.add("ParamAngleZ", 12 * Math.min(1, k * 3)); this.add("ParamEyeBallY", 0.7); }
      else if (this.gesture.name === "salut" || this.gesture.name === "danse") { this.add("ParamAngleZ", Math.sin(k * 8) * 12 * fade); this.add("ParamBodyAngleX", Math.sin(k * 8) * 6 * fade); }
    }
  }
}
