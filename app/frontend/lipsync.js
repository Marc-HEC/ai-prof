// Lip-sync à partir du son de la voix, sans phonèmes du serveur : on estime à chaque image
// l'ouverture (volume) et la voyelle la plus proche (a, e, i, o, u) depuis le spectre.
//
// Deux indices suffisent pour une bouche d'avatar :
//   - le premier formant (F1, 250 à 1000 Hz) monte quand la bouche s'ouvre : a > e, o > i, u ;
//   - l'énergie aiguë (1800 à 3500 Hz, là où monte F2) par rapport au grave (250 à 1000 Hz)
//     grandit quand les lèvres s'étirent : i > e > a > o > u.
// Les deux sont recalibrés en continu sur la voix entendue, pour marcher avec n'importe quelle
// voix AllTalk (grave, aiguë, homme, femme) sans réglage.

export const VOWELS = ["a", "e", "i", "o", "u"];
// Position de chaque voyelle dans le plan (ouverture, étirement), de 0 à 1. Sur une voix
// aiguë, F1 est mal résolu (les harmoniques sont espacées de 200 Hz) : l'ouverture sert surtout
// à repérer le a, et compte moitié moins que l'étirement dans la distance.
const PROTOTYPES = { a: [1, 0.5], e: [0.15, 0.78], i: [0, 1], o: [0.3, 0.17], u: [0.05, 0] };
// Forme de bouche de chaque voyelle : hauteur et largeur relatives.
const SHAPES = { a: [1, 1], e: [0.6, 1.1], i: [0.35, 1.2], o: [0.75, 0.75], u: [0.45, 0.55] };

// Plage adaptative : suit vite les nouveaux extrêmes, et se resserre lentement.
class Range {
  constructor(lo, hi) { this.lo = lo; this.hi = hi; }
  norm(v) {
    this.lo = v < this.lo ? v : this.lo + (v - this.lo) * 0.002;
    this.hi = v > this.hi ? v : this.hi + (v - this.hi) * 0.002;
    const span = Math.max(this.hi - this.lo, 1e-6);
    return Math.min(1, Math.max(0, (v - this.lo) / span));
  }
}

export class LipSync {
  constructor(sampleRate, binCount) {
    this.hz = sampleRate / (binCount * 2);
    this.power = new Float32Array(binCount);
    this.f1 = new Range(420, 820); // Hz, plage de départ typique, ajustée ensuite à la voix
    this.bright = new Range(-50, -16); // dB
    this.weights = { a: 0, e: 0, i: 0, o: 0, u: 1 };
  }

  centroid(from, to) {
    let sum = 0, weighted = 0;
    const a = Math.round(from / this.hz), b = Math.min(this.power.length - 1, Math.round(to / this.hz));
    for (let k = a; k <= b; k++) { sum += this.power[k]; weighted += this.power[k] * k * this.hz; }
    return sum > 0 ? weighted / sum : 0;
  }

  energy(from, to) {
    let sum = 1e-12;
    const a = Math.round(from / this.hz), b = Math.min(this.power.length - 1, Math.round(to / this.hz));
    for (let k = a; k <= b; k++) sum += this.power[k];
    return 10 * Math.log10(sum);
  }

  // dbSpectrum : getFloatFrequencyData de l'AnalyserNode. level : volume de 0 à 1.
  update(dbSpectrum, level) {
    if (level > 0.06) {
      for (let k = 0; k < dbSpectrum.length; k++) this.power[k] = 10 ** (dbSpectrum[k] / 10);
      const open = this.f1.norm(this.centroid(250, 1000));
      const wide = this.bright.norm(this.energy(1800, 3500) - this.energy(250, 1000));
      const scores = {};
      let total = 0;
      for (const v of VOWELS) {
        const [po, pw] = PROTOTYPES[v];
        scores[v] = Math.exp(-(0.5 * (open - po) ** 2 + (wide - pw) ** 2) / 0.03);
        total += scores[v];
      }
      // Lissage court : la bouche reste lisible sans sauter d'une forme à l'autre à chaque image.
      for (const v of VOWELS) this.weights[v] += (scores[v] / total - this.weights[v]) * 0.45;
    }
    return this.mouth(level);
  }

  // Ce que les avatars utilisent : ouverture, largeur, voyelle dominante et poids de chaque voyelle.
  mouth(level) {
    let height = 0, width = 0, vowel = "a", best = -1;
    for (const v of VOWELS) {
      const w = this.weights[v];
      height += w * SHAPES[v][0];
      width += w * SHAPES[v][1];
      if (w > best) { best = w; vowel = v; }
    }
    return { level, open: Math.min(1, level * height), width, vowel, weights: { ...this.weights } };
  }
}
