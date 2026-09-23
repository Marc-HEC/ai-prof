"""Validation de la sonde de latence sans GPU ni Pipecat.

Rejoue une session synthetique de tours de parole, avec une interruption, et
verifie que la sonde attribue correctement les etages et que le rapport se rend.

A lancer sur le portable avant de louer du GPU : decouvrir que l'instrumentation
est cassee une fois le pod demarre coute du temps facture a l'heure.

    python selftest.py
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import report as report_module  # noqa: E402
from instrumentation import LatencyProbe  # noqa: E402

# Latences simulees, en secondes, proches de ce qu'on attend d'une 4090.
STAGES = {"stt": (0.11, 0.05), "llm": (0.20, 0.08), "tts": (0.33, 0.14)}

_CLASSES: dict[str, type] = {}


def frame(name: str, text: str = ""):
    """Fabrique une trame dont seul le nom de classe compte pour la sonde."""
    cls = _CLASSES.setdefault(name, type(name, (object,), {}))
    instance = cls()
    instance.text = text
    return instance


def simulate(probe: LatencyProbe, turns: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    for index in range(turns):
        probe.observe("UserStartedSpeakingFrame", frame("UserStartedSpeakingFrame"))
        time.sleep(0.005)
        probe.observe("UserStoppedSpeakingFrame", frame("UserStoppedSpeakingFrame"))

        base, spread = STAGES["stt"]
        time.sleep(base + rng.random() * spread)
        probe.observe("TranscriptionFrame", frame("TranscriptionFrame", f"phrase numero {index}"))

        base, spread = STAGES["llm"]
        time.sleep(base + rng.random() * spread)
        probe.observe("LLMTextFrame", frame("LLMTextFrame"))

        base, spread = STAGES["tts"]
        time.sleep(base + rng.random() * spread)
        probe.observe("TTSAudioRawFrame", frame("TTSAudioRawFrame"))
        probe.observe("BotStartedSpeakingFrame", frame("BotStartedSpeakingFrame"))

        for _ in range(5):
            time.sleep(0.01)
            probe.observe("TTSAudioRawFrame", frame("TTSAudioRawFrame"))

        # Un tour sur trois, l'utilisateur coupe la parole a l'avatar.
        if index % 3 == 2:
            probe.observe("UserStartedSpeakingFrame", frame("UserStartedSpeakingFrame"))
            time.sleep(0.04)
            probe.observe("TTSAudioRawFrame", frame("TTSAudioRawFrame"))

        probe.observe("BotStoppedSpeakingFrame", frame("BotStoppedSpeakingFrame"))


def main() -> int:
    probe = LatencyProbe(label="selftest", verbose=False)
    simulate(probe, turns=9)
    summary = probe.summary()

    failures: list[str] = []
    if summary["complete_turns"] != 9:
        failures.append(f"9 tours attendus, {summary['complete_turns']} mesures")

    stages = summary["stages_ms"]
    expectations = {"stt": 110, "llm_ttft": 200, "tts_ttfb": 330}
    for key, floor in expectations.items():
        p50 = stages.get(key, {}).get("p50")
        if p50 is None:
            failures.append(f"etage {key} non mesure")
        elif not floor <= p50 <= floor * 2.2:
            failures.append(f"etage {key} : p50 {p50:.0f} ms hors de l'intervalle attendu")

    total = stages.get("total", {}).get("p50")
    parts = sum(stages[k]["p50"] for k in expectations if stages.get(k, {}).get("p50"))
    if total is None:
        failures.append("total non mesure")
    elif abs(total - parts) > 80:
        failures.append(f"total {total:.0f} ms incoherent avec la somme des etages {parts:.0f} ms")

    barge = summary.get("barge_in_ms", {})
    if barge.get("n", 0) != 3:
        failures.append(f"3 interruptions attendues, {barge.get('n', 0)} detectees")
    elif not 20 <= barge["p50"] <= 200:
        failures.append(f"barge-in p50 aberrant : {barge['p50']:.0f} ms")

    out = Path(tempfile.mkdtemp()) / "latency.json"
    probe.save(out, context={"tts_engine": "selftest", "llm_model": "n/a", "whisper": "n/a"})
    raw = out.read_text(encoding="utf-8")

    if "phrase numero" in raw:
        failures.append("fuite de confidentialite : les transcriptions sont ecrites dans les traces")

    rendered = report_module.render([(out, json.loads(raw))])
    if "TOTAL" not in rendered or "Verdict" not in rendered:
        failures.append("le rapport ne se rend pas correctement")

    print(rendered)
    print("=" * 62)
    for key, label in (("stt", "STT"), ("llm_ttft", "LLM"), ("tts_ttfb", "TTS"), ("total", "TOTAL")):
        stage = stages.get(key, {})
        print(f"  {label:6s} p50 {stage.get('p50', float('nan')):6.0f} ms   p90 {stage.get('p90', float('nan')):6.0f} ms")
    print(f"  barge  p50 {barge.get('p50', float('nan')):6.0f} ms   sur {barge.get('n', 0)} interruptions")

    if failures:
        print("\nECHEC :")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nSonde et rapport valides.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
