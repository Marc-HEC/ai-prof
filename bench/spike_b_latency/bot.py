"""Essai B - boucle conversationnelle temps reel instrumentee.

Pipeline mesure :

    micro (WebRTC)
      -> Silero VAD            detection de parole, sur CPU
      -> faster-whisper        transcription francaise
      -> SmartTurn v3          fin de tour reelle, pas simple silence
      -> LLM local (Ollama)    persona et contexte
      -> TTS clone             moteur retenu a l'essai A
      -> haut-parleurs (WebRTC)

Les trois briques que le cahier des charges initial avait oubliees sont ici, et
ce sont elles qui font la difference entre un appel et un chatbot qui parle :

- la detection de fin de tour, parce qu'un silence de 300 ms ne signifie pas que
  l'utilisateur a fini sa phrase ;
- l'interruption, pour pouvoir couper l'avatar en parlant ;
- l'annulation d'echo, deleguee au navigateur, sans quoi le micro capte la voix
  de l'avatar et la transcrit en boucle.

Preambule :

    python bot.py --check        verifie l'environnement sans rien lancer
    python bot.py --turns 20     tourne, mesure 20 tours, ecrit le rapport
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Port de signalisation WebRTC. Fixe par le runner de developpement Pipecat ;
# il doit correspondre a l'EXPOSE de infra/Dockerfile.runtime et au mapping de
# infra/docker-compose.yml. Ce n'est volontairement pas une option en ligne de
# commande : elle donnerait l'illusion de pouvoir le changer d'un seul endroit.
SIGNALLING_PORT = 7860

DEFAULT_PERSONA = """Tu es une compagne francaise de 27 ans. Tu parles a l'oral,
dans une conversation telephonique. Tes reponses sont courtes : une a trois
phrases, jamais plus. Tu n'utilises ni listes, ni puces, ni emoji, ni markdown,
parce que tout ce que tu ecris sera lu a voix haute. Tu poses des questions, tu
reagis, tu n'es pas un assistant. Tu tutoies."""


def load_persona(name: str) -> str:
    """Charge backend/personas/<name>.md, avec repli sur la persona par defaut."""
    path = Path(__file__).resolve().parents[2] / "backend" / "personas" / f"{name}.md"
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return DEFAULT_PERSONA


def check_environment() -> int:
    """Preflight : verifie que chaque import resout avant de louer du GPU a l'heure."""
    checks = [
        ("Pipeline", "pipecat.pipeline.pipeline", "Pipeline"),
        ("PipelineTask", "pipecat.pipeline.task", "PipelineTask"),
        ("PipelineRunner", "pipecat.pipeline.runner", "PipelineRunner"),
        ("VAD Silero", "pipecat.audio.vad.silero", "SileroVADAnalyzer"),
        ("Fin de tour", "pipecat.audio.turn.smart_turn.local_smart_turn_v3", "LocalSmartTurnAnalyzerV3"),
        ("Transport WebRTC", "pipecat.transports.smallwebrtc.transport", "SmallWebRTCTransport"),
        ("STT Whisper", "pipecat.services.whisper.stt", "WhisperSTTService"),
        ("LLM Ollama", "pipecat.services.ollama.llm", "OLLamaLLMService"),
        ("Contexte LLM", "pipecat.processors.aggregators.llm_context", "LLMContext"),
    ]
    print("Verification de l'environnement\n")
    missing = 0
    for label, module, symbol in checks:
        try:
            mod = __import__(module, fromlist=[symbol])
            getattr(mod, symbol)
            print(f"  ok    {label:18s} {module}.{symbol}")
        except Exception as exc:  # noqa: BLE001
            missing += 1
            print(f"  MANQUE {label:18s} {module}.{symbol}  ({type(exc).__name__}: {exc})")

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spike_a_voice"))
        import engines as registry

        print(f"\n  Moteurs TTS disponibles : {', '.join(registry.available()) or 'aucun'}")
        if not registry.available():
            missing += 1
    except Exception as exc:  # noqa: BLE001
        print(f"\n  Moteurs TTS indisponibles : {exc}")
        missing += 1

    if missing:
        print(
            f"\n{missing} element(s) manquant(s). Pipecat reorganise regulierement ses modules :\n"
            "  pip show pipecat-ai\n"
            "  python -c \"import pipecat, pkgutil; print([m.name for m in pkgutil.iter_modules(pipecat.__path__)])\"\n"
            "puis ajuster les imports en tete de ce fichier."
        )
    else:
        print("\nEnvironnement complet.")
    return 1 if missing else 0


async def run(args: argparse.Namespace) -> int:
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response import LLMContextAggregatorPair
    from pipecat.services.ollama.llm import OLLamaLLMService
    from pipecat.services.whisper.stt import WhisperSTTService
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spike_a_voice"))
    import engines as registry

    from instrumentation import LatencyProbe
    from local_tts import LocalCloneTTSService

    dataset = Path(args.dataset).resolve()
    reference = dataset / "reference.wav"
    reference_text = (dataset / "reference.txt").read_text(encoding="utf-8").strip() \
        if (dataset / "reference.txt").exists() else ""
    if not reference.exists():
        print(f"ERREUR: {reference} absent. Lancer tools/prepare_dataset.py.", file=sys.stderr)
        return 1

    prompt = registry.VoicePrompt(
        reference_wav=reference, reference_text=reference_text, dataset_dir=dataset
    )

    transport = SmallWebRTCTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Aucun filtre audio cote serveur, volontairement. L'annulation
            # d'echo est faite par le navigateur, en amont de l'encodage : c'est
            # le seul endroit ou elle fonctionne, le signal de reference etant
            # deja desynchronise une fois passe par le reseau.
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    # 200 ms : en dessous on coupe au milieu des hesitations,
                    # au dessus la latence percue augmente d'autant.
                    stop_secs=args.vad_stop_secs,
                )
            ),
            turn_analyzer=LocalSmartTurnAnalyzerV3(),
        ),
    )

    stt = WhisperSTTService(model=args.whisper, device=args.device, language="fr")
    llm = OLLamaLLMService(model=args.llm_model, base_url=args.ollama_url)
    tts = LocalCloneTTSService(args.tts_engine, prompt, device=args.device)

    context = LLMContext(
        messages=[{"role": "system", "content": load_persona(args.persona)}],
    )
    aggregators = LLMContextAggregatorPair(context)
    probe = LatencyProbe(label=f"{args.tts_engine}|{args.llm_model}|{args.whisper}")

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            probe,  # juste avant la sortie : mesure ce qui part reellement
            transport.output(),
            aggregators.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,  # le barge-in
            enable_metrics=True,
            enable_usage_metrics=False,
        ),
    )

    stop = asyncio.Event()

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnect(_transport, _client):  # noqa: ANN001
        stop.set()
        await task.cancel()

    async def watchdog() -> None:
        """Arrete la session apres N tours complets. Sort aussi sur `stop`."""
        if not args.turns:
            await stop.wait()
            return
        while not stop.is_set():
            await asyncio.sleep(1.0)
            if len([t for t in probe.turns if t.complete()]) >= args.turns:
                print(f"\n{args.turns} tours mesures, arret.")
                stop.set()
                await task.cancel()
                return

    print(f"\nSignalisation sur http://localhost:{SIGNALLING_PORT}")
    print(f"Depuis le laptop : ssh -N -L {SIGNALLING_PORT}:localhost:{SIGNALLING_PORT} root@<ip-tailscale>")
    print(f"Ouvrir ensuite http://localhost:{SIGNALLING_PORT} dans le navigateur, AU CASQUE.\n")
    print("  Ordre des colonnes : STT, LLM (TTFT), TTS (TTFB), TOTAL, en ms\n")

    runner = PipelineRunner(handle_sigint=True)
    runner_task = asyncio.create_task(runner.run(task), name="runner")
    watchdog_task = asyncio.create_task(watchdog(), name="watchdog")

    failure: BaseException | None = None
    try:
        # FIRST_COMPLETED et non gather : le runner peut s'arreter seul, sur
        # Ctrl+C ou sur erreur, sans que `stop` soit pose. Un gather attendrait
        # alors le watchdog indefiniment et la session de mesure serait perdue.
        done, pending = await asyncio.wait(
            {runner_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for pending_task in pending:
            pending_task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for finished in done:
            if not finished.cancelled() and finished.exception() is not None:
                failure = finished.exception()
    finally:
        # Les traces sont ecrites quoi qu'il arrive : une session interrompue a
        # mi-parcours reste exploitable, la perdre non.
        out = Path(args.out)
        probe.save(
            out,
            context={
                "tts_engine": args.tts_engine,
                "llm_model": args.llm_model,
                "whisper": args.whisper,
                "vad_stop_secs": args.vad_stop_secs,
                "device": args.device,
            },
        )
        complete = len([t for t in probe.turns if t.complete()])
        print(f"\n{complete} tour(s) complet(s). Traces ecrites dans {out}")
        print(f"Rapport : python report.py {out}")

    if failure is not None:
        print(f"\nLa session s'est terminee sur une erreur : {failure}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verifier l'environnement et sortir")
    parser.add_argument("--dataset", default=os.environ.get("DATASET", "/workspace/voices/default/dataset"))
    parser.add_argument("--tts-engine", default=os.environ.get("TTS_ENGINE", "chatterbox-multilingual-v3"))
    parser.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "hermes3:8b"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--whisper", default=os.environ.get("WHISPER_MODEL", "large-v3"))
    parser.add_argument("--persona", default=os.environ.get("PERSONA", "default"))
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--vad-stop-secs", type=float, default=0.2)
    parser.add_argument("--turns", type=int, default=20, help="arret automatique apres N tours complets")
    parser.add_argument("--out", default="results/latency.json")
    args = parser.parse_args()

    if args.check:
        return check_environment()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
