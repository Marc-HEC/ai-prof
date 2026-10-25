# Compagne : app vocale avec avatar 3D

Une app de compagne IA dans l'esprit d'Otherhalf ou Replika, mais qui tourne chez toi. L'interface, la mémoire et l'avatar sont sur ton PC. Le LLM, Whisper et la voix tournent sur ton pod RunPod, reliés par un tunnel SSH.

## Ce que fait l'app

- **Conversation vocale en temps réel.** Le micro est écouté en continu et la fin de ta phrase est détectée automatiquement. La réponse est envoyée phrase par phrase à la voix, et la première phrase peut être coupée à la virgule pour qu'elle commence à parler plus tôt.
- **Interruption.** Si tu parles pendant qu'elle parle, elle s'arrête : la génération est annulée et la lecture coupée. Ce qu'elle a eu le temps de dire reste dans l'historique avec « … ».
- **Chat à côté**, façon live Twitch : texte, images (le modèle doit gérer la vision) et messages vocaux (fichiers audio transcrits par Whisper).
- **Avatar VRM** : respiration, clignements, regard vers toi, lip-sync calé sur le volume de sa voix, expressions, gestes et vue buste ou plein pied.
- **Le LLM pilote le corps** avec des balises invisibles : `[emotion:joie]`, `[action:danse]`, `[camera:plein_pied]`.
- **Mémoire vivante**, dans un fichier SQLite sur ton PC :
  - **Souvenirs.** Après chaque session, ou tous les 12 échanges, elle relit la conversation et en garde un souvenir daté, noté selon son importance.
  - **Faits sur toi**, protégés de l'oubli et sans doublons.
  - **Oubli progressif.** Un souvenir faible devient flou, puis il est effacé. Les souvenirs importants et souvent rappelés restent.
  - **Personnalité qui évolue.** Son humeur, son affection et sa confiance bougent par petits pas, plafonnés pour qu'elle ne change pas de caractère en une soirée. Les notes d'évolution sont gardées.
  - **Une vie entre les sessions.** Si tu reviens après plus de 6 h, elle s'écrit une petite journée et arrive avec ses propres sujets.
  - **Notion du temps** : l'heure, le jour, et depuis combien de temps vous ne vous êtes pas parlé.

## Installation sur ton PC

Il faut avoir suivi le guide [V1 compagne IA via RunPod](https://claude.ai/artifact/XU3sdgTMzDoZZJLpp1yJMd) jusqu'à l'étape 5 : KoboldCpp et AllTalk tournent sur le pod et le tunnel SSH est ouvert. SillyTavern n'est plus nécessaire, cette app le remplace.

Prérequis : Python 3.11 ou plus, et Node.js.

```powershell
cd compagne-app\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.yaml config.yaml

cd ..\frontend
npm install
```

Ensuite :

1. **Avatar :** exporte ton personnage depuis VRoid Studio en `.vrm` et place-le dans `frontend/avatar/model.vrm`.
2. **Personnage :** copie `backend/persona/exemple.yaml`, remplis-le avec ton perso (l'âge doit être de 18 ans ou plus), puis indique le chemin du fichier dans `config.yaml` (`persona:`).
3. **Voix :** dans `config.yaml`, mets dans `tts.voice` le nom du fichier de voix présent dans `alltalk_tts/voices/` sur le pod.
4. **Lancement**, tunnel ouvert :

```powershell
cd compagne-app\backend
.venv\Scripts\activate
python -m app.main
```

5. Ouvre **http://127.0.0.1:8765** dans Chrome ou Edge, puis clique sur « Activer le micro ».

Mets un casque : l'annulation d'écho du navigateur aide, mais sur haut-parleurs le micro peut entendre sa voix et croire que tu l'interromps.

## Gestes et animations

Sans fichier d'animation, les gestes sont procéduraux (salut, hoche, rire, réfléchit, étirement, danse). Pour des mouvements réalistes, dépose des fichiers `.vrma` (VRM Animation) nommés comme l'action dans `frontend/animations/`, par exemple `danse.vrma` ou `salut.vrma`. Ils sont utilisés en priorité. Pour en obtenir :

- convertir des FBX Mixamo avec un outil comme `mixamo-to-vrma` ou l'add-on VRM pour Blender ;
- en exporter depuis VRM Posing Desktop ;
- ou enregistrer ta propre capture de mouvement.

Les actions disponibles sont dans `backend/app/persona.py` (`ACTIONS`). Si tu en ajoutes une, ajoute aussi son geste dans `frontend/avatar.js`, ou un fichier `.vrma` du même nom.

## Réglages utiles (`config.yaml`)

| Réglage | Effet |
| --- | --- |
| `vad.end_silence_ms` | Silence qui marque la fin de ta phrase. 400 = plus réactif, 700 = te coupe moins. |
| `vad.barge_in_ms` | Durée de parole avant de l'interrompre. Monte-la si elle s'arrête pour un bruit. |
| `llm.history_turns` | Nombre d'échanges gardés tels quels. Au-delà, c'est la mémoire qui prend le relais. |
| `memory.half_life_days` | Vitesse d'oubli : un souvenir sans rappel perd la moitié de sa force en ce nombre de jours. |
| `memory.reflect_every_turns` | Fréquence des réflexions qui créent souvenirs et faits. |

## Confidentialité

- Le serveur écoute sur `127.0.0.1` uniquement. Il avertit si tu changes ça.
- La mémoire (`backend/data/memoire.db`), la fiche et l'historique restent sur ton PC. Le pod ne reçoit que la requête du moment.
- Les librairies de l'avatar sont installées par `npm install`, dans `node_modules`. Aucun CDN n'est appelé pendant l'utilisation.
- Au premier lancement, `fastembed` télécharge une fois le modèle d'embeddings multilingue (~120 Mo) depuis Hugging Face, puis travaille hors ligne. Sans lui, la mémoire utilise une recherche par mots-clés.
- Range le dossier hors de OneDrive, et chiffre le disque.

## Architecture

```
navigateur (127.0.0.1:8765)                    backend local (FastAPI)                     pod RunPod (tunnel SSH)
 micro PCM16 16 kHz  ── WebSocket ──▶  détection de parole (WebRTC VAD)
                                         ├─ fin de phrase ─────────────▶ Whisper (KoboldCpp)
                                         ├─ prompt + souvenirs + état ──▶ LLM en flux (KoboldCpp)
                                         ├─ phrase par phrase ──────────▶ voix (AllTalk XTTS/RVC)
 lecture + lip-sync  ◀── audio WAV ──────┘
 avatar VRM ◀── balises emotion / action / camera
 chat ◀── texte au fil de l'eau                 mémoire SQLite (souvenirs, faits, journal, état)
```

| Fichier | Rôle |
| --- | --- |
| `backend/app/session.py` | Une conversation : flux, interruption, réflexion, journal |
| `backend/app/memory.py` | Souvenirs, faits, oubli |
| `backend/app/persona.py` | Fiche personnage, état évolutif, prompt système |
| `backend/app/audio.py` | Détection de parole et d'interruption |
| `backend/app/text.py` | Découpage en phrases, balises |
| `backend/app/services.py` | Clients KoboldCpp et AllTalk (format OpenAI) |
| `frontend/avatar.js` | Rendu VRM, lip-sync, expressions, gestes, caméra |
| `frontend/audio.js` | Micro, lecture, messages vocaux, images |

## Tests

```powershell
pip install -r requirements-dev.txt
pytest
```

Les tests couvrent le découpage en phrases, les balises, la mémoire et l'oubli, la détection de parole, un tour complet de conversation (texte, balises, audio, image), l'interruption et la réflexion de fin de session. Ils utilisent de faux services et n'ont pas besoin de GPU.

## Limites connues et suite

- **Lip-sync basé sur le volume :** la bouche s'ouvre avec la voix, mais ne forme pas chaque son. Pour mieux faire, il faudrait des visèmes : AllTalk ne les fournit pas, mais un analyseur de fréquences côté navigateur peut en estimer.
- **Pas encore de selfies :** brancher ComfyUI (étape « Pour aller plus loin » du guide) et une balise `[selfie:description]`.
- **Pas de détection d'émotion dans ta voix :** seul le texte transcrit est analysé.
- **Un seul utilisateur et un seul personnage** par dossier de données.
