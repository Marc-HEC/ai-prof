# bench

Harnais de mesure de la phase 0. Deux essais valident les risques qui peuvent
tuer le projet, avant qu'on construise par dessus.

Le principe : les seuils sont fixes **avant** les mesures, dans
[`decide.py`](decide.py). Decider apres coup revient a choisir le moteur qu'on
preferait deja, puis a justifier le seuil qui l'arrange.

## Essai A - clonage vocal francais

Compare plusieurs moteurs sur le meme dataset et les memes phrases.

```
cd bench/spike_a_voice
python run_bench.py --dataset ../../voices/lea/dataset --out results/run1
python listening_test.py --run results/run1
```

Trois mesures, qui repondent a trois questions distinctes :

| Mesure | Question | Outil |
|---|---|---|
| Similarite locuteur | Est-ce que ca sonne comme la bonne personne | ECAPA-TDNN |
| WER francais | Est-ce que le francais est bien prononce | faster-whisper + jiwer |
| Ecoute en aveugle | Est-ce que ca trompe une oreille humaine | protocole a temoins |

Un moteur peut exceller sur la premiere et echouer sur la deuxieme : c'est le
profil attendu d'un modele dont le frontend graphemes-phonemes ignore le
francais mais qui transfere bien le timbre. Les separer est ce qui permet de le
diagnostiquer.

Les phrases de test ne figurent pas dans le script d'enregistrement. Mesurer sur
le texte d'entrainement mesurerait la memorisation.

## Essai B - latence de la boucle

```
cd bench/spike_b_latency
python bot.py --check          # verifie l'environnement, a faire avant de louer
python bot.py --turns 20
python report.py results/latency.json
```

La sonde mesure chaque etage separement. Un chiffre global ne sert a rien pour
optimiser : savoir qu'un tour prend 1,4 s ne dit pas s'il faut changer de STT,
quantifier le LLM ou decouper autrement le texte. Le rapport designe l'etage
dominant et ne propose des pistes que pour celui-la.

### Valider la sonde sans GPU

```
python bench/spike_b_latency/selftest.py
```

Rejoue une session synthetique avec interruptions et verifie l'attribution des
etages, la coherence du total, la detection du barge-in, et l'absence de
transcription dans les traces. A lancer sur le laptop : decouvrir que
l'instrumentation est cassee une fois le pod demarre coute du temps facture a
l'heure.

## Arbitrage

```
python bench/decide.py \
    --spike-a bench/spike_a_voice/results/run1/results.json \
    --spike-b bench/spike_b_latency/results/latency.json \
    --out docs/decisions/stack.md
```

Code de sortie non nul si un seuil n'est pas tenu.

## Pourquoi ce code n'est pas jetable

Les moteurs de l'essai A implementent
[`engines/base.py`](spike_a_voice/engines/base.py), qui est le contrat
d'adaptateur TTS de production.
[`local_tts.py`](spike_b_latency/local_tts.py) le branche dans Pipecat. Le
moteur qui gagne l'essai A part en production sans etre reecrit.
