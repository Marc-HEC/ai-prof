// Micro (PCM16 16 kHz vers le serveur), lecture de la voix avec niveau pour le lip-sync,
// et conversion des messages vocaux.

const WORKLET = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.acc = 0; this.out = []; }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        const v = Math.max(-1, Math.min(1, ch[i]));
        this.out.push(v < 0 ? v * 0x8000 : v * 0x7fff);
        if (this.out.length === 480) {
          this.port.postMessage(new Int16Array(this.out).buffer, []);
          this.out = [];
        }
      }
    }
    return true;
  }
}
registerProcessor("capture", Capture);
`;

export class Microphone {
  constructor(onFrame) { this.onFrame = onFrame; this.active = false; }

  // deviceId : un micro précis, par exemple la sortie de VoiceMeeter pendant un cours sur Teams.
  async start(deviceId) {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
               ...(deviceId ? { deviceId: { exact: deviceId } } : {}) },
    });
    this.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "capture");
    this.node.port.onmessage = (e) => this.onFrame(e.data);
    src.connect(this.node);
    this.active = true;
  }

  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.active = false;
  }
}

export class VoicePlayer {
  constructor(onLevel, onIdle) {
    this.ctx = new AudioContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(this.ctx.destination);
    this.data = new Float32Array(this.analyser.fftSize);
    this.queue = [];
    this.current = null;
    this.generation = 0;
    this.onLevel = onLevel;
    this.onIdle = onIdle;
    const loop = () => { this.onLevel(this.level()); requestAnimationFrame(loop); };
    loop();
  }

  get playing() { return this.current !== null || this.queue.length > 0; }

  level() {
    if (!this.current) return 0;
    this.analyser.getFloatTimeDomainData(this.data);
    let sum = 0;
    for (const v of this.data) sum += v * v;
    return Math.min(1, Math.sqrt(sum / this.data.length) * 6);
  }

  async enqueue(arrayBuffer) {
    const gen = this.generation;
    if (this.ctx.state === "suspended") await this.ctx.resume();
    const buf = await this.ctx.decodeAudioData(arrayBuffer);
    if (gen !== this.generation) return; // une interruption est passée entre-temps
    this.queue.push(buf);
    if (!this.current) this.next();
  }

  next() {
    const buf = this.queue.shift();
    if (!buf) { this.current = null; this.onIdle(); return; }
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.analyser);
    src.onended = () => { if (this.current === src) this.next(); };
    this.current = src;
    src.start();
  }

  stop() {
    this.generation += 1;
    this.queue = [];
    const cur = this.current;
    this.current = null;
    try { cur?.stop(); } catch { /* déjà arrêté */ }
  }
}

// Message vocal (n'importe quel format lisible par le navigateur) -> PCM16 16 kHz en base64.
export async function fileToPcmBase64(file) {
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(await file.arrayBuffer());
  ctx.close();
  const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start();
  const rendered = await off.startRendering();
  const f32 = rendered.getChannelData(0);
  const i16 = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const v = Math.max(-1, Math.min(1, f32[i]));
    i16[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  let bin = "";
  const bytes = new Uint8Array(i16.buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

// Image -> data URL redimensionnée (1024 px max) pour garder des messages légers.
export async function imageToDataUrl(file, max = 1024) {
  const img = await createImageBitmap(file);
  const scale = Math.min(1, max / Math.max(img.width, img.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.width * scale);
  canvas.height = Math.round(img.height * scale);
  canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85);
}

// Liste des micros. Les noms ne sont visibles qu'une fois l'accès au micro accordé.
export async function listMicrophones() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((d) => d.kind === "audioinput" && d.deviceId);
}
