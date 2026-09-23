# models

Scripts de telechargement et configurations. Aucun poids n'est commite.

## Ce qui sera telecharge

| Brique | Modele | Taille | Ou |
|---|---|---|---|
| STT | `faster-whisper large-v3` | ~3 Go | Pod |
| LLM | 8B quantifie Q4_K_M, GGUF | ~5 Go | Pod, via Ollama |
| TTS | selon l'issue de l'essai A | 2 a 5 Go | Pod |
| Similarite locuteur | `speechbrain/spkrec-ecapa-voxceleb` | ~80 Mo | Pod, essai A uniquement |
| VAD | Silero, ONNX | ~2 Mo | Laptop, embarque avec Pipecat |
| Fin de tour | SmartTurn v3, ONNX | ~50 Mo | Laptop, embarque avec Pipecat |

Total sur le pod : environ 15 Go de poids, plus les caches. Le volume persistant
est dimensionne a 80 Go dans [`infra/README.md`](../infra/README.md).

## Caches

Les caches Hugging Face et Torch pointent vers le volume persistant, configure
par [`provision.sh`](../infra/provision.sh). Sans cela, chaque recreation de pod
retelecharge 15 Go, ce qui coute plus cher en temps GPU facture que le stockage
economise.
