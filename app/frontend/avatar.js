// Avatar VRM : idle (respiration, clignements, regard), lip-sync, émotions, caméra, gestes du
// corps entier, déplacements et tenues.
//
// Gestes : animations/<nom>.vrma ou animations/<nom>.fbx (Mixamo) s'il existe, sinon un geste
// procédural. animations/repos.(vrma|fbx), s'il existe, tourne en boucle quand elle ne fait rien.
// Tenues : un VRM par tenue dans avatar/tenues/<nom>.vrm, chargé à la demande et gardé en cache.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from "@pixiv/three-vrm-animation";
import { loadMixamoClip } from "./mixamo.js";

const EMOTIONS = { joie: "happy", tendresse: "relaxed", taquine: "happy", surprise: "surprised",
                   triste: "sad", agacee: "angry", neutre: null };
// Durée des gestes procéduraux, en secondes.
const DURATIONS = { salut: 2, hoche: 1.2, rire: 1.5, reflechit: 2.5, danse: 8, etirement: 3, marche: 6,
                    pointe: 2.5, explique: 3, applaudit: 2, s_incline: 2, bras_croises: 4, saute: 1.4, tourne: 2.4 };
// Gestes où l'on voit les jambes : on passe en plein pied.
const FULL_BODY = new Set(["danse", "marche", "saute", "tourne", "s_incline"]);
const LOOPING = new Set(["danse", "marche"]);
// Cadrages calculés à partir de la hauteur de la tête, pour s'adapter à la taille de l'avatar.
function framing(headY) {
  return {
    buste: { pos: new THREE.Vector3(0, headY * 0.97, headY * 1.0), target: new THREE.Vector3(0, headY * 0.9, 0) },
    plein_pied: { pos: new THREE.Vector3(0, headY * 0.7, headY * 2.7), target: new THREE.Vector3(0, headY * 0.52, 0) },
  };
}
let CAMERAS = framing(1.4);
const ARM_DOWN = 1.2; // inverse le signe si les bras montent au lieu de descendre

export class Avatar {
  constructor(canvas) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.1, 50);
    this.camTarget = CAMERAS.buste.target.clone();
    this.camera.position.copy(CAMERAS.buste.pos);
    this.cameraMode = "buste";
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x444466, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(1, 2, 2);
    this.scene.add(key);
    this.clock = new THREE.Clock();
    this.mouth = 0;
    this.mouthTarget = 0;
    this.emotion = null;
    this.emotionUntil = 0;
    this.nextBlink = 2;
    this.gesture = null; // { name, start, duration }
    this.mixer = null;
    this.clips = new Map();
    this.outfits = new Map(); // nom -> vrm déjà chargé
    this.outfit = null;
    this.walk = null; // déplacement en cours : { start, duration }
    this.root = new THREE.Group(); // porte l'avatar, se déplace pendant la marche
    this.scene.add(this.root);
    this.loader = new GLTFLoader();
    this.loader.register((p) => new VRMLoaderPlugin(p));
    this.loader.register((p) => new VRMAnimationLoaderPlugin(p));
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this.renderer.setAnimationLoop(() => this.tick());
  }

  async fetchVrm(url) {
    const gltf = await this.loader.loadAsync(url);
    const vrm = gltf.userData.vrm;
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons?.(gltf.scene);
    VRMUtils.rotateVRM0(vrm);
    return vrm;
  }

  async load(url) { this.attach(await this.fetchVrm(url)); }

  // Change de tenue : un autre VRM du même personnage, à la même place et dans la même pose.
  async setOutfit(name) {
    if (name === this.outfit) return;
    let vrm = this.outfits.get(name);
    if (!vrm) {
      vrm = await this.fetchVrm(`avatar/tenues/${name}.vrm`);
      this.outfits.set(name, vrm);
    }
    this.outfit = name;
    this.attach(vrm);
  }

  attach(vrm) {
    if (this.vrm) this.root.remove(this.vrm.scene);
    this.mixer?.stopAllAction();
    this.root.add(vrm.scene);
    if (vrm.lookAt) vrm.lookAt.target = this.camera;
    vrm.scene.updateMatrixWorld(true);
    const head = vrm.humanoid.getRawBoneNode("head").getWorldPosition(new THREE.Vector3());
    CAMERAS = framing(head.y - this.root.position.y + 0.05);
    this.vrm = vrm;
    this.hipsY = vrm.humanoid.getNormalizedBoneNode("hips").position.y;
    this.mixer = new THREE.AnimationMixer(vrm.scene);
    this.mixer.addEventListener("finished", () => this.idleAction?.reset().fadeIn(0.4).play());
    this.clips = new Map(); // les clips sont liés aux os d'un VRM précis
    this.idleAction = null;
    this.startIdle();
  }

  async startIdle() {
    const vrm = this.vrm;
    const clip = await this.clip("repos");
    if (!clip || vrm !== this.vrm) return;
    this.idleAction = this.mixer.clipAction(clip).setLoop(THREE.LoopRepeat, Infinity);
    this.idleAction.fadeIn(0.5).play();
  }

  resize() {
    const el = this.renderer.domElement.parentElement;
    const w = el.clientWidth, h = el.clientHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // shape : voyelle estimée par lipsync.js, pour les visèmes VRM aa, ee, ih, oh, ou.
  setMouth(level, shape) {
    this.mouthTarget = Math.min(1, level);
    if (shape) this.vowels = shape.weights;
  }

  setEmotion(name) {
    this.emotion = EMOTIONS[name] ?? null;
    this.emotionUntil = this.clock.elapsedTime + 5;
  }

  setCamera(mode) { if (CAMERAS[mode]) this.cameraMode = mode; }

  // Geste : fichier animations/<nom>.vrma ou .fbx s'il existe, sinon geste procédural de secours.
  async play(name) {
    if (name === "se_lever") return this.setCamera("plein_pied");
    if (name === "s_asseoir") return this.setCamera("buste");
    if (!this.vrm) return;
    if (FULL_BODY.has(name)) this.setCamera("plein_pied");
    const t = this.clock.elapsedTime;
    if (name === "marche") this.walk = { start: t, duration: DURATIONS.marche };
    const vrm = this.vrm;
    const clip = await this.clip(name);
    if (vrm !== this.vrm) return; // tenue changée pendant le chargement
    if (clip) {
      const action = this.mixer.clipAction(clip);
      const loops = LOOPING.has(name) ? Math.max(1, Math.round(DURATIONS[name] / clip.duration)) : 1;
      action.reset().setLoop(loops > 1 ? THREE.LoopRepeat : THREE.LoopOnce, loops);
      action.clampWhenFinished = false;
      this.idleAction?.fadeOut(0.3);
      action.fadeIn(0.3).play();
      return;
    }
    this.idleAction?.fadeOut(0.25);
    this.gesture = { name, start: this.clock.elapsedTime, duration: DURATIONS[name] ?? 1.5 };
  }

  // animations : { nom: fichier } donné par le serveur (/api/info), pour ne demander que ce qui existe.
  async clip(name) {
    if (this.clips.has(name)) return this.clips.get(name);
    const file = this.animations?.[name];
    let clip = null;
    try {
      if (file?.endsWith(".vrma")) {
        const anim = (await this.loader.loadAsync(file)).userData.vrmAnimations?.[0];
        if (anim) clip = createVRMAnimationClip(anim, this.vrm);
      } else if (file?.endsWith(".fbx")) {
        clip = await loadMixamoClip(file, this.vrm);
      }
    } catch (e) { console.warn(`Animation ${file} illisible :`, e); }
    this.clips.set(name, clip);
    return clip;
  }

  tick() {
    const dt = this.clock.getDelta();
    const t = this.clock.elapsedTime;
    const cam = CAMERAS[this.cameraMode];
    this.camera.position.lerp(cam.pos, 1 - Math.exp(-dt * 2.5));
    this.camTarget.lerp(cam.target, 1 - Math.exp(-dt * 2.5));
    this.camera.lookAt(this.camTarget);

    if (this.vrm) {
      this.pose(t);
      const em = this.vrm.expressionManager;
      this.mouth += (this.mouthTarget - this.mouth) * Math.min(1, dt * 18);
      const w = this.vowels ?? { a: 1, e: 0, i: 0, o: 0.3, u: 0 };
      for (const [preset, v] of [["aa", "a"], ["ee", "e"], ["ih", "i"], ["oh", "o"], ["ou", "u"]]) {
        em.setValue(preset, this.mouth * Math.min(1, w[v] * 1.2));
      }
      if (t > this.nextBlink) this.nextBlink = t + 2 + Math.random() * 4;
      const blink = Math.max(0, 1 - Math.abs(this.nextBlink - t - 0.08) / 0.08);
      em.setValue("blink", blink);
      for (const preset of ["happy", "relaxed", "surprised", "sad", "angry"]) {
        const on = preset === this.emotion && t < this.emotionUntil;
        const cur = em.getValue(preset) ?? 0;
        em.setValue(preset, cur + ((on ? 0.7 : 0) - cur) * Math.min(1, dt * 4));
      }
      this.mixer.update(dt);
      this.talk(t);
      this.move(t);
      this.vrm.update(dt);
    }
    this.renderer.render(this.scene, this.camera);
  }

  pose(t) {
    const h = this.vrm.humanoid;
    const bone = (n) => h.getNormalizedBoneNode(n) ?? DUMMY;
    const lArm = bone("leftUpperArm"), rArm = bone("rightUpperArm");
    const lFore = bone("leftLowerArm"), rFore = bone("rightLowerArm");
    const head = bone("head"), spine = bone("spine"), chest = bone("chest"), hips = bone("hips");
    const lLeg = bone("leftUpperLeg"), rLeg = bone("rightUpperLeg"), lKnee = bone("leftLowerLeg"), rKnee = bone("rightLowerLeg");
    // repos : bras le long du corps, respiration, légers mouvements de tête
    lArm.rotation.set(0, 0, -ARM_DOWN);
    rArm.rotation.set(0, 0, ARM_DOWN);
    lFore.rotation.set(0, 0, 0);
    rFore.rotation.set(0, 0, 0);
    for (const b of [lLeg, rLeg, lKnee, rKnee, chest]) b.rotation.set(0, 0, 0);
    spine.rotation.set(Math.sin(t * 1.4) * 0.015, 0, 0);
    head.rotation.set(Math.sin(t * 0.7) * 0.03, Math.sin(t * 0.4) * 0.05, 0);
    hips.position.y = this.hipsY;
    hips.rotation.set(0, 0, 0);

    const g = this.gesture;
    if (!g) return;
    const p = (t - g.start) / g.duration;
    if (p >= 1) { this.gesture = null; this.idleAction?.reset().fadeIn(0.4).play(); return; }
    const env = Math.sin(Math.PI * p); // monte puis redescend
    const hold = Math.min(1, p * 5, (1 - p) * 5); // monte vite, tient, redescend
    const s = (f) => Math.sin((t - g.start) * f);
    switch (g.name) {
      case "salut":
        rArm.rotation.z = ARM_DOWN - env * 2.4;
        rFore.rotation.z = -env * 0.6 + s(14) * 0.35 * env;
        break;
      case "hoche": head.rotation.x += s(9) * 0.18 * env; break;
      case "rire": spine.rotation.x += Math.abs(s(16)) * 0.05 * env; head.rotation.x -= 0.12 * env; break;
      case "reflechit":
        head.rotation.z += 0.18 * env; head.rotation.x -= 0.1 * env;
        rArm.rotation.set(-0.5 * hold, 0, ARM_DOWN - 0.25 * hold); rFore.rotation.y = 2.3 * hold; // main au menton
        break;
      case "etirement":
        lArm.rotation.z = -ARM_DOWN + env * 2.9; rArm.rotation.z = ARM_DOWN - env * 2.9;
        spine.rotation.x -= 0.15 * env; break;
      case "danse":
        hips.position.y = this.hipsY + Math.abs(s(7)) * 0.05;
        hips.rotation.y = s(3.5) * 0.35;
        spine.rotation.z = s(7) * 0.08;
        lArm.rotation.z = -ARM_DOWN + 0.35 + s(7) * 0.2;
        rArm.rotation.z = ARM_DOWN - 0.35 + s(7) * 0.2;
        lKnee.rotation.x = Math.max(0, s(7)) * 0.4; rKnee.rotation.x = Math.max(0, -s(7)) * 0.4;
        head.rotation.z = s(3.5) * 0.1;
        break;
      case "marche": {
        // cycle de marche : jambes et bras opposés, petit rebond du bassin
        const c = s(9) * Math.min(1, p * 8, (1 - p) * 8);
        lLeg.rotation.x = c * 0.45; rLeg.rotation.x = -c * 0.45;
        lKnee.rotation.x = Math.max(0, -c) * 0.7; rKnee.rotation.x = Math.max(0, c) * 0.7;
        lArm.rotation.x = -c * 0.35; rArm.rotation.x = c * 0.35;
        hips.position.y = this.hipsY - Math.abs(c) * 0.015;
        break;
      }
      case "pointe": // montre le tableau, à sa gauche (à droite de l'écran)
        lArm.rotation.set(-0.5 * hold, 0, -ARM_DOWN + 1.1 * hold);
        lFore.rotation.y = -0.2 * hold;
        head.rotation.y += 0.35 * hold; spine.rotation.y = 0.12 * hold;
        break;
      case "explique": // mains ouvertes devant soi, qui bougent en parlant
        lArm.rotation.set((-0.45 + s(5) * 0.1) * hold, 0, -ARM_DOWN + 0.25 * hold);
        rArm.rotation.set((-0.45 + s(6) * 0.1) * hold, 0, ARM_DOWN - 0.25 * hold);
        lFore.rotation.y = (-1.1 + s(5) * 0.25) * hold;
        rFore.rotation.y = (1.1 + s(6) * 0.25) * hold;
        break;
      case "applaudit": {
        const clap = (1 + s(16)) * 0.5;
        lArm.rotation.set(-0.7 * hold, 0, -ARM_DOWN + 0.2 * hold);
        rArm.rotation.set(-0.7 * hold, 0, ARM_DOWN - 0.2 * hold);
        lFore.rotation.set(0, -1.2 * hold, -(0.35 + clap * 0.35) * hold);
        rFore.rotation.set(0, 1.2 * hold, (0.35 + clap * 0.35) * hold);
        break;
      }
      case "s_incline": spine.rotation.x += 0.45 * env; chest.rotation.x = 0.2 * env; head.rotation.x += 0.2 * env; break;
      case "bras_croises":
        lArm.rotation.set(-0.35 * hold, 0, -ARM_DOWN + 0.15 * hold);
        rArm.rotation.set(-0.35 * hold, 0, ARM_DOWN - 0.15 * hold);
        lFore.rotation.set(0, -1.6 * hold, -0.9 * hold); rFore.rotation.set(0, 1.6 * hold, 0.9 * hold);
        head.rotation.z += 0.06 * hold;
        break;
      case "saute": {
        // flexion, impulsion, réception
        const crouch = p < 0.25 ? Math.sin((p / 0.25) * Math.PI) : p > 0.75 ? Math.sin(((p - 0.75) / 0.25) * Math.PI) : 0;
        const air = p >= 0.25 && p <= 0.75 ? Math.sin(((p - 0.25) / 0.5) * Math.PI) : 0;
        hips.position.y = this.hipsY - crouch * 0.12 + air * 0.25;
        for (const leg of [lLeg, rLeg]) leg.rotation.x = -crouch * 0.6;
        for (const knee of [lKnee, rKnee]) knee.rotation.x = crouch * 1.2 + air * 0.3;
        lArm.rotation.z = -ARM_DOWN + air * 2.2; rArm.rotation.z = ARM_DOWN - air * 2.2;
        break;
      }
      case "tourne": hips.rotation.y = Math.PI * 2 * (p < 0.1 ? 0 : p > 0.9 ? 1 : (p - 0.1) / 0.8); break;
    }
  }

  // Après les animations : petits gestes des mains et de la tête pendant qu'elle parle.
  talk(t) {
    if (this.gesture || this.mouth < 0.04) { this.talking = Math.max(0, (this.talking ?? 0) - 0.03); }
    else this.talking = Math.min(1, (this.talking ?? 0) + 0.05);
    if (!this.talking) return;
    const h = this.vrm.humanoid, k = this.talking;
    const add = (n, x, y, z) => { const b = h.getNormalizedBoneNode(n); if (b) { b.rotation.x += x * k; b.rotation.y += y * k; b.rotation.z += z * k; } };
    add("rightLowerArm", 0, 0.35 + Math.sin(t * 2.3) * 0.2, 0);
    add("leftLowerArm", 0, -0.25 - Math.sin(t * 1.9 + 1) * 0.15, 0);
    add("head", Math.sin(t * 3.1) * 0.03, 0, Math.sin(t * 1.3) * 0.03);
  }

  // Déplacement pendant la marche : elle traverse la scène vers la droite, se retourne, revient.
  move(t) {
    const w = this.walk;
    let x = 0, yaw = 0;
    if (w) {
      const p = (t - w.start) / w.duration;
      if (p >= 1) this.walk = null;
      else {
        x = Math.sin(Math.PI * p) * 0.6;
        const dir = Math.cos(Math.PI * p); // vitesse : positive à l'aller, négative au retour
        const turning = Math.min(1, Math.abs(dir) * 3);
        yaw = (dir >= 0 ? 1 : -1) * (Math.PI / 2) * turning * Math.min(1, p * 10, (1 - p) * 10);
      }
    }
    this.root.position.x = x;
    this.root.rotation.y += (yaw - this.root.rotation.y) * 0.2;
  }
}

const DUMMY = new THREE.Object3D(); // os absent du modèle : on écrit dans le vide
