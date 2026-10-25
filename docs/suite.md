# La suite, dans l'ordre

Ce qui est fait : l'app V1 dans [`app/`](../app/README.md), compagne et prof,
avec 24 tests qui passent sans GPU. Ce qui suit demande ton pod RunPod, ton PC
Windows ou une décision de ta part. Chaque étape dit comment savoir qu'elle est
finie.

## 1. Brancher l'app sur ton pod (30 min)

Le pod est celui du guide [V1 compagne IA via RunPod](https://claude.ai/artifact/XU3sdgTMzDoZZJLpp1yJMd) :
KoboldCpp (LLM et Whisper) et AllTalk (voix), joints par le tunnel SSH.

1. Sur ton PC : `git clone` du dépôt, puis l'installation de
   [app/README.md](../app/README.md#installation-sur-ton-pc).
2. `copy config.example.yaml config.yaml`, puis vérifie les ports : 5001 pour
   KoboldCpp, 7851 pour AllTalk, et le nom de ta voix dans `tts.voice`.
3. Tunnel ouvert, lance `python -m app.main` et ouvre http://127.0.0.1:8765.

**Fini quand** : tu parles, elle répond à voix haute, et elle se tait quand tu
lui coupes la parole.

## 2. Essayer le prof (20 min)

1. `python -m app.main --persona persona/prof.yaml`
2. Demande « Explique-moi le théorème de Pythagore avec un schéma ».
3. Pour la recherche web : `recherche_web.enabled: true` dans `config.yaml`,
   puis `pip install ddgs trafilatura` si ce n'est pas déjà fait. Demande une
   actualité.

**Fini quand** : le tableau se remplit pendant qu'il parle, rien du bloc
`<tableau>` n'est lu à voix haute, et les sources s'affichent sous le tableau.

Si le modèle oublie les blocs ou les lit à voix haute, c'est le LLM qui est trop
petit pour suivre les consignes. Le modèle de la compagne peut suffire. Sinon,
essaie un modèle plus solide en français, par exemple Mistral Small 3.2 24B en
GGUF Q4_K_M dans KoboldCpp, sur un GPU de 48 Go (A40 ou A6000).

## 3. L'avatar du prof (1 à 2 h)

Prépare les calques PNG décrits dans
[app/README.md](../app/README.md#mode-prof) : une illustration anime à toi,
découpée dans Krita. Le visage provisoire sert seulement à vérifier que la
bouche suit la voix.

**Fini quand** : la bouche bouge en rythme, les yeux clignent, et l'expression
change avec `[emotion:joie]`.

## 4. Un cours test sur Teams (30 min)

Suis [Faire cours sur Teams](../app/README.md#faire-cours-sur-teams), avec ton
téléphone comme élève dans la même réunion.

**Fini quand** : depuis le téléphone, on voit l'avatar et le tableau, on entend
le prof, et le prof répond à ce qui est dit au téléphone.

## 5. Mesurer avant d'aller plus loin (phase 0)

Les essais de [bench/](../bench/README.md) décident de la voix clonée et de la
suite de l'architecture. Deux points à savoir avant de les lancer :

- **L'essai B ne tourne pas en l'état avec Pipecat 1.x.** Son import principal
  est corrigé, mais `SmallWebRTCTransport` exige maintenant une connexion créée
  par un serveur de signalisation. Il faut soit passer le script sur le runner
  de Pipecat (`pipecat.runner.run`), soit figer une version de Pipecat
  antérieure à 1.0 dans `bench/spike_b_latency/requirements.txt`.
- **`infra/` suppose Docker et WebRTC sur le pod.** Sur RunPod, le pod est déjà
  un conteneur (pas de Docker dedans) et seuls les ports TCP sont exposés (pas
  l'UDP du WebRTC). Voir la décision D9 dans [decisions.md](decisions.md).
  L'essai A, lui, peut tourner directement dans un venv sur le pod.

## Ensuite, au choix

Par ordre d'intérêt, une fois la V1 validée :

| Idée | Pour | Effort |
|---|---|---|
| Tableau collaboratif (Excalidraw) | Les élèves dessinent aussi | 1 jour |
| Le prof voit le tableau | Il corrige ce que l'élève dessine (modèle avec vision) | 1 jour |
| Tes cours en mémoire | Il s'appuie sur tes PDF et fiches plutôt que sur internet | 2 jours |
| Voix clonée du prof | Après l'essai A, brancher le moteur gagnant derrière AllTalk ou en direct | 1 jour |
| Avatar 2D plus vivant | Boucles LivePortrait pré-générées à partir de l'illustration | 1 jour |
| Vrai participant Teams | Un bot qui rejoint la réunion seul (API Microsoft Graph, Azure) | 1 à 2 semaines |
