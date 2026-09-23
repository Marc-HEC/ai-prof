"""Arbitrage de la stack a partir des mesures des deux essais.

Ce script applique des seuils fixes AVANT d'avoir vu les donnees. C'est
volontaire : decider apres coup revient a choisir le modele qu'on preferait
deja, puis a justifier le seuil qui l'arrange.

    python bench/decide.py \
        --spike-a bench/spike_a_voice/results/latest/results.json \
        --spike-b bench/spike_b_latency/results/latency.json \
        --out docs/decisions/stack.md

Sortie : un verdict par seuil, la stack retenue, et la liste des points bloquants
avec la marche a suivre pour chacun.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# --- Seuils, engages a l'avance ---------------------------------------------
SIMILARITY_MIN = 0.75
SIMILARITY_RATIO_MIN = 0.90  # part du plafond humain, critere prioritaire
WER_MAX = 0.08
LISTENING_CONFUSION_MIN = 0.75
TOTAL_P50_MAX_MS = 900.0
TOTAL_P90_MAX_MS = 1400.0
BARGE_IN_P50_MAX_MS = 300.0
RTF_MAX = 0.7  # au dela, le TTS ne tient pas le temps reel sur du texte long


@dataclass
class Check:
    name: str
    passed: bool | None  # None = non mesure
    detail: str
    remedy: str = ""

    @property
    def mark(self) -> str:
        return {True: "tenu", False: "NON TENU", None: "non mesure"}[self.passed]


def load(path: Path | None) -> dict | None:
    if path is None:
        return None
    if not path.exists():
        print(f"attention : {path} introuvable, essai considere comme non realise", file=sys.stderr)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_listening(run_dir: Path, engine: str) -> dict | None:
    path = run_dir / f"listening_{engine}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_voice(payload: dict | None, run_dir: Path | None) -> tuple[list[Check], str | None, str | None]:
    if payload is None:
        return (
            [Check("Essai A realise", None, "aucun resultat", "lancer bench/spike_a_voice/run_bench.py")],
            None,
            None,
        )

    checks: list[Check] = []
    baseline = payload.get("enrollment_baseline")
    # NaN est vrai au sens booleen : tester explicitement.
    usable_baseline = isinstance(baseline, (int, float)) and baseline == baseline and baseline > 0
    ranked: list[tuple[str, float, float]] = []

    if usable_baseline:
        checks.append(
            Check("Plafond de similarite mesure", True, f"{baseline:.3f} (voix reelle contre elle-meme)")
        )
    else:
        checks.append(
            Check(
                "Plafond de similarite mesure",
                None,
                "non mesurable, dataset trop court",
                "allonger l'enregistrement ; sans plafond, le critere de part du plafond est neutralise",
            )
        )

    for entry in payload["engines"]:
        summary = entry["summary"]
        name = entry["engine"]
        if summary.get("error"):
            checks.append(
                Check(
                    f"Moteur {name}",
                    None,
                    f"echec technique : {summary['error'][:120]}",
                    "corriger l'installation ou retirer ce moteur de la comparaison",
                )
            )
            continue

        sim, wer = summary["similarity_mean"], summary["wer_mean"]
        ratio = sim / baseline if usable_baseline else float("nan")
        reasons = []
        if sim < SIMILARITY_MIN:
            reasons.append(f"similarite {sim:.3f} < {SIMILARITY_MIN}")
        # Un seuil qu'on ne sait pas evaluer ne doit pas ecarter un candidat.
        if usable_baseline and ratio < SIMILARITY_RATIO_MIN:
            reasons.append(f"{ratio * 100:.0f} % du plafond < {SIMILARITY_RATIO_MIN * 100:.0f} %")
        if wer > WER_MAX:
            reasons.append(f"WER {wer * 100:.1f} % > {WER_MAX * 100:.0f} %")

        ratio_text = f" ({ratio * 100:.0f} % du plafond)" if usable_baseline else ""
        checks.append(
            Check(
                f"Moteur {name}",
                not reasons,
                f"similarite {sim:.3f}{ratio_text}, WER {wer * 100:.1f} %, "
                f"RTF {summary['rtf_median']:.2f}",
                "; ".join(reasons),
            )
        )

        # Ecoute en aveugle. Deux conditions : l'auditeur doit avoir reconnu les
        # temoins, sinon il repondait au hasard et le taux de confusion ne veut
        # rien dire ; et la synthese doit tromper assez souvent.
        listening = load_listening(run_dir, name) if run_dir else None
        if listening is None:
            checks.append(
                Check(
                    f"Ecoute en aveugle {name}",
                    None,
                    "non realisee",
                    f"lancer listening_test.py --engine {name}",
                )
            )
        else:
            control = listening.get("control_accuracy") or 0.0
            confusion = listening.get("synth_confusion_rate") or 0.0
            if control < LISTENING_CONFUSION_MIN:
                checks.append(
                    Check(
                        f"Ecoute en aveugle {name}",
                        None,
                        f"protocole invalide : temoins reconnus a {control * 100:.0f} %",
                        "refaire au casque dans un environnement calme",
                    )
                )
            else:
                checks.append(
                    Check(
                        f"Ecoute en aveugle {name}",
                        confusion >= LISTENING_CONFUSION_MIN,
                        f"{confusion * 100:.0f} % de confusion (temoins a {control * 100:.0f} %)",
                        "identifier le defaut dominant a l'ecoute : timbre, prosodie, artefacts, accent",
                    )
                )
                if confusion < LISTENING_CONFUSION_MIN:
                    reasons.append("ecoute en aveugle sous le seuil")

        if not reasons:
            ranked.append((name, sim, summary["rtf_median"]))

    ranked.sort(key=lambda item: item[1], reverse=True)
    realtime = [name for name, _, rtf in ranked if rtf <= RTF_MAX]
    winner = realtime[0] if realtime else (ranked[0][0] if ranked else None)
    fallback = next((name for name, _, _ in ranked if name != winner), None)

    if ranked and not realtime:
        checks.append(
            Check(
                "Vitesse de synthese",
                False,
                f"aucun moteur sous RTF {RTF_MAX}",
                "le TTS ne suivra pas en conversation ; reduire la taille du modele ou "
                "decouper le texte plus finement avant synthese",
            )
        )
    return checks, winner, fallback


def evaluate_latency(payload: dict | None) -> list[Check]:
    if payload is None:
        return [Check("Essai B realise", None, "aucun resultat", "lancer bench/spike_b_latency/bot.py")]

    summary = payload["summary"]
    stages = summary["stages_ms"]
    total = stages.get("total", {})
    barge = summary.get("barge_in_ms", {})
    checks: list[Check] = []

    p50 = total.get("p50")
    checks.append(
        Check(
            "Latence totale p50",
            None if p50 is None else p50 <= TOTAL_P50_MAX_MS,
            f"{p50:.0f} ms" if p50 is not None else "non mesuree",
            "identifier l'etage dominant dans le rapport de l'essai B et n'agir que sur lui",
        )
    )
    p90 = total.get("p90")
    checks.append(
        Check(
            "Latence totale p90",
            None if p90 is None else p90 <= TOTAL_P90_MAX_MS,
            f"{p90:.0f} ms" if p90 is not None else "non mesuree",
            "un p90 degrade alors que le p50 tient signale une variabilite : "
            "verifier le dechargement du modele et la contention GPU",
        )
    )
    barge_p50 = barge.get("p50")
    checks.append(
        Check(
            "Barge-in p50",
            None if barge_p50 is None else barge_p50 <= BARGE_IN_P50_MAX_MS,
            f"{barge_p50:.0f} ms sur {barge.get('n', 0)} interruptions"
            if barge_p50 is not None
            else "aucune interruption pendant la session",
            "verifier allow_interruptions et l'emission du TTS par trames courtes",
        )
    )

    for key, label in (("stt", "STT"), ("llm_ttft", "LLM"), ("tts_ttfb", "TTS")):
        stage = stages.get(key, {})
        if stage.get("p50") is not None:
            checks.append(Check(f"Etage {label}", True, f"p50 {stage['p50']:.0f} ms"))
    return checks


def render(voice: list[Check], latency: list[Check], winner: str | None, fallback: str | None) -> str:
    lines = [
        "# Arbitrage de la stack",
        "",
        "Verdict genere par `bench/decide.py` a partir des mesures des essais A et B.",
        "Les seuils sont ceux fixes dans le plan, avant toute mesure.",
        "",
        "## Clonage vocal (essai A)",
        "",
        "| Critere | Resultat | Verdict |",
        "|---|---|---|",
    ]
    lines += [f"| {c.name} | {c.detail} | {c.mark} |" for c in voice]
    lines += [
        "",
        "## Latence (essai B)",
        "",
        "| Critere | Resultat | Verdict |",
        "|---|---|---|",
    ]
    lines += [f"| {c.name} | {c.detail} | {c.mark} |" for c in latency]

    blocking = [c for c in voice + latency if c.passed is False]
    pending = [c for c in voice + latency if c.passed is None]

    lines += ["", "## Stack retenue", ""]
    if winner:
        lines.append(f"- Moteur TTS principal : **{winner}**")
        lines.append(f"- Fallback : **{fallback}**" if fallback else "- Fallback : aucun second moteur ne passe les seuils")
    else:
        lines.append("- Moteur TTS : **non arbitre**, aucun candidat ne passe les deux seuils")
    lines += [
        "- Orchestration : Pipecat (VAD Silero, SmartTurn v3, barge-in, SmallWebRTC)",
        "- STT : faster-whisper, francais",
        "- LLM : Ollama, modele 8B quantifie",
        "- Avatar : VRM rendu par three-vrm dans le navigateur",
        "",
    ]

    if blocking:
        lines += ["## Points bloquants", ""]
        for check in blocking:
            lines.append(f"- **{check.name}** : {check.detail}")
            if check.remedy:
                lines.append(f"  - {check.remedy}")
        lines.append("")
    if pending:
        lines += ["## Mesures manquantes", ""]
        lines += [f"- {c.name} : {c.remedy or c.detail}" for c in pending]
        lines.append("")

    if not blocking and not pending:
        lines += [
            "## Feu vert",
            "",
            "Tous les seuils sont tenus. La phase 1 (boucle vocale complete, sans avatar)",
            "peut demarrer sur cette base.",
            "",
        ]
    else:
        lines += [
            "## Conclusion",
            "",
            "La phase 1 ne demarre pas tant que les points ci-dessus ne sont pas leves.",
            "Construire l'avatar par dessus une boucle vocale qui ne tient pas ses seuils",
            "revient a rendre le probleme plus cher a corriger.",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spike-a", type=Path, help="results.json de l'essai A")
    parser.add_argument("--spike-b", type=Path, help="traces de l'essai B")
    parser.add_argument("--out", type=Path, help="fichier de sortie")
    args = parser.parse_args()

    run_dir = args.spike_a.parent if args.spike_a else None
    voice, winner, fallback = evaluate_voice(load(args.spike_a), run_dir)
    latency = evaluate_latency(load(args.spike_b))
    document = render(voice, latency, winner, fallback)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(document, encoding="utf-8")
        print(f"Arbitrage ecrit dans {args.out}")
    print(document)

    return 1 if any(c.passed is False for c in voice + latency) else 0


if __name__ == "__main__":
    raise SystemExit(main())
