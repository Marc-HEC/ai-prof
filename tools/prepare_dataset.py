"""Prepare et valide un dataset vocal pour le clonage (essai A).

Convertit des sources audio quelconques en WAV 48 kHz mono, mesure la qualite
reelle du signal, decoupe en segments sur les silences, et extrait le meilleur
extrait de reference pour les modeles zero-shot.

Le point important est la validation : un dataset issu de vocaux WhatsApp passe
visuellement pour correct alors que sa bande passante est tronquee vers 8 kHz,
ce qui plafonne definitivement la fidelite du clone. Le controle de bande
passante ci-dessous detecte ce cas.

Dependances : numpy, et ffmpeg/ffprobe dans le PATH.

    python tools/prepare_dataset.py voices/lea/raw --out voices/lea/dataset
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

TARGET_SR = 48_000
AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma", ".webm"}

# Seuils de validation. `warn` degrade la note, `fail` invalide le dataset.
PEAK_DBFS_WARN = (-12.0, -1.0)
CLIP_RATIO_FAIL = 1e-3
NOISE_FLOOR_WARN = -50.0
NOISE_FLOOR_FAIL = -35.0
BANDWIDTH_WARN_HZ = 13_000
BANDWIDTH_FAIL_HZ = 9_000
SPEECH_SECONDS_WARN = 300.0
SPEECH_SECONDS_FAIL = 120.0


@dataclass
class ClipReport:
    path: str
    duration_s: float
    speech_s: float
    peak_dbfs: float
    rms_dbfs: float
    noise_floor_dbfs: float
    snr_db: float
    bandwidth_hz: float
    clip_ratio: float
    dc_offset: float
    segments: int
    issues: list[str]


def die(message: str) -> None:
    print(f"ERREUR: {message}", file=sys.stderr)
    raise SystemExit(1)


def decode(path: Path, sr: int = TARGET_SR) -> np.ndarray:
    """Decode n'importe quel format en mono float32 via ffmpeg."""
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "1", "-ar", str(sr),
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        die(f"ffmpeg n'a pas pu decoder {path.name}\n{proc.stderr.decode(errors='replace')}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def write_wav(path: Path, x: np.ndarray, sr: int = TARGET_SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(x, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(pcm.tobytes())


def dbfs(value: float) -> float:
    return 20.0 * np.log10(max(float(value), 1e-12))


def frame_rms(x: np.ndarray, sr: int, win_ms: float = 25.0, hop_ms: float = 10.0) -> tuple[np.ndarray, int]:
    win = max(1, int(sr * win_ms / 1000))
    hop = max(1, int(sr * hop_ms / 1000))
    if len(x) < win:
        return np.array([np.sqrt(np.mean(x**2))] if len(x) else [0.0]), hop
    count = 1 + (len(x) - win) // hop
    strides = np.lib.stride_tricks.as_strided(
        x, shape=(count, win), strides=(x.strides[0] * hop, x.strides[0]), writeable=False
    )
    return np.sqrt(np.mean(strides.astype(np.float64) ** 2, axis=1)), hop


def measure_bandwidth(x: np.ndarray, sr: int) -> float:
    """Frequence au-dela de laquelle il ne reste plus d'energie significative.

    Un enregistrement 48 kHz honnete monte vers 16-20 kHz. Un passage par Opus
    ou AMR basse bitrate coupe net vers 8-12 kHz : le clone heritera de ce mur.
    """
    n = 1 << 15
    if len(x) < n:
        return 0.0
    # Moyenne spectrale sur les tranches les plus energetiques (donc voisees).
    hop = n // 2
    frames = [x[i:i + n] for i in range(0, len(x) - n, hop)]
    if not frames:
        return 0.0
    frames.sort(key=lambda f: float(np.mean(f.astype(np.float64) ** 2)), reverse=True)
    frames = frames[: max(1, len(frames) // 4)]

    window = np.hanning(n)
    spectrum = np.zeros(n // 2 + 1)
    for frame in frames:
        spectrum += np.abs(np.fft.rfft(frame.astype(np.float64) * window)) ** 2
    spectrum /= len(frames)

    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    peak = spectrum.max()
    if peak <= 0:
        return 0.0
    # -60 dB sous le pic, en remontant depuis les hautes frequences.
    threshold = peak * 10 ** (-60.0 / 10.0)
    above = np.nonzero(spectrum > threshold)[0]
    return float(freqs[above[-1]]) if len(above) else 0.0


def detect_segments(
    x: np.ndarray,
    sr: int,
    min_speech_s: float = 1.0,
    max_speech_s: float = 14.0,
    min_silence_s: float = 0.35,
    pad_s: float = 0.15,
) -> list[tuple[int, int]]:
    """Decoupe sur les silences par seuil d'energie adaptatif."""
    rms, hop = frame_rms(x, sr)
    if not len(rms):
        return []
    floor = np.percentile(rms, 10)
    ceil = np.percentile(rms, 95)
    threshold = max(floor * 3.0, ceil * 0.06)

    voiced = rms > threshold
    min_sil_frames = int(min_silence_s * sr / hop)

    # Comble les micro-silences pour ne pas hacher au milieu des mots.
    idx = 0
    while idx < len(voiced):
        if voiced[idx]:
            idx += 1
            continue
        start = idx
        while idx < len(voiced) and not voiced[idx]:
            idx += 1
        if 0 < start and idx < len(voiced) and (idx - start) < min_sil_frames:
            voiced[start:idx] = True

    segments: list[tuple[int, int]] = []
    pad = int(pad_s * sr)
    idx = 0
    while idx < len(voiced):
        if not voiced[idx]:
            idx += 1
            continue
        start = idx
        while idx < len(voiced) and voiced[idx]:
            idx += 1
        a = max(0, start * hop - pad)
        b = min(len(x), idx * hop + pad)
        if (b - a) / sr < min_speech_s:
            continue
        # Un segment trop long est redecoupe en parts egales.
        span = (b - a) / sr
        parts = max(1, int(np.ceil(span / max_speech_s)))
        step = (b - a) // parts
        for p in range(parts):
            segments.append((a + p * step, a + (p + 1) * step if p < parts - 1 else b))
    return segments


def analyse(path: Path, x: np.ndarray, sr: int, segments: list[tuple[int, int]]) -> ClipReport:
    issues: list[str] = []
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms, _ = frame_rms(x, sr)
    speech_s = sum(b - a for a, b in segments) / sr

    noise_floor = float(np.percentile(rms, 5)) if len(rms) else 0.0
    speech_rms = float(np.percentile(rms, 75)) if len(rms) else 0.0
    snr = dbfs(speech_rms) - dbfs(noise_floor)
    clip_ratio = float(np.mean(np.abs(x) >= 0.999)) if len(x) else 0.0
    bandwidth = measure_bandwidth(x, sr)
    dc = float(np.mean(x)) if len(x) else 0.0

    peak_db = dbfs(peak)
    if peak_db > PEAK_DBFS_WARN[1]:
        issues.append(f"niveau trop chaud ({peak_db:.1f} dBFS), risque de saturation")
    elif peak_db < PEAK_DBFS_WARN[0]:
        issues.append(f"niveau trop faible ({peak_db:.1f} dBFS), remonter le gain a la prise")
    if clip_ratio > CLIP_RATIO_FAIL:
        issues.append(f"FAIL saturation sur {clip_ratio * 100:.2f} % des echantillons")
    if dbfs(noise_floor) > NOISE_FLOOR_FAIL:
        issues.append(f"FAIL bruit de fond a {dbfs(noise_floor):.1f} dBFS, piece ou micro inexploitables")
    elif dbfs(noise_floor) > NOISE_FLOOR_WARN:
        issues.append(f"bruit de fond eleve ({dbfs(noise_floor):.1f} dBFS)")
    if bandwidth < BANDWIDTH_FAIL_HZ:
        issues.append(
            f"FAIL bande passante limitee a {bandwidth / 1000:.1f} kHz : source deja compressee "
            "avec perte, le clone heritera de ce plafond"
        )
    elif bandwidth < BANDWIDTH_WARN_HZ:
        issues.append(f"bande passante reduite ({bandwidth / 1000:.1f} kHz)")
    if abs(dc) > 0.01:
        issues.append(f"decalage continu de {dc:+.4f}, corrige automatiquement")

    return ClipReport(
        path=path.name,
        duration_s=len(x) / sr,
        speech_s=speech_s,
        peak_dbfs=round(peak_db, 2),
        rms_dbfs=round(dbfs(float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))) if len(x) else -120.0, 2),
        noise_floor_dbfs=round(dbfs(noise_floor), 2),
        snr_db=round(snr, 2),
        bandwidth_hz=round(bandwidth, 1),
        clip_ratio=round(clip_ratio, 6),
        dc_offset=round(dc, 6),
        segments=len(segments),
        issues=issues,
    )


def pick_reference(
    pool: list[tuple[np.ndarray, float]], target_s: float, sr: int
) -> tuple[np.ndarray, float] | None:
    """Choisit l'extrait de reference : duree proche de la cible, meilleur SNR, sans saturation."""
    best = None
    best_score = -1e9
    for audio, snr in pool:
        duration = len(audio) / sr
        if duration < target_s * 0.6:
            continue
        if float(np.max(np.abs(audio))) >= 0.999:
            continue
        score = snr - 4.0 * abs(duration - target_s)
        if score > best_score:
            best_score = score
            best = (audio[: int(target_s * sr)] if duration > target_s else audio, snr)
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="fichier ou dossier audio brut")
    parser.add_argument("--out", type=Path, required=True, help="dossier de sortie du dataset")
    parser.add_argument("--ref-seconds", type=float, default=10.0, help="duree de l'extrait de reference")
    parser.add_argument("--force", action="store_true", help="ecrire le dataset meme en cas de FAIL")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        die("ffmpeg est introuvable dans le PATH")
    if not args.source.exists():
        die(f"source introuvable : {args.source}")

    sources = (
        sorted(p for p in args.source.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES)
        if args.source.is_dir()
        else [args.source]
    )
    if not sources:
        die(f"aucun fichier audio dans {args.source}")

    segments_dir = args.out / "segments"
    # Les segments sont d'abord ecrits a cote, puis deplaces seulement si le
    # dataset est valide. Sans cela, un dataset refuse laisserait quand meme des
    # fichiers en place et le message "relancer avec --force" serait mensonger.
    staging_dir = args.out / ".staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    reports: list[ClipReport] = []
    manifest: list[dict] = []
    ref_pool: list[tuple[np.ndarray, float]] = []
    seen: dict[tuple, str] = {}
    duplicates = 0
    index = 0

    print(f"{len(sources)} fichier(s) source\n")
    for path in sources:
        x = decode(path)
        if not len(x):
            print(f"  {path.name}: vide, ignore")
            continue
        x = x - float(np.mean(x))  # retrait du continu

        # Detection de doublons. Le meme vocal present en .opus et en .wav
        # decode vers le meme signal : le compter deux fois gonflerait la duree
        # de parole et dupliquerait les segments d'enrolement, ce qui fausserait
        # ensuite le plafond de similarite du banc.
        signature = (
            round(len(x) / TARGET_SR, 2),
            round(dbfs(float(np.max(np.abs(x)))), 1),
            round(dbfs(float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))), 1),
        )
        if signature in seen:
            print(f"  [doublon] {path.name}: contenu identique a {seen[signature]}, ignore")
            duplicates += 1
            continue
        seen[signature] = path.name

        segments = detect_segments(x, TARGET_SR)
        report = analyse(path, x, TARGET_SR, segments)
        reports.append(report)

        status = "FAIL" if any(i.startswith("FAIL") for i in report.issues) else "ok  "
        print(
            f"  [{status}] {path.name}: {report.duration_s:6.1f}s  "
            f"parole {report.speech_s:6.1f}s  crete {report.peak_dbfs:6.1f} dBFS  "
            f"SNR {report.snr_db:5.1f} dB  bande {report.bandwidth_hz / 1000:4.1f} kHz  "
            f"{report.segments} segments"
        )
        for issue in report.issues:
            print(f"         - {issue}")

        for a, b in segments:
            chunk = x[a:b]
            rms, _ = frame_rms(chunk, TARGET_SR)
            snr = dbfs(float(np.percentile(rms, 75))) - dbfs(float(np.percentile(rms, 5))) if len(rms) else 0.0
            name = f"{index:04d}.wav"
            write_wav(staging_dir / name, chunk)
            manifest.append(
                {
                    "file": f"segments/{name}",
                    "source": path.name,
                    "duration_s": round(len(chunk) / TARGET_SR, 3),
                    "snr_db": round(snr, 2),
                }
            )
            ref_pool.append((chunk, snr))
            index += 1

    total_speech = sum(r.speech_s for r in reports)
    failures = [i for r in reports for i in r.issues if i.startswith("FAIL")]
    if total_speech < SPEECH_SECONDS_FAIL:
        failures.append(f"FAIL seulement {total_speech / 60:.1f} min de parole, minimum 2 min")

    print(f"\n{len(reports)} fichier(s) retenu(s)" + (f", {duplicates} doublon(s) ignore(s)" if duplicates else ""))
    print(f"Total parole utile : {total_speech / 60:.1f} min sur {index} segments")
    if SPEECH_SECONDS_FAIL <= total_speech < SPEECH_SECONDS_WARN:
        print(f"  attention : {total_speech / 60:.1f} min, la cible est 5 min")

    if failures:
        print("\nDataset invalide :")
        for failure in dict.fromkeys(failures):
            print(f"  - {failure}")
        if not args.force:
            shutil.rmtree(staging_dir, ignore_errors=True)
            try:
                args.out.rmdir()
            except OSError:
                pass
            print("\nAucun fichier n'a ete ecrit.")
            print("Relancer avec --force pour ecrire quand meme (le clone sera plafonne).")
            return 1
        print("\n--force : le dataset est ecrit malgre les defauts ci-dessus.")

    # Validation passee : on publie.
    args.out.mkdir(parents=True, exist_ok=True)
    if segments_dir.exists():
        shutil.rmtree(segments_dir)
    if staging_dir.exists():
        staging_dir.rename(segments_dir)
    else:
        segments_dir.mkdir(parents=True)

    reference = pick_reference(ref_pool, args.ref_seconds, TARGET_SR)
    if reference:
        write_wav(args.out / "reference.wav", reference[0])
        print(
            f"Extrait de reference : reference.wav "
            f"({len(reference[0]) / TARGET_SR:.1f}s, SNR {reference[1]:.1f} dB)"
        )
    else:
        print("Aucun extrait de reference exploitable n'a pu etre extrait")

    (args.out / "manifest.json").write_text(
        json.dumps(
            {
                "sample_rate": TARGET_SR,
                "total_speech_s": round(total_speech, 2),
                "segment_count": index,
                "duplicates_ignored": duplicates,
                "reference": "reference.wav" if reference else None,
                "clips": [asdict(r) for r in reports],
                "segments": manifest,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"\nDataset ecrit dans {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
