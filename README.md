# Compagne IA et prof IA, auto-heberges

Systeme de conversation vocale temps reel avec avatar, LLM local et memoire,
en francais. Le meme code fait une compagne dans l'esprit de Replika ou
Otherhalf, et un prof en visio avec tableau blanc et recherche web. Le calcul
lourd tourne sur un GPU loue (RunPod) ; l'historique et la memoire restent sur
la machine de l'utilisateur.

## Etat

**Utilisable : l'app V1, dans [`app/`](app/README.md).** Interface web, memoire
et avatar sur le PC ; LLM, Whisper et voix sur le pod RunPod, par tunnel SSH.

- Compagne : voix continue avec coupure de parole, chat avec images et messages
  vocaux, avatar VRM, memoire avec oubli progressif, personnalite qui evolue.
- Prof : la meme app avec `--persona persona/prof.yaml`. Tableau blanc (notes
  au feutre et schemas), recherche web (desactivee par defaut), avatar 2D anime,
  mode visio a partager dans Teams.

**En cours : la phase 0**, qui mesure deux risques avant d'aller plus loin que
la V1 :

- **la fidelite du clone vocal en francais**, mesuree et non supposee ;
- **la latence bout-en-bout**, mesuree etage par etage.

La suite (voix clonee, latence garantie) ne demarre que si
[`bench/decide.py`](bench/decide.py) donne un feu vert sur des seuils fixes a
l'avance. Ce qu'il reste a faire, dans l'ordre : [docs/suite.md](docs/suite.md).

## Par ou commencer

| Vous voulez | Lire |
|---|---|
| Lancer la compagne ou le prof | [app/README.md](app/README.md) |
| Savoir quoi faire ensuite | [docs/suite.md](docs/suite.md) |
| Comprendre le systeme cible | [docs/architecture.md](docs/architecture.md) |
| Savoir pourquoi tel choix | [docs/decisions.md](docs/decisions.md) |
| Enregistrer la voix | [tools/record_script_fr.md](tools/record_script_fr.md) |
| Lancer les mesures | [bench/README.md](bench/README.md) |
| Monter le pod GPU | [infra/README.md](infra/README.md) |

## Demarrage

```bash
# 1. Preparer le dataset vocal, sur le laptop
python tools/prepare_dataset.py voices/lea/raw --out voices/lea/dataset

# 2. Valider l'instrumentation, sur le laptop, avant de payer du GPU
python bench/spike_b_latency/selftest.py

# 3. Sur le pod GPU
export TS_AUTHKEY=tskey-auth-xxxx
bash infra/provision.sh
docker compose -f infra/docker-compose.yml --profile spike-a up --build

# 4. Arbitrer
python bench/decide.py --spike-a ... --spike-b ... --out docs/decisions/stack.md
```

## Quatre corrections par rapport au cahier des charges initial

Le projet part d'un cahier des charges dont quatre hypotheses ne resistaient pas
a la verification. Elles sont corrigees et documentees dans
[docs/decisions.md](docs/decisions.md) :

**GPT-SoVITS ne synthetise pas le francais.** Son frontend ne connait que `zh`,
`en`, `ja`, `ko` et `yue`. Il devient temoin de mesure ; les candidats sont
Chatterbox Multilingual v3 et Qwen3-TTS Base, qui supportent officiellement le
francais.

**La machine de travail n'a pas de GPU NVIDIA.** Le calcul part sur un GPU loue,
environ 0,30 $/h.

**Un GPU loue n'est pas "prive".** L'hebergeur a un acces physique. Le pod
recoit donc les modeles et le calcul, jamais l'historique ni le dataset vocal.

**"90 % de similarite" et "latence < X ms" n'etaient pas des specifications.**
Elles sont remplacees par des seuils mesurables, et la similarite est exprimee
en pourcentage du plafond que la voix reelle atteint contre elle-meme.

## Briques absentes du cahier des charges initial

Ce sont elles qui separent un appel d'un chatbot qui parle, et elles sont
couvertes par Pipecat : detection d'activite vocale, detection de fin de tour,
interruption en cours de parole, annulation d'echo, transport WebRTC, canal
d'emotion, tracage de latence par etage.

## Confidentialite

L'historique des conversations, la memoire vectorielle et le dataset vocal
restent sur la machine de l'utilisateur, chiffres. Le pod de calcul est jetable
et ne conserve rien. Aucun appel vers une API tierce dans le chemin de
production, verifiable au `tcpdump`.

Ce que cela ne protege pas est ecrit noir sur blanc dans
[docs/architecture.md, section 7](docs/architecture.md).
