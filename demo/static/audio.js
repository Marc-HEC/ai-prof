// Audio cote navigateur : lecture en file, lip-sync, capture micro et VAD.
//
// L'annulation d'echo est demandee au navigateur a la capture (decision D5) :
// c'est le seul endroit ou elle fonctionne, et sans elle, en mains libres,
// le prof s'entend parler et se repond a lui-meme.

let sharedCtx = null;
export function audioContext() {
  if (!sharedCtx) sharedCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (sharedCtx.state === "suspended") sharedCtx.resume();
  return sharedCtx;
}

// -- Lecture ---------------------------------------------------------------

// File de phrases : chaque phrase est synthetisee des qu'elle arrive (fetch
// lance tout de suite), mais jouee dans l'ordre. C'est ce qui permet a la
// phrase 2 d'etre prete pendant que la phrase 1 est lue.
export class Player {
  constructor({ onSentenceStart, onIdle, onFirstAudio } = {}) {
    this.ctx = audioContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.35;
    this.analyser.connect(this.ctx.destination);
    this.queue = [];
    this.current = null;
    this.playing = false;
    this.generation = 0;
    this.onSentenceStart = onSentenceStart || (() => {});
    this.onIdle = onIdle || (() => {});
    this.onFirstAudio = onFirstAudio || (() => {});
    this.firstAudioPending = false;
  }

  get speaking() {
    return this.playing || this.queue.length > 0;
  }

  beginTurn() {
    this.firstAudioPending = true;
  }

  enqueue(audioPromise, meta) {
    this.queue.push({ audioPromise, meta, gen: this.generation });
    if (!this.playing) this._next();
  }

  async _next() {
    const item = this.queue.shift();
    if (!item) {
      this.playing = false;
      this.onIdle();
      return;
    }
    this.playing = true;
    let buffer = null;
    try {
      const data = await item.audioPromise;
      if (item.gen !== this.generation) return;
      buffer = await this.ctx.decodeAudioData(data);
    } catch (err) {
      if (item.gen !== this.generation) return;
      console.warn("TTS : phrase ignoree", err);
    }
    if (item.gen !== this.generation) return;
    if (!buffer) return this._next();

    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.analyser);
    this.current = src;
    src.onended = () => {
      if (this.current === src) this.current = null;
      if (item.gen === this.generation) this._next();
    };
    if (this.firstAudioPending) {
      this.firstAudioPending = false;
      this.onFirstAudio();
    }
    this.onSentenceStart(item.meta);
    src.start();
  }

  stop() {
    this.generation += 1;
    this.queue = [];
    if (this.current) {
      try { this.current.stop(); } catch (_) {}
      this.current = null;
    }
    this.playing = false;
    this.firstAudioPending = false;
  }
}

// -- Lip-sync ----------------------------------------------------------------

// Enveloppe d'energie par bande de frequence -> visemes VRM (aa, ih, ou, ee, oh).
// C'est la methode prevue par l'architecture (section 2.4) : moins precise qu'un
// alignement phonetique, mais gratuite et independante du moteur TTS.
export class LipSync {
  constructor(analyser) {
    this.analyser = analyser;
    this.freq = new Uint8Array(analyser.frequencyBinCount);
    this.time = new Float32Array(analyser.fftSize);
    this.state = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0, level: 0 };
    this.noiseFloor = 0.004;
  }

  _band(lo, hi) {
    const hz = this.analyser.context.sampleRate / this.analyser.fftSize;
    const a = Math.max(1, Math.floor(lo / hz));
    const b = Math.min(this.freq.length - 1, Math.ceil(hi / hz));
    let s = 0;
    for (let i = a; i <= b; i++) s += this.freq[i];
    return s / Math.max(1, b - a + 1) / 255;
  }

  compute(dt) {
    this.analyser.getFloatTimeDomainData(this.time);
    this.analyser.getByteFrequencyData(this.freq);
    let rms = 0;
    for (let i = 0; i < this.time.length; i++) rms += this.time[i] * this.time[i];
    rms = Math.sqrt(rms / this.time.length);
    const level = Math.min(1, Math.max(0, (rms - this.noiseFloor) * 9));

    const low = this._band(150, 600);    // F1 bas : ou, oh
    const mid = this._band(600, 1500);   // F1 haut : aa
    const high = this._band(1500, 3500); // F2 haut : ee, ih
    const hiss = this._band(3500, 7000); // fricatives : bouche peu ouverte
    const sum = low + mid + high + hiss + 1e-6;
    const [l, m, h, s] = [low / sum, mid / sum, high / sum, hiss / sum];

    const target = {
      aa: level * Math.min(1, m * 2.2),
      oh: level * Math.min(1, l * 1.6) * (m > 0.22 ? 1 : 0.5),
      ou: level * Math.min(1, l * 1.8) * (m < 0.22 ? 1 : 0.3),
      ee: level * Math.min(1, h * 2.4),
      ih: level * Math.min(1, (h + s) * 1.4) * 0.7,
    };
    // Le viseme dominant l'emporte, les autres sont attenues : evite la bouillie.
    const top = Math.max(...Object.values(target), 1e-6);
    const k = level / top;
    const attack = 1 - Math.exp(-dt * 28);
    const release = 1 - Math.exp(-dt * 14);
    for (const key of Object.keys(target)) {
      let v = target[key] * k;
      v = v / top >= 0.75 ? v : v * 0.35;
      const cur = this.state[key];
      this.state[key] = cur + (v - cur) * (v > cur ? attack : release);
    }
    this.state.level = level;
    return this.state;
  }
}

// -- WAV -----------------------------------------------------------------------

export function encodeWav(samples, sampleRate = 16000) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE");
  w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

// -- Micro + VAD -----------------------------------------------------------------

const MIC_CONSTRAINTS = {
  audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};

// Silero VAD dans le navigateur (vad-web, ONNX sur CPU), comme prevu par
// l'architecture. Repli sur un VAD a seuil d'energie si le CDN est injoignable.
export async function createListener(handlers) {
  if (window.vad && window.vad.MicVAD && !window.__forceEnergyVad) {
    try {
      const mic = await window.vad.MicVAD.new({
        model: "v5",
        onnxWASMBasePath: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/",
        baseAssetPath: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.31/dist/",
        positiveSpeechThreshold: 0.5,
        negativeSpeechThreshold: 0.35,
        redemptionMs: 700,
        preSpeechPadMs: 300,
        minSpeechMs: 250,
        startOnLoad: false,
        getStream: () => navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS),
        onSpeechStart: () => handlers.onSpeechStart(),
        onSpeechRealStart: () => handlers.onSpeechConfirmed && handlers.onSpeechConfirmed(),
        onVADMisfire: () => handlers.onMisfire && handlers.onMisfire(),
        onSpeechEnd: (audio) => handlers.onSpeechEnd(audio),
      });
      return {
        kind: "Silero VAD",
        start: () => mic.start(),
        pause: () => mic.pause(),
      };
    } catch (err) {
      console.warn("vad-web indisponible, repli sur le VAD d'energie", err);
    }
  }
  return createEnergyListener(handlers);
}

const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("tap", Tap);
`;

async function createEnergyListener(handlers) {
  const stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "tap");
  const sink = ctx.createGain();
  sink.gain.value = 0;
  src.connect(node).connect(sink).connect(ctx.destination);

  const ratio = ctx.sampleRate / 16000;
  const FRAME = 480; // 30 ms a 16 kHz
  let carry = new Float32Array(0);
  let active = false;
  let running = false;
  let floor = 0.003;
  let loud = 0, quiet = 0;
  let pre = [];
  let speech = [];

  function downsample(input) {
    const joined = new Float32Array(carry.length + input.length);
    joined.set(carry); joined.set(input, carry.length);
    const n = Math.floor(joined.length / ratio);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = joined[Math.floor(i * ratio)];
    carry = joined.slice(Math.floor(n * ratio));
    return out;
  }

  let acc = new Float32Array(0);
  node.port.onmessage = (e) => {
    if (!running) return;
    const d = downsample(e.data);
    const merged = new Float32Array(acc.length + d.length);
    merged.set(acc); merged.set(d, acc.length);
    let off = 0;
    while (merged.length - off >= FRAME) {
      frame(merged.subarray(off, off + FRAME).slice());
      off += FRAME;
    }
    acc = merged.slice(off);
  };

  function frame(f) {
    let e = 0;
    for (let i = 0; i < f.length; i++) e += f[i] * f[i];
    const rms = Math.sqrt(e / f.length);
    const threshold = Math.max(0.012, floor * 3.5);
    if (!active) {
      floor = floor * 0.97 + Math.min(rms, 0.05) * 0.03;
      pre.push(f); if (pre.length > 10) pre.shift(); // 300 ms de pre-roll
      loud = rms > threshold ? loud + 1 : 0;
      if (loud >= 3) {
        active = true; quiet = 0; speech = pre.slice(); pre = [];
        handlers.onSpeechStart();
      }
    } else {
      speech.push(f);
      quiet = rms < threshold * 0.7 ? quiet + 1 : 0;
      if (quiet >= 25) { // 750 ms de silence
        active = false; loud = 0;
        const frames = speech.length - quiet;
        if (frames < 8) { handlers.onMisfire && handlers.onMisfire(); speech = []; return; }
        const out = new Float32Array(speech.length * FRAME);
        speech.forEach((s, i) => out.set(s, i * FRAME));
        speech = [];
        handlers.onSpeechEnd(out);
      }
    }
  }

  return {
    kind: "VAD d'energie (secours)",
    start: async () => { await ctx.resume(); running = true; },
    pause: () => { running = false; active = false; speech = []; },
  };
}
