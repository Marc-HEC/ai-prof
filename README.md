# Compagne IA conversationnelle auto-hebergee

Systeme de conversation vocale temps reel avec avatar 3D, LLM local, et voix
clonee en francais. Tout le calcul tourne sur du materiel que l'on choisit ;
l'historique des conversations ne quitte jamais la machine de l'utilisateur.

## Etat : phase 0, validation

Rien n'est encore construit. Deux essais instrumentes valident d'abord les deux
risques capables de tuer le projet, parce qu'aucun document d'architecture ne
les reduit :

- **la fidelite du clone vocal en francais**, mesuree et non supposee ;
- **la latence bout-en-bout**, mesuree etage par etage.

La suite ne demarre que si [`bench/decide.py`](bench/decide.py) donne un feu
vert sur des seuils fixes a l'avance.

## Par ou commencer

| Vous voulez | Lire |
|---|---|
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
