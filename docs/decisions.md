# Registre des decisions

Chaque entree indique ce qui a ete decide, pourquoi, et ce qu'il faudrait
observer pour revenir dessus. Les decisions qui dependent de mesures ne figurent
pas ici : elles sont produites par `bench/decide.py` dans
`docs/decisions/stack.md`.

---

## D1 - Le calcul est deporte sur un GPU loue

**Decide.**

La machine de travail est un portable Intel : i7-1360P, Iris Xe, 16 Go de RAM,
aucun GPU NVIDIA. Le cahier des charges initial concluait sur une faisabilite
"avec du materiel grand public, une ou deux RTX 3090/4090" : ce materiel
n'existe pas ici. Rien de la chaine temps reel ne peut tourner localement, et le
finetune vocal exige CUDA.

Le GPU est donc loue a l'heure. Environ 0,30 $/h, soit de l'ordre de 18 $ par
mois a deux heures d'usage quotidien. Le cout n'est pas le facteur limitant :
ce sont le **demarrage a froid** et le **stockage facture a l'arret**.

**Revenir dessus si** : acquisition d'une machine avec GPU dedie. Le portage est
sans friction, l'infrastructure etant deja en conteneurs.

---

## D2 - Le pod loue est traite comme du materiel non fiable

**Decide.**

Le cahier des charges demandait que tout reste "en local ou sur un serveur prive
controle", puis autorisait RunPod et Vast. Ces deux exigences sont
contradictoires : l'hebergeur d'un GPU loue dispose d'un acces physique a la
machine et peut lire la memoire et le disque. Le chiffrement au repos ne protege
pas d'un adversaire qui detient l'hote.

Plutot que d'ecrire "prive" partout, la repartition des donnees est inversee :

| Donnee | Emplacement |
|---|---|
| Poids des modeles | Pod (publics, sans valeur a proteger) |
| Tour de conversation en cours | Pod, en memoire, detruit apres usage |
| Dataset vocal source | Laptop uniquement |
| Historique des conversations | Laptop, chiffre |
| Memoire vectorielle | Laptop |

Le pod devient jetable : rien d'irremplacable n'y reside.

**Limite assumee** : l'hebergeur peut theoriquement observer un tour de
conversation pendant son traitement en memoire. Pour un usage prive, risque
accepte en connaissance de cause. La seule parade complete est un GPU possede.

---

## D3 - GPT-SoVITS n'est pas la brique principale

**Decide.**

Le cahier des charges imposait GPT-SoVITS "au coeur du design" pour cloner une
voix francaise. Or son frontend texte ne connait que `zh`, `en`, `ja`, `ko` et
`yue`. Il n'existe aucun module de conversion graphemes-phonemes francais, et
les modeles pre-entraines n'ont jamais vu de phoneme francais. Le timbre se
transfere depuis un audio de reference francais ; le texte francais, lui, est
phonetise par le frontend d'une autre langue.

Les candidats retenus supportent officiellement le francais : **Chatterbox
Multilingual v3** (MIT, `language_id="fr"`) et **Qwen3-TTS Base** (Apache).
GPT-SoVITS reste dans le banc comme **temoin**, pour mesurer l'ecart au lieu de
le postuler, et reste enfichable derriere la meme interface si la persona devait
parler anglais.

**Revenir dessus si** : le banc montre un WER francais acceptable pour
GPT-SoVITS, ou si un frontend G2P francais credible apparait en amont du projet.

---

## D4 - Les seuils remplacent les objectifs qualitatifs

**Decide.**

"90 % de similarite" et "latence totale inferieure a X ms" ne sont pas des
specifications : la premiere n'a pas d'echelle definie, la seconde pas de valeur.

| Critere | Seuil | Mesure par |
|---|---|---|
| Similarite locuteur | >= 0,75 absolu, et >= 90 % du plafond humain | ECAPA-TDNN, `bench/spike_a_voice/metrics.py` |
| Prononciation francaise | WER <= 8 % | faster-whisper + jiwer |
| Ecoute en aveugle | >= 75 % de confusion, temoins reconnus a >= 75 % | `listening_test.py` |
| Latence totale | p50 <= 900 ms, p90 <= 1400 ms | `spike_b_latency/instrumentation.py` |
| Barge-in | p50 <= 300 ms | idem |
| Vitesse de synthese | RTF <= 0,7 | `run_bench.py` |

Le point important est le **plafond humain** : la similarite cosinus brute n'est
pas interpretable seule. Deux enregistrements differents d'une meme personne ne
donnent pas 1,0 mais plutot 0,75 a 0,90. Le banc mesure ce plafond sur le
dataset reel et exprime chaque moteur en pourcentage de celui-ci. C'est aussi
pourquoi l'objectif "90 %" du cahier des charges etait inatteignable tel
qu'exprime : il depassait ce que la voix reelle obtient contre elle-meme.

---

## D5 - Pipecat pour l'orchestration temps reel

**Decide.**

Le cahier des charges detaillait STT, LLM, TTS et avatar, mais omettait les
briques qui font qu'un appel ressemble a un appel : detection d'activite vocale,
detection de fin de tour, interruption, annulation d'echo, transport temps reel.
Ce sont pourtant elles qui separent un appel d'un chatbot qui parle.

Pipecat les fournit nativement : `SileroVADAnalyzer` sur CPU,
`LocalSmartTurnAnalyzerV3` pour distinguer une pause d'une fin de phrase,
`allow_interruptions` pour le barge-in, `SmallWebRTCTransport` en pair a pair
sans service tiers. Le framework est agnostique du transport et se branche sur
des services locaux, ce qui preserve l'exigence de modularite.

L'annulation d'echo est deleguee au **navigateur**, en amont de l'encodage.
C'est le seul endroit ou elle fonctionne : cote serveur, le signal de reference
est deja desynchronise par le reseau.

**Alternative ecartee** : LiveKit Agents, qui embarque son propre serveur de
media. Pertinent pour du multi-participant ou de la telephonie, surdimensionne
pour un appel a deux.

---

## D6 - Avatar VRM rendu dans le navigateur

**Decide.**

Le cahier des charges melangeait deux familles incompatibles. MuseTalk, SadTalker
et Wav2Lip generent une video de tete parlante : pas de corps, cout GPU par
image, et incapacite structurelle a produire "l'avatar se leve et fait des
pompes". Live2D et VRM sont des modeles riggés rendus en temps reel.

VRM plutot que Live2D, pour trois raisons :

- le passage buste vers plein pied est un simple changement de camera, alors
  qu'en Live2D il faut un second modele ;
- les animations corps entier viennent de Mixamo et se retargettent sur le
  squelette VRM standard ;
- Live2D impose un fichier `.moc3` produit par Cubism Editor, payant, avec une
  licence SDK commerciale.

Le rendu se fait sur le GPU du client via `three-vrm`. Aucune video n'est
encodee ni transmise : seuls transitent l'audio et un flux de visemes et de tags
d'emotion, ce qui supprime un poste de latence entier.

**Reference** : Open-LLM-VTuber implemente deja persona, mapping emotion vers
expression, interruption sans casque et mode hors ligne. Son rendu est Live2D
uniquement (le support VRM est une issue ouverte), mais ses schemas sont a
reprendre. Amica couvre la partie VRM.

---

## D7 - Le canal emotion est une brique a part entiere

**Decide.**

Le LLM emet des tags structures, consommes simultanement par le TTS (choix de
l'audio de reference et de l'expressivite) et par l'avatar (expression faciale
et posture). Sans ce canal, la voix est plate et le visage desynchronise du ton
du propos.

Ce n'est pas un raffinement tardif : c'est ce qui differencie un avatar qui
parle d'un avatar qui exprime quelque chose, et cela contraint le format de
sortie du LLM des la phase 2.

---

## D8 - Le perimetre est reduit

**Decide.**

La phase 5 du cahier des charges evoquait un "monde type metaverse". Retiree.
Elle n'a aucun rapport avec le reste du systeme, multiplie la surface technique,
et aucune de ses difficultes n'est sur le chemin critique.

Les phases conservees sont : boucle vocale, persona et memoire, avatar et
lip-sync, animations corps entier, chat enrichi.
