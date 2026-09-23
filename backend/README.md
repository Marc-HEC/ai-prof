# backend

Orchestration de la boucle conversationnelle et adaptateurs vers les briques
interchangeables.

## Etat actuel

Le projet est en phase 0 : validation par la mesure avant construction. Ce
dossier contient donc uniquement ce qui est deja utilise.

```
personas/          definitions de personnalite, chargees par le pipeline
  default.md       persona de reference, utilisee par l'essai B
```

Le contrat d'adaptateur TTS existe deja et tourne :
[`bench/spike_a_voice/engines/base.py`](../bench/spike_a_voice/engines/base.py).
Il est volontairement identique a celui qui sera utilise en production, et
[`bench/spike_b_latency/local_tts.py`](../bench/spike_b_latency/local_tts.py)
le branche dans Pipecat. Le banc d'essai n'est pas du code jetable : le moteur
qui gagne l'essai A se retrouve en production sans reecriture.

## Structure cible, a partir de la phase 1

```
adapters/
  llm/             Ollama, vLLM, llama.cpp derriere une interface commune
  stt/             faster-whisper, autres moteurs
  tts/             promotion de bench/spike_a_voice/engines
  avatar/          emission des visemes et des tags d'emotion
pipeline/          assemblage Pipecat, gestion des tours, interruption
memory/            contexte court, resume, memoire vectorielle
personas/          definitions de personnalite
api/               signalisation WebRTC, canal de donnees du chat
```

## Regles qui ne changent pas

Aucun etat de conversation n'est ecrit sur le pod. L'historique et la memoire
vectorielle vivent cote client (decision D2 dans
[docs/decisions.md](../docs/decisions.md)). Un adaptateur qui persisterait des
messages cote serveur serait un defaut, pas une optimisation.

Chaque brique est remplacable sans toucher aux autres. Un adaptateur qui laisse
fuir un detail de son implementation dans le pipeline casse cette propriete.
