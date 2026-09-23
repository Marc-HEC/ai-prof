"""Test d'ecoute en aveugle - essai A.

Les metriques objectives ne capturent pas tout : un clone peut obtenir une bonne
similarite cosinus et rester immediatement identifiable comme synthetique. Ce
test tranche a l'oreille.

Protocole. On presente des paires d'extraits et on demande a chaque fois "meme
personne ?". La moitie des paires oppose deux vrais enregistrements (temoins),
l'autre moitie oppose un vrai enregistrement a une synthese. L'auditeur ne sait
jamais dans quel cas il se trouve.

Deux chiffres en sortie :

- taux de confusion sur les paires synthetiques : proportion de fois ou le clone
  a ete pris pour la vraie personne. C'est le score du moteur.
- exactitude sur les temoins : si elle est basse, l'auditeur repond au hasard et
  le premier chiffre ne veut rien dire. C'est le garde-fou du protocole.

    python listening_test.py --run results/20260915-2312 --engine chatterbox-multilingual-v3
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path


def play(path: Path) -> None:
    player = shutil.which("ffplay")
    if not player:
        print(f"    (ffplay absent, ecouter manuellement : {path})")
        return
    subprocess.run(
        [player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        check=False,
    )


def ask(prompt: str, valid: set[str]) -> str:
    while True:
        answer = input(prompt).strip().lower()
        if answer in valid:
            return answer
        print(f"    reponse attendue : {' / '.join(sorted(valid))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True, help="dossier de resultats de run_bench.py")
    parser.add_argument("--engine", help="moteur a evaluer (par defaut : le premier trouve)")
    parser.add_argument("--pairs", type=int, default=8, help="nombre total de paires")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    results_path = args.run / "results.json"
    if not results_path.exists():
        print(f"ERREUR: {results_path} introuvable.", file=sys.stderr)
        return 1
    payload = json.loads(results_path.read_text(encoding="utf-8"))

    dataset = Path(payload["dataset"])
    real_clips = sorted((dataset / "segments").glob("*.wav"))
    if len(real_clips) < 4:
        print("ERREUR: pas assez de segments reels pour construire des temoins.", file=sys.stderr)
        return 1

    engine = args.engine
    if not engine:
        candidates = [e["engine"] for e in payload["engines"] if not e["summary"].get("error")]
        if not candidates:
            print("ERREUR: aucun moteur exploitable dans ce run.", file=sys.stderr)
            return 1
        engine = candidates[0]

    synth_clips = sorted((args.run / "audio" / engine).glob("wer_*.wav"))
    if not synth_clips:
        print(f"ERREUR: aucune synthese pour {engine}.", file=sys.stderr)
        return 1

    rng = random.Random(args.seed if args.seed is not None else time.time_ns())
    half = args.pairs // 2
    trials: list[tuple[str, Path, Path]] = []
    for _ in range(half):
        a, b = rng.sample(real_clips, 2)
        trials.append(("control", a, b))
    for _ in range(args.pairs - half):
        trials.append(("synth", rng.choice(real_clips), rng.choice(synth_clips)))
    rng.shuffle(trials)

    print(f"\nTest d'ecoute en aveugle - moteur evalue : {engine}")
    print(f"{len(trials)} paires. Repondre o (meme personne), n (personnes differentes), r (rejouer).\n")
    print("Utiliser un casque. Ne pas regarder les noms de fichiers.\n")

    answers: list[tuple[str, bool]] = []
    for index, (kind, first, second) in enumerate(trials, start=1):
        clips = [first, second]
        rng.shuffle(clips)
        while True:
            print(f"  Paire {index}/{len(trials)}")
            for position, clip in enumerate(clips, start=1):
                print(f"    extrait {position}...")
                play(clip)
                time.sleep(0.25)
            answer = ask("    meme personne ? [o/n/r] ", {"o", "n", "r"})
            if answer != "r":
                answers.append((kind, answer == "o"))
                print()
                break

    controls = [same for kind, same in answers if kind == "control"]
    synths = [same for kind, same in answers if kind == "synth"]
    control_accuracy = sum(controls) / len(controls) if controls else float("nan")
    confusion = sum(synths) / len(synths) if synths else float("nan")

    print("=" * 58)
    print(f"Moteur                     : {engine}")
    print(f"Temoins reconnus identiques: {sum(controls)}/{len(controls)}  ({control_accuracy * 100:.0f} %)")
    print(f"Syntheses prises pour vraies: {sum(synths)}/{len(synths)}  ({confusion * 100:.0f} %)")
    print()
    if control_accuracy < 0.75:
        print("Protocole invalide : les temoins n'ont pas ete reconnus. Refaire au casque,")
        print("dans un environnement calme. Le taux de confusion ci-dessus est ininterpretable.")
    elif confusion >= 0.75:
        print("Seuil atteint : le clone passe pour la vraie personne dans la majorite des cas.")
    else:
        print("Seuil non atteint. Ecouter les erreurs pour identifier le defaut dominant")
        print("(timbre, prosodie, artefacts, accent) avant de changer de moteur.")

    out = args.run / f"listening_{engine}.json"
    out.write_text(
        json.dumps(
            {
                "engine": engine,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "control_accuracy": control_accuracy,
                "synth_confusion_rate": confusion,
                "trials": [{"kind": kind, "judged_same": same} for kind, same in answers],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nResultat enregistre dans {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
