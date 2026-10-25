// Avatar VRM : idle (respiration, clignements, regard), lip-sync, émotions, caméra et gestes.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from "@pixiv/three-vrm-animation";

const EMOTIONS = { joie: "happy", tendresse: "relaxed", taquine: "happy", surprise: "surprised",
                   triste: "sad", agacee: "angry", neutre: null };
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
    this.loader = new GLTFLoader();
    this.loader.register((p) => new VRMLoaderPlugin(p));
    this.loader.register((p) => new VRMAnimationLoaderPlugin(p));
    addEventListener("resize", () => this.resize());
    this.resize();
    this.renderer.setAnimationLoop(() => this.tick());
  }

  async load(url) {
    const gltf = await this.loader.loadAsync(url);
    const vrm = gltf.userData.vrm;
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons?.(gltf.scene);
    VRMUtils.rotateVRM0(vrm);
    this.scene.add(vrm.scene);
    if (vrm.lookAt) vrm.lookAt.target = this.camera;
    vrm.scene.updateMatrixWorld(true);
    const head = vrm.humanoid.getRawBoneNode("head").getWorldPosition(new THREE.Vector3());
    CAMERAS = framing(head.y + 0.05);
    this.camera.position.copy(CAMERAS[this.cameraMode].pos);
    this.camTarget.copy(CAMERAS[this.cameraMode].target);
    this.vrm = vrm;
    this.hipsY = vrm.humanoid.getNormalizedBoneNode("hips").position.y;
    this.mixer = new THREE.AnimationMixer(vrm.scene);
  }

  resize() {
    const el = this.renderer.domElement.parentElement;
    const w = el.clientWidth, h = el.clientHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  setMouth(level) { this.mouthTarget = Math.min(1, level); }

  setEmotion(name) {
    this.emotion = EMOTIONS[name] ?? null;
    this.emotionUntil = this.clock.elapsedTime + 5;
  }

  setCamera(mode) { if (CAMERAS[mode]) this.cameraMode = mode; }

  // Geste : fichier animations/<nom>.vrma s'il existe, sinon geste procédural de secours.
  async play(name) {
    if (name === "se_lever") return this.setCamera("plein_pied");
    if (name === "s_asseoir") return this.setCamera("buste");
    if (!this.vrm) return;
    const clip = await this.clip(name);
    if (clip) {
      const action = this.mixer.clipAction(clip);
      action.reset().setLoop(name === "danse" ? THREE.LoopRepeat : THREE.LoopOnce, name === "danse" ? 4 : 1);
      action.clampWhenFinished = false;
      action.fadeIn(0.3).play();
      return;
    }
    const durations = { salut: 2, hoche: 1.2, rire: 1.5, reflechit: 2.5, danse: 8, etirement: 3 };
    this.gesture = { name, start: this.clock.elapsedTime, duration: durations[name] ?? 1.5 };
    if (name === "danse") this.setCamera("plein_pied");
  }

  async clip(name) {
    if (this.clips.has(name)) return this.clips.get(name);
    let clip = null;
    try {
      const gltf = await this.loader.loadAsync(`animations/${name}.vrma`);
      const anim = gltf.userData.vrmAnimations?.[0];
      if (anim) clip = createVRMAnimationClip(anim, this.vrm);
    } catch { /* pas de fichier : geste procédural */ }
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
      em.setValue("aa", this.mouth * 0.9);
      em.setValue("oh", this.mouth * 0.25);
      if (t > this.nextBlink) this.nextBlink = t + 2 + Math.random() * 4;
      const blink = Math.max(0, 1 - Math.abs(this.nextBlink - t - 0.08) / 0.08);
      em.setValue("blink", blink);
      for (const preset of ["happy", "relaxed", "surprised", "sad", "angry"]) {
        const on = preset === this.emotion && t < this.emotionUntil;
        const cur = em.getValue(preset) ?? 0;
        em.setValue(preset, cur + ((on ? 0.7 : 0) - cur) * Math.min(1, dt * 4));
      }
      this.mixer.update(dt);
      this.vrm.update(dt);
    }
    this.renderer.render(this.scene, this.camera);
  }

  pose(t) {
    const h = this.vrm.humanoid;
    const bone = (n) => h.getNormalizedBoneNode(n);
    const lArm = bone("leftUpperArm"), rArm = bone("rightUpperArm");
    const rFore = bone("rightLowerArm"), head = bone("head"), spine = bone("spine"), hips = bone("hips");
    // repos : bras le long du corps, respiration, légers mouvements de tête
    lArm.rotation.set(0, 0, -ARM_DOWN);
    rArm.rotation.set(0, 0, ARM_DOWN);
    rFore.rotation.set(0, 0, 0);
    spine.rotation.set(Math.sin(t * 1.4) * 0.015, 0, 0);
    head.rotation.set(Math.sin(t * 0.7) * 0.03, Math.sin(t * 0.4) * 0.05, 0);
    hips.position.y = this.hipsY;
    hips.rotation.set(0, 0, 0);

    const g = this.gesture;
    if (!g) return;
    const p = (t - g.start) / g.duration;
    if (p >= 1) { this.gesture = null; return; }
    const env = Math.sin(Math.PI * p); // monte puis redescend
    const s = (f) => Math.sin((t - g.start) * f);
    switch (g.name) {
      case "salut":
        rArm.rotation.z = ARM_DOWN - env * 2.4;
        rFore.rotation.z = -env * 0.6 + s(14) * 0.35 * env;
        break;
      case "hoche": head.rotation.x += s(9) * 0.18 * env; break;
      case "rire": spine.rotation.x += Math.abs(s(16)) * 0.05 * env; head.rotation.x -= 0.12 * env; break;
      case "reflechit": head.rotation.z += 0.18 * env; head.rotation.x -= 0.1 * env; break;
      case "etirement":
        lArm.rotation.z = -ARM_DOWN + env * 2.9; rArm.rotation.z = ARM_DOWN - env * 2.9;
        spine.rotation.x -= 0.15 * env; break;
      case "danse":
        hips.position.y = this.hipsY + Math.abs(s(7)) * 0.05;
        hips.rotation.y = s(3.5) * 0.35;
        spine.rotation.z = s(7) * 0.08;
        lArm.rotation.z = -ARM_DOWN + 0.35 + s(7) * 0.2;
        rArm.rotation.z = ARM_DOWN - 0.35 + s(7) * 0.2;
        head.rotation.z = s(3.5) * 0.1;
        break;
    }
  }
}
