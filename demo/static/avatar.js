// Avatar : modele VRM rendu par three-vrm (decision D6), ou visage 2D de secours.
//
// Interface commune :
//   avatar.update(dt, visemes, speaking)   a chaque image
//   avatar.setEmotion("joie")              tag emis par le LLM (decision D7)
//   avatar.toggleFraming()                 buste <-> plein pied

// Tags d'emotion du LLM -> expressions VRM standard, avec leur intensite.
const EMOTION_TO_VRM = {
  neutre: {},
  joie: { happy: 0.7 },
  encourageant: { happy: 0.45, relaxed: 0.2 },
  tendresse: { relaxed: 0.7 },
  tristesse: { sad: 0.7 },
  surprise: { surprised: 0.75 },
  agacement: { angry: 0.5 },
  taquin: { happy: 0.4, relaxed: 0.2 },
  pensif: { relaxed: 0.25, sad: 0.1 },
};
const EXPRESSIONS = ["happy", "relaxed", "sad", "surprised", "angry"];
const VISEMES = ["aa", "ih", "ou", "ee", "oh"];

export async function createAvatar(container, vrmUrl, onStatus = () => {}) {
  if (vrmUrl && !window.__forceFaceFallback) {
    try {
      onStatus("chargement du modele 3D…");
      return await createVrmAvatar(container, vrmUrl);
    } catch (err) {
      console.warn("VRM indisponible, visage 2D de secours", err);
      onStatus("modele 3D indisponible, visage 2D");
    }
  }
  return createFaceAvatar(container);
}

// -- VRM -------------------------------------------------------------------------

async function createVrmAvatar(container, url) {
  const THREE = await import("three");
  const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
  const { VRMLoaderPlugin, VRMUtils } = await import("@pixiv/three-vrm");

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(22, 1, 0.05, 30);
  const key = new THREE.DirectionalLight(0xffffff, Math.PI * 0.9);
  key.position.set(0.6, 1.2, 1.5);
  scene.add(key, new THREE.AmbientLight(0xffffff, 0.9));

  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error("fichier sans extension VRM");
  if (VRMUtils.removeUnnecessaryVertices) VRMUtils.removeUnnecessaryVertices(gltf.scene);
  if (VRMUtils.combineSkeletons) VRMUtils.combineSkeletons(gltf.scene);
  VRMUtils.rotateVRM0(vrm); // les VRM 0.x regardent -Z, les 1.0 regardent +Z
  vrm.scene.traverse((o) => { o.frustumCulled = false; });
  scene.add(vrm.scene);

  const bone = (name) => vrm.humanoid && vrm.humanoid.getNormalizedBoneNode(name);
  // Bras le long du corps plutot qu'en T.
  const arms = { leftUpperArm: -1.2, rightUpperArm: 1.2, leftLowerArm: -0.15, rightLowerArm: 0.15 };
  for (const [name, z] of Object.entries(arms)) { const b = bone(name); if (b) b.rotation.z = z; }
  vrm.update(0);
  vrm.scene.updateMatrixWorld(true);

  const headPos = new THREE.Vector3();
  const rawHead = vrm.humanoid.getRawBoneNode("head");
  rawHead.getWorldPosition(headPos);
  const framings = [
    // Le noeud "head" est a la base du crane : on vise un peu au-dessus.
    { pos: [0, 0.07, 1.2], look: [0, 0.05, 0] },                           // buste
    { pos: [0, -headPos.y * 0.46, 4.6], look: [0, -headPos.y * 0.46, 0] }, // plein pied
  ];
  let framing = 0;
  function applyFraming() {
    const f = framings[framing];
    camera.position.set(headPos.x + f.pos[0], headPos.y + f.pos[1], headPos.z + f.pos[2]);
    camera.lookAt(headPos.x + f.look[0], headPos.y + f.look[1], headPos.z + f.look[2]);
  }
  applyFraming();
  try { vrm.lookAt.target = camera; } catch (_) {}

  function resize() {
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  const em = vrm.expressionManager;
  const has = (n) => em && em.getExpression && em.getExpression(n);
  const emotion = { target: {}, value: Object.fromEntries(EXPRESSIONS.map((e) => [e, 0])) };
  let blinkT = 2 + Math.random() * 3, blink = 0, t = 0;
  const head = bone("head"), neck = bone("neck"), spine = bone("spine");

  return {
    kind: "VRM",
    setEmotion(name) { emotion.target = EMOTION_TO_VRM[name] || {}; },
    toggleFraming() { framing = 1 - framing; applyFraming(); return framing ? "plein pied" : "buste"; },
    update(dt, vis, speaking) {
      t += dt;
      for (const e of EXPRESSIONS) {
        let target = emotion.target[e] || 0;
        // "happy" ferme souvent la bouche et les yeux : on l'attenue en parlant.
        if (speaking && (e === "happy" || e === "surprised")) target *= 0.6;
        emotion.value[e] += (target - emotion.value[e]) * (1 - Math.exp(-dt * 4));
        if (has(e)) em.setValue(e, emotion.value[e]);
      }
      for (const v of VISEMES) if (has(v)) em.setValue(v, Math.min(1, vis[v] * 1.1));

      blinkT -= dt;
      if (blinkT <= 0) { blink = 1; blinkT = 2.5 + Math.random() * 3.5; }
      blink = Math.max(0, blink - dt * 7);
      const closed = blink > 0 ? Math.sin(blink * Math.PI) : 0;
      if (has("blink")) em.setValue("blink", Math.min(1, closed * (1 - emotion.value.happy)));

      // Vie au repos : respiration, micro-mouvements, hochements en parlant.
      if (spine) spine.rotation.x = Math.sin(t * 1.6) * 0.012;
      if (neck) neck.rotation.y = Math.sin(t * 0.37) * 0.05;
      if (head) {
        head.rotation.x = Math.sin(t * 0.53) * 0.025 + (speaking ? vis.level * 0.05 : 0);
        head.rotation.z = Math.sin(t * 0.29) * 0.03;
      }
      vrm.update(dt);
      renderer.render(scene, camera);
    },
  };
}

// -- Visage 2D de secours ------------------------------------------------------------

function createFaceAvatar(container) {
  const canvas = document.createElement("canvas");
  container.appendChild(canvas);
  const g = canvas.getContext("2d");
  let W = 1, H = 1, t = 0, blinkT = 2, blink = 0;
  const emo = { smile: 0, brow: 0, eyes: 1, tSmile: 0, tBrow: 0, tEyes: 1 };
  const EMO = {
    neutre: [0.1, 0, 1], joie: [0.9, 0.2, 0.85], encourageant: [0.6, 0.15, 1],
    tendresse: [0.5, -0.1, 0.8], tristesse: [-0.5, -0.5, 0.9], surprise: [0, 0.8, 1.25],
    agacement: [-0.3, -0.7, 0.9], taquin: [0.6, 0.4, 0.8], pensif: [0, 0.3, 0.9],
  };

  function resize() {
    const r = window.devicePixelRatio || 1;
    W = container.clientWidth || 1; H = container.clientHeight || 1;
    canvas.width = W * r; canvas.height = H * r;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    g.setTransform(r, 0, 0, r, 0, 0);
  }
  new ResizeObserver(resize).observe(container);
  resize();

  const css = (name, fallback) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

  return {
    kind: "visage 2D",
    setEmotion(name) { [emo.tSmile, emo.tBrow, emo.tEyes] = EMO[name] || EMO.neutre; },
    toggleFraming() { return "buste"; },
    update(dt, vis, speaking) {
      t += dt;
      const k = 1 - Math.exp(-dt * 5);
      emo.smile += (emo.tSmile - emo.smile) * k;
      emo.brow += (emo.tBrow - emo.brow) * k;
      emo.eyes += (emo.tEyes - emo.eyes) * k;
      blinkT -= dt;
      if (blinkT <= 0) { blink = 1; blinkT = 2.5 + Math.random() * 3.5; }
      blink = Math.max(0, blink - dt * 7);

      const s = Math.min(W, H) / 2.6;
      const cx = W / 2 + Math.sin(t * 0.4) * s * 0.03;
      const cy = H / 2 + Math.sin(t * 1.6) * s * 0.012 + s * 0.08;
      g.clearRect(0, 0, W, H);
      g.save();
      g.translate(cx, cy);
      g.rotate(Math.sin(t * 0.3) * 0.03);

      // cheveux arriere, visage, cheveux avant
      g.fillStyle = css("--hair", "#3b2a24");
      g.beginPath(); g.ellipse(0, -s * 0.12, s * 0.98, s * 1.08, 0, 0, Math.PI * 2); g.fill();
      g.fillRect(-s * 0.98, -s * 0.12, s * 1.96, s * 1.1);
      g.fillStyle = css("--skin", "#f1c7a8");
      g.beginPath(); g.ellipse(0, 0, s * 0.78, s * 0.95, 0, 0, Math.PI * 2); g.fill();
      g.fillStyle = css("--hair", "#3b2a24");
      g.beginPath(); g.ellipse(-s * 0.3, -s * 0.78, s * 0.7, s * 0.32, -0.35, 0, Math.PI * 2); g.fill();
      g.beginPath(); g.ellipse(s * 0.45, -s * 0.75, s * 0.5, s * 0.28, 0.4, 0, Math.PI * 2); g.fill();

      // yeux
      const open = Math.max(0.05, (1 - Math.sin(blink * Math.PI)) * emo.eyes);
      const lookX = Math.sin(t * 0.23) * s * 0.02;
      for (const side of [-1, 1]) {
        const ex = side * s * 0.3, ey = -s * 0.08;
        g.fillStyle = "#fff";
        g.beginPath(); g.ellipse(ex, ey, s * 0.13, s * 0.1 * open, 0, 0, Math.PI * 2); g.fill();
        g.fillStyle = css("--iris", "#4a3326");
        g.beginPath(); g.ellipse(ex + lookX, ey, s * 0.065, s * 0.075 * open, 0, 0, Math.PI * 2); g.fill();
        // sourcils
        g.strokeStyle = css("--hair", "#3b2a24");
        g.lineWidth = s * 0.04; g.lineCap = "round";
        const by = ey - s * 0.2 - emo.brow * s * 0.06;
        g.beginPath();
        g.moveTo(ex - s * 0.13, by + side * emo.brow * -s * 0.02 + (emo.brow < 0 ? -side * s * 0.03 : 0));
        g.lineTo(ex + s * 0.13, by - side * emo.brow * -s * 0.02);
        g.stroke();
      }

      // joues
      g.fillStyle = "rgba(230,120,120,0.18)";
      for (const side of [-1, 1]) {
        g.beginPath(); g.ellipse(side * s * 0.45, s * 0.2, s * 0.12, s * 0.07, 0, 0, Math.PI * 2); g.fill();
      }

      // bouche : ouverture par aa/oh, largeur par ee/ih, arrondi par ou
      const openness = Math.min(1, vis.aa + vis.oh * 0.8 + vis.ou * 0.5 + vis.ee * 0.35 + vis.ih * 0.25);
      const width = s * (0.24 + vis.ee * 0.08 + vis.ih * 0.05 - vis.ou * 0.1 - vis.oh * 0.04 + emo.smile * 0.04);
      const my = s * 0.45;
      g.fillStyle = "#7a2e38";
      g.strokeStyle = "#7a2e38";
      g.lineWidth = s * 0.035;
      if (openness > 0.04) {
        g.beginPath();
        g.ellipse(0, my + openness * s * 0.05, width, s * (0.02 + openness * 0.16), 0, 0, Math.PI * 2);
        g.fill();
        g.fillStyle = "#fff";
        g.fillRect(-width * 0.6, my - s * 0.015 + openness * s * 0.05 - s * (0.02 + openness * 0.16) * 0.7,
                   width * 1.2, s * 0.03 * Math.min(1, openness * 2));
      } else {
        g.beginPath();
        g.moveTo(-width, my - emo.smile * s * 0.05);
        g.quadraticCurveTo(0, my + emo.smile * s * 0.1, width, my - emo.smile * s * 0.05);
        g.stroke();
      }
      g.restore();
    },
  };
}
