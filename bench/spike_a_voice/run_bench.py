"""Essai A - banc d'essai du clonage vocal francais.

Compare plusieurs moteurs TTS sur le meme dataset, avec les memes phrases, et
produit un tableau chiffre au lieu d'une impression subjective.

Usage type, sur le pod GPU :

    python run_bench.py \
        --dataset ../../voices/lea/dataset \
        --engines chatterbox-multilingual-v3 qwen3-tts-base gpt-sovits-v4 \
        --out results/2026-09-15

Sorties :
    results/<run>/audio/<moteur>/wer_XX.wav   syntheses scorees
    results/<run>/audio/<moteur>/hard_XX.wav  cas difficiles, pour l'ecoute
    results/<run>/results.json                mesures brutes
    results/<run>/report.md                   tableau comparatif et verdict
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import engines as engine_registry  # noqa: E402
from audio_io import read_wav, write_wav  # noqa: E402
from metrics import FrenchWER, SentenceScore, SpeakerSimilarity, summarise  # noqa: E402

# Seuils definis dans le plan. Ils tranchent, ils ne decorent pas.
SIMILARITY_TARGET = 0.75
# Part du plafond humain. C'est le critere prioritaire : la similarite absolue
# depend du micro et de la piece, la part du plafond non.
SIMILARITY_RATIO_TARGET = 0.90
WER_TARGET = 0.08
ENROLLMENT_CLIPS = 12


def load_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def resolve_reference_text(dataset: Path, reference: Path, wer_engine: FrenchWER, out_dir: Path) -> str:
    """Transcription de l'extrait de reference, requise par les moteurs de clonage.

    Transcrite une fois puis mise en cache : la recalculer entre deux moteurs
    introduirait une variation de prompt et fausserait la comparaison.

    Le cache va dans le dossier de resultats, pas dans le dataset : sur le pod,
    `voices/` est monte en lecture seule (c'est une donnee biometrique, elle n'a
    pas a etre modifiable par le calcul). Un `reference.txt` depose a la main
    dans le dataset reste prioritaire, ce qui permet de corriger une
    transcription approximative.
    """
    for cached in (dataset / "reference.txt", out_dir / "reference.txt"):
        if cached.exists():
            text = cached.read_text(encoding="utf-8").strip()
            if text:
                return text

    audio, sr = read_wav(reference)
    print("  transcription de l'extrait de reference...")
    text = wer_engine.transcribe(audio, sr)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reference.txt").write_text(text, encoding="utf-8")
    print(f"  reference : {text[:110]}{'...' if len(text) > 110 else ''}")
    return text


def load_enrollment(dataset: Path, limit: int = ENROLLMENT_CLIPS) -> list[tuple[np.ndarray, int]]:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    segments = sorted(manifest["segments"], key=lambda s: s["snr_db"], reverse=True)[:limit]
    return [read_wav(dataset / seg["file"]) for seg in segments]


def run_engine(
    name: str,
    prompt,
    wer_texts: list[str],
    hard_texts: list[str],
    out_dir: Path,
    similarity: SpeakerSimilarity,
    wer_engine: FrenchWER,
    enrollment_embedding: np.ndarray,
    device: str,
) -> dict:
    engine = engine_registry.build(name, device=device)
    audio_dir = out_dir / "audio" / name
    scores: list[SentenceScore] = []

    print(f"\n=== {name} ===")
    started = time.perf_counter()
    engine.load()
    engine.prepare(prompt)
    print(f"  chargement : {time.perf_counter() - started:.1f}s")

    for index, text in enumerate(wer_texts):
        result = engine.synth(text, prompt)
        write_wav(audio_dir / f"wer_{index:02d}.wav", result.audio, result.sample_rate)
        transcript = wer_engine.transcribe(result.audio, result.sample_rate)
        embedding = similarity.embed(result.audio, result.sample_rate)
        score = SentenceScore(
            text=text,
            transcript=transcript,
            wer=FrenchWER.wer(text, transcript),
            similarity=SpeakerSimilarity.cosine(embedding, enrollment_embedding),
            rtf=result.rtf,
            duration_s=result.duration_s,
            synth_s=result.total_s,
        )
        scores.append(score)
        print(
            f"  [{index + 1:2d}/{len(wer_texts)}] WER {score.wer * 100:5.1f} %  "
            f"sim {score.similarity:.3f}  RTF {score.rtf:.2f}"
        )

    for index, text in enumerate(hard_texts):
        try:
            result = engine.synth(text, prompt)
            write_wav(audio_dir / f"hard_{index:02d}.wav", result.audio, result.sample_rate)
        except Exception as exc:  # noqa: BLE001 - un cas dur qui plante est une donnee
            print(f"  cas difficile {index} en echec : {exc}")

    engine.unload()

    summary = summarise(scores)
    summary["license"] = engine.license
    summary["supports_french"] = engine.supports_french
    return {"engine": name, "summary": summary, "scores": [asdict(s) for s in scores]}


def render_report(payload: dict, out_path: Path) -> None:
    baseline = payload["enrollment_baseline"]
    lines = [
        "# Essai A - clonage vocal francais",
        "",
        f"Date : {payload['timestamp']}",
        f"Dataset : `{payload['dataset']}`  -  {payload['enrollment_clips']} segments d'enrolement",
        "",
        "## Calibration",
        "",
        f"Similarite de la vraie voix contre elle-meme : **{baseline:.3f}**"
        if not np.isnan(baseline)
        else "Similarite de reference indisponible (dataset trop court).",
        "",
        "C'est le plafond atteignable. Un moteur ne peut pas raisonnablement le depasser ;",
        "l'objectif est de s'en approcher, pas d'atteindre un chiffre absolu.",
        "",
        "## Resultats",
        "",
        "| Moteur | Similarite | % du plafond | WER | RTF median | Licence | FR natif |",
        "|---|---|---|---|---|---|---|",
    ]

    for entry in payload["engines"]:
        summary = entry["summary"]
        if "error" in summary:
            lines.append(f"| {entry['engine']} | echec | - | - | - | - | - |")
            continue
        sim = summary["similarity_mean"]
        wer = summary["wer_mean"]
        ratio = f"{sim / baseline * 100:.0f} %" if not np.isnan(baseline) and baseline else "-"
        lines.append(
            f"| {entry['engine']} | {sim:.3f} | {ratio} | {wer * 100:.1f} % | "
            f"{summary['rtf_median']:.2f} | {summary['license']} | "
            f"{'oui' if summary['supports_french'] else 'non'} |"
        )

    lines += ["", "## Verdict", ""]
    passed = []
    usable_baseline = not np.isnan(baseline) and baseline > 0
    for entry in payload["engines"]:
        summary = entry["summary"]
        if "error" in summary:
            lines.append(f"- **{entry['engine']}** : echec technique, `{summary['error'][:160]}`")
            continue
        sim, wer = summary["similarity_mean"], summary["wer_mean"]
        sim_ok = sim >= SIMILARITY_TARGET
        wer_ok = wer <= WER_TARGET
        ratio = sim / baseline if usable_baseline else float("nan")
        # Si le plafond n'a pas pu etre mesure, ce critere ne peut pas ecarter
        # un moteur : un seuil qu'on ne sait pas evaluer ne doit pas trancher.
        ratio_ok = (not usable_baseline) or ratio >= SIMILARITY_RATIO_TARGET

        reasons = []
        if not sim_ok:
            reasons.append(f"similarite {sim:.3f} < {SIMILARITY_TARGET}")
        if not ratio_ok:
            reasons.append(
                f"{ratio * 100:.0f} % du plafond < {SIMILARITY_RATIO_TARGET * 100:.0f} %"
            )
        if not wer_ok:
            reasons.append(f"WER {wer * 100:.1f} % > {WER_TARGET * 100:.0f} %")

        if sim_ok and wer_ok and ratio_ok:
            passed.append((entry["engine"], sim))
        detail = f" ({', '.join(reasons)})" if reasons else ""
        lines.append(f"- **{entry['engine']}** : {'retenu' if not reasons else 'ecarte'}{detail}")

    if not usable_baseline:
        lines.append(
            "- Plafond non mesurable sur ce dataset : le critere de part du plafond "
            "est neutralise. Allonger l'enregistrement pour le retablir."
        )

    lines += [""]
    if passed:
        winner = max(passed, key=lambda item: item[1])
        lines.append(f"Moteur principal : **{winner[0]}**.")
        others = [name for name, _ in passed if name != winner[0]]
        lines.append(f"Fallback : {others[0]}." if others else "Aucun fallback n'a passe les deux seuils.")
    else:
        lines.append(
            "Aucun moteur ne passe les deux seuils. Avant de changer de modele, verifier le dataset : "
            "une bande passante tronquee ou un bruit de fond eleve plafonnent tous les moteurs a la fois."
        )

    lines += [
        "",
        "## Ecoute en aveugle",
        "",
        "Les mesures ci-dessus ne remplacent pas l'oreille. Lancer :",
        "",
        "```",
        f"python listening_test.py --run {out_path.parent.name}",
        "```",
        "",
        "Protocole : 8 paires, dont 4 temoins opposant deux vrais enregistrements et",
        "4 opposant un vrai enregistrement a une synthese. Deux criteres, tous deux",
        "necessaires : au moins 75 % des temoins reconnus comme la meme personne",
        "(sinon l'auditeur repond au hasard et la mesure ne vaut rien), et au moins",
        "75 % des syntheses prises pour la vraie personne.",
        "",
        "Ecouter aussi les fichiers `hard_*.wav` : chiffres, dates et sigles sont",
        "exactement ce que le WER ne mesure pas et ou un frontend inadapte se trahit.",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, help="sortie de tools/prepare_dataset.py")
    parser.add_argument("--engines", nargs="+", default=None, help="moteurs a comparer")
    parser.add_argument("--out", type=Path, default=Path("results") / time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--whisper", default="large-v3")
    parser.add_argument("--limit", type=int, default=0, help="limiter le nombre de phrases (mise au point)")
    parser.add_argument("--list", action="store_true", help="lister les moteurs disponibles et sortir")
    args = parser.parse_args()

    if args.list:
        print("Moteurs enregistres :")
        for name in engine_registry.available():
            print(f"  {name}")
        return 0

    if not args.dataset:
        parser.error("--dataset est requis, sauf avec --list")

    dataset = args.dataset.resolve()
    reference = dataset / "reference.wav"
    if not reference.exists():
        print(f"ERREUR: {reference} absent. Lancer tools/prepare_dataset.py d'abord.", file=sys.stderr)
        return 1

    texts_dir = Path(__file__).parent / "texts"
    wer_texts = load_lines(texts_dir / "fr_wer.txt")
    hard_texts = load_lines(texts_dir / "fr_hard.txt")
    if args.limit:
        wer_texts = wer_texts[: args.limit]
        hard_texts = hard_texts[: max(1, args.limit // 4)]

    names = args.engines or engine_registry.available()
    if not names:
        print("ERREUR: aucun moteur disponible, verifier les dependances.", file=sys.stderr)
        return 1

    print(f"Dataset   : {dataset}")
    print(f"Moteurs   : {', '.join(names)}")
    print(f"Phrases   : {len(wer_texts)} scorees + {len(hard_texts)} cas difficiles")
    print(f"Sortie    : {args.out}\n")

    wer_engine = FrenchWER(device=args.device, model_size=args.whisper)
    similarity = SpeakerSimilarity(device=args.device)

    reference_text = resolve_reference_text(dataset, reference, wer_engine, args.out)
    enrollment_clips = load_enrollment(dataset)
    print(f"  enrolement sur {len(enrollment_clips)} segments")
    enrollment_embedding = similarity.enrollment(enrollment_clips)
    baseline = similarity.enrollment_baseline(enrollment_clips)
    print(f"  plafond de similarite (voix reelle contre elle-meme) : {baseline:.3f}")

    prompt = engine_registry.VoicePrompt(
        reference_wav=reference,
        reference_text=reference_text,
        dataset_dir=dataset,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for name in names:
        try:
            results.append(
                run_engine(
                    name, prompt, wer_texts, hard_texts, args.out,
                    similarity, wer_engine, enrollment_embedding, args.device,
                )
            )
        except Exception as exc:  # noqa: BLE001 - un moteur qui plante ne doit pas perdre les autres
            print(f"  ECHEC {name}: {exc}")
            traceback.print_exc(limit=3)
            results.append({"engine": name, "summary": {"error": str(exc)}, "scores": []})

    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": str(dataset),
        "enrollment_clips": len(enrollment_clips),
        "enrollment_baseline": baseline,
        "reference_text": reference_text,
        "thresholds": {"similarity": SIMILARITY_TARGET, "wer": WER_TARGET},
        "engines": results,
    }
    (args.out / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    render_report(payload, args.out / "report.md")

    print(f"\nRapport : {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
