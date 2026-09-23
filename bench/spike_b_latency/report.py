"""Rapport de l'essai B : verdict chiffre sur la latence.

    python report.py results/latency.json
    python report.py results/*.json          comparaison de plusieurs configurations
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Seuils definis dans le plan.
TOTAL_P50_TARGET_MS = 900.0
TOTAL_P90_TARGET_MS = 1400.0
BARGE_IN_TARGET_MS = 300.0

STAGE_LABELS = {
    "stt": "STT (fin de parole -> transcription)",
    "llm_ttft": "LLM (transcription -> premier token)",
    "tts_ttfb": "TTS (premier token -> premier son)",
    "total": "TOTAL (fin de parole -> premier son)",
}


def fmt(value: float | None) -> str:
    return f"{value:.0f}" if isinstance(value, (int, float)) else "-"


def render(payloads: list[tuple[Path, dict]]) -> str:
    lines = ["# Essai B - latence de la boucle conversationnelle", ""]

    for path, payload in payloads:
        summary = payload["summary"]
        context = payload.get("context", {})
        stages = summary["stages_ms"]
        total = stages.get("total", {})
        barge = summary.get("barge_in_ms", {})

        lines += [
            f"## {context.get('tts_engine', path.stem)}",
            "",
            f"Mesure du {payload['timestamp']} sur {summary['complete_turns']} tours complets "
            f"({summary['turns']} amorces).",
            "",
            "| Etage | p50 | p90 | max | n |",
            "|---|---|---|---|---|",
        ]
        for key, label in STAGE_LABELS.items():
            stage = stages.get(key, {})
            lines.append(
                f"| {label} | {fmt(stage.get('p50'))} ms | {fmt(stage.get('p90'))} ms | "
                f"{fmt(stage.get('max'))} ms | {stage.get('n', 0)} |"
            )
        lines += [
            f"| Barge-in (parole -> silence de l'avatar) | {fmt(barge.get('p50'))} ms | "
            f"{fmt(barge.get('p90'))} ms | {fmt(barge.get('max'))} ms | {barge.get('n', 0)} |",
            "",
            "Configuration : "
            + ", ".join(f"`{k}={v}`" for k, v in context.items() if v is not None),
            "",
        ]

        thin = [
            label
            for key, label in list(STAGE_LABELS.items()) + [("barge_in", "Barge-in")]
            for stage in [barge if key == "barge_in" else stages.get(key, {})]
            if stage.get("n", 0) and not stage.get("p90_reliable", True)
        ]
        if thin:
            lines += [
                f"Moins de dix mesures sur : {', '.join(thin)}. Les p90 correspondants "
                "reposent sur deux points et ne sont pas exploitables ; seuls les p50 "
                "le sont. Relancer avec davantage de tours pour les valider.",
                "",
            ]

        verdicts = []
        p50, p90 = total.get("p50"), total.get("p90")
        if p50 is None:
            verdicts.append("- Aucun tour complet mesure. Verifier que le micro et le VAD repondent.")
        else:
            verdicts.append(
                f"- Total p50 : {p50:.0f} ms, cible {TOTAL_P50_TARGET_MS:.0f} ms -> "
                + ("**tenu**" if p50 <= TOTAL_P50_TARGET_MS else "**depasse**")
            )
        if p90 is not None:
            verdicts.append(
                f"- Total p90 : {p90:.0f} ms, cible {TOTAL_P90_TARGET_MS:.0f} ms -> "
                + ("**tenu**" if p90 <= TOTAL_P90_TARGET_MS else "**depasse**")
            )
        if barge.get("p50") is not None:
            verdicts.append(
                f"- Barge-in p50 : {barge['p50']:.0f} ms, cible {BARGE_IN_TARGET_MS:.0f} ms -> "
                + ("**tenu**" if barge["p50"] <= BARGE_IN_TARGET_MS else "**depasse**")
            )
        else:
            verdicts.append(
                "- Barge-in non mesure : aucune interruption pendant la session. "
                "Refaire en coupant volontairement la parole a l'avatar."
            )

        lines += ["### Verdict", "", *verdicts, ""]

        # Le plus gros etage est l'endroit ou l'optimisation rapporte, et lui seul.
        measured = {k: v.get("p50") for k, v in stages.items() if k != "total" and v.get("p50") is not None}
        if measured and p50 and p50 > TOTAL_P50_TARGET_MS:
            worst = max(measured, key=lambda k: measured[k])
            share = measured[worst] / p50 * 100
            lines += [
                "### Ou optimiser",
                "",
                f"L'etage dominant est **{STAGE_LABELS[worst]}** : {measured[worst]:.0f} ms, "
                f"soit {share:.0f} % du budget. Agir ailleurs ne changera rien de perceptible.",
                "",
                {
                    "stt": "Pistes : distil-whisper francais ou `large-v3-turbo` a la place de "
                           "`large-v3`, ou `int8_float16` en type de calcul.",
                    "llm_ttft": "Pistes : quantification plus agressive, modele plus petit, ou vLLM "
                                "a la place d'Ollama. Verifier aussi que le modele reste charge "
                                "(`OLLAMA_KEEP_ALIVE`) : un rechargement ajoute plusieurs secondes.",
                    "tts_ttfb": "Pistes : emettre la premiere phrase des qu'elle est complete au lieu "
                                "d'attendre la fin de la reponse du LLM, et verifier que le moteur "
                                "streame vraiment. Les vocodeurs sans decodage incremental retombent "
                                "en mode fragment et perdent tout l'interet du streaming.",
                }[worst],
                "",
            ]

    if len(payloads) > 1:
        lines += ["## Comparaison", "", "| Configuration | Total p50 | Total p90 | Barge-in p50 |", "|---|---|---|---|"]
        for path, payload in payloads:
            summary = payload["summary"]
            total = summary["stages_ms"].get("total", {})
            barge = summary.get("barge_in_ms", {})
            label = payload.get("context", {}).get("tts_engine", path.stem)
            lines.append(
                f"| {label} | {fmt(total.get('p50'))} ms | {fmt(total.get('p90'))} ms | "
                f"{fmt(barge.get('p50'))} ms |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("traces", nargs="+", type=Path, help="fichiers de traces produits par bot.py")
    parser.add_argument("--out", type=Path, help="ecrire le rapport dans ce fichier")
    args = parser.parse_args()

    payloads = []
    for path in args.traces:
        if not path.exists():
            print(f"ignore : {path} introuvable", file=sys.stderr)
            continue
        payloads.append((path, json.loads(path.read_text(encoding="utf-8"))))

    if not payloads:
        print("ERREUR: aucune trace exploitable.", file=sys.stderr)
        return 1

    report = render(payloads)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"Rapport ecrit dans {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
