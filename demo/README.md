# Démo sans GPU

Une version jouable de la cible décrite dans [docs/architecture.md](../docs/architecture.md) :
tu parles au prof, il te répond à voix haute avec l'avatar VRM synchronisé sur sa voix, et il
écrit au tableau. Aucun GPU n'est nécessaire : les briques GPU de l'architecture sont
remplacées par des API gratuites ou des modèles qui tournent sur CPU.

Cette démo ne remplace pas la phase 0. Elle sert à montrer le produit et à valider
l'interface pendant que les essais A et B tranchent la pile finale. Chaque brique
reste interchangeable par une variable d'environnement.

## Lancer

```bash
cp demo/.env.example demo/.env     # puis y coller une clé gratuite (voir plus bas)
python demo/start.py
```

La première fois, `start.py` installe ce qui manque avec pip, télécharge l'avatar VRM
d'exemple (10 Mo) et la voix Piper (60 Mo), puis ouvre `http://localhost:8000`.

Sans clé, la démo tourne en **mode mock** : réponses factices, voix synthétique, mais
l'avatar, le lip-sync, le micro, l'interruption et le tableau fonctionnent. Pratique pour
vérifier l'installation : `python demo/start.py --mock`.

`python demo/start.py --check` vérifie la clé et liste les modèles disponibles.

## Les briques

| Brique de l'architecture | Sur le pod GPU | Dans la démo | Où |
|---|---|---|---|
| VAD | Silero, Pipecat | Silero dans le navigateur (vad-web), repli sur un seuil d'énergie | Navigateur |
| Annulation d'écho | Navigateur | Navigateur (`echoCancellation`) | Navigateur |
| STT | faster-whisper large-v3 | Whisper large-v3-turbo par l'API Groq, ou faster-whisper `small` sur CPU | API ou laptop |
| LLM | Ollama 8B | Toute API compatible OpenAI : Groq, Gemini, OpenAI, ou Ollama en local | API ou laptop |
| Vision | aucune | Gemini (dessins du tableau, photos de cours) | API |
| Canal émotion (D7) | à construire | Tags `[emotion:joie]` du LLM, vers l'expression VRM et le débit de la voix | Serveur |
| TTS | clone, essai A | Piper `fr_FR-siwis-medium` sur CPU, ou edge-tts | Laptop ou API |
| Avatar (D6) | three-vrm | three-vrm, lip-sync par énergie spectrale, clignements, cadrage buste ou plein pied | Navigateur |
| Tableau | absent | Markdown et LaTeX (KaTeX) écrits par le prof, dessin de l'élève envoyé au modèle vision | Navigateur |
| Mémoire | laptop, chiffrée | Historique dans l'onglet et supports de cours renvoyés à chaque tour | Navigateur |

### Les clés gratuites

- **Groq**, sur [console.groq.com/keys](https://console.groq.com/keys). C'est le plus rapide pour le LLM et le STT. Par défaut, le modèle est `openai/gpt-oss-120b`, avec un raisonnement réglé sur `low`.
- **Gemini**, sur [aistudio.google.com/apikey](https://aistudio.google.com/apikey). Il sait lire les images, donc c'est lui qui gère les dessins du tableau et les photos de cours.

Avec les deux clés, Groq parle et Gemini voit. Avec une seule, la démo s'adapte. Les
identifiants de modèles changent souvent. Si `--check` indique qu'un modèle est absent,
choisis-en un dans la liste affichée et renseigne-le dans `LLM_MODEL=` ou `VISION_MODEL=`.

### Tout en local, sans aucune API

```bash
# demo/.env
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:3b     # installer Ollama puis : ollama pull qwen2.5:3b
STT_ENGINE=local
TTS_ENGINE=piper
```

Sur l'i7-1360P, compte plusieurs secondes par réponse : la démo reste utilisable, mais on
n'a plus l'impression d'un appel. C'est ce que la location de GPU viendra corriger.

## Utilisation

- **Micro** : active-le, puis parle normalement. La fin de phrase est détectée toute seule.
- **Interruption** : si tu parles pendant que le prof parle, il s'arrête. En haut-parleurs,
  l'annulation d'écho du navigateur fait l'essentiel du travail. Avec un casque, c'est parfait.
  Tu peux décocher l'option si le prof se coupe lui-même.
- **Cours** : ajoute un PDF, un fichier texte ou Markdown, ou une photo. Le texte est
  envoyé au prof à chaque tour, qui reprend tes notations.
- **Tableau** : le prof y écrit les formules et les exercices. Avec *Stylo*, tu dessines ;
  *Montrer au prof* joint ton dessin à ta prochaine question.
- **Persona** : `prof` (Camille, prof particulière) ou `default` (Léa). Un fichier dans
  `backend/personas/` suffit pour en ajouter une.
- En bas, les latences du dernier tour (STT, premier token, fin de parole jusqu'au premier son)
  sont affichées étage par étage, dans l'esprit de l'essai B.

## Vers la cible

Rien n'est jetable. Le jour où le GPU arrive :

- `LLM_PROVIDER=ollama` avec `LLM_BASE_URL` qui pointe vers le pod, et le LLM passe sur le GPU sans toucher au code ;
- le moteur retenu à l'essai A se branche dans `demo/tts.py`, qui a la même forme d'interface : une phrase en entrée, de l'audio en sortie ;
- le parseur `stream_parser.py`, avec l'émotion, les phrases et le tableau, et tout le frontend se réutilisent tels quels dans le pipeline Pipecat.

## Confidentialité

Avec des API, les tours de conversation et les cours partent chez Groq et Google, ce qui
contredit la règle « aucun appel tiers » du chemin de production. C'est un choix fait
pour la démo, en connaissance de cause, et il ne s'applique pas à la cible. Le mode tout en local
ci-dessus respecte la règle. Le serveur ne stocke rien : l'historique vit dans l'onglet
et disparaît quand on le ferme.

## Tests

```bash
python demo/tests/test_demo.py
```

Ils couvrent le découpage du flux LLM, avec l'émotion, les phrases et le tableau quel que
soit le découpage des tokens, ainsi que tous les endpoints du serveur en mode mock.
