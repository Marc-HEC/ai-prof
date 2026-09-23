# Infrastructure - pod GPU loue

## Pourquoi un pod loue

La machine de travail est un portable Intel sans GPU NVIDIA : i7-1360P, Iris Xe,
16 Go de RAM. Aucune brique lourde du projet ne peut y tourner. Le calcul est
donc deporte sur un GPU loue a l'heure, le portable ne gardant que l'interface,
le microphone et les donnees personnelles.

## Le modele de menace, explicitement

Un GPU loue n'est pas "un serveur prive sous mon controle". L'hebergeur dispose
d'un acces physique a la machine : il peut lire la memoire vive et le disque. Le
chiffrement au repos ne protege pas d'un adversaire qui detient l'hote.

La conception en tient compte au lieu de l'ignorer. La repartition est inversee
par rapport a un deploiement classique :

| Donnee | Emplacement | Raison |
|---|---|---|
| Poids des modeles | Pod | Publics, aucune valeur a proteger |
| Audio et texte en cours de tour | Pod, en memoire | Transitoire, detruit a la fin du tour |
| Dataset vocal source | Laptop | Donnee biometrique, ne quitte jamais la machine |
| Historique des conversations | Laptop, chiffre | Le contenu le plus sensible du projet |
| Memoire vectorielle | Laptop | Derive directement de l'historique |
| Extrait de reference vocal | Pod, en lecture seule, pendant l'essai A seulement | Quelques secondes, retire ensuite |

Consequence concrete : le pod est jetable. On le detruit apres usage, on en
recree un au besoin. Rien d'irremplacable n'y reside.

Ce que ce modele ne protege pas : l'hebergeur peut, en theorie, observer le
contenu d'un tour de conversation pendant qu'il transite en memoire. Pour un
usage prive c'est un risque accepte, et il doit l'etre en connaissance de cause.
La seule parade complete serait un GPU physiquement possede.

## Choix du fournisseur

Releve de septembre 2026, par GPU et par heure :

| GPU | VRAM | RunPod community | Vast marketplace |
|---|---|---|---|
| RTX 3090 | 24 Go | 0,22 $ | 0,08 $ |
| RTX 4090 | 24 Go | 0,34 $ | 0,11 - 0,36 $ |
| RTX A6000 | 48 Go | 0,33 $ | 0,27 $ |
| RTX 5090 | 32 Go | 0,69 $ | 0,27 $ |

Recommandation : **RTX A6000 48 Go** pour l'essai A. Le surcout par rapport a
une 4090 est negligeable, et les 48 Go evitent de jongler entre le chargement
des moteurs candidats, de Whisper large-v3 et du modele d'embedding locuteur.
Pour l'essai B et la production, une **4090 24 Go** suffit si le LLM est un 8B
quantifie.

Vast est moins cher mais c'est une place de marche d'hotes heterogenes, dont des
machines de particuliers. Pour un projet ou la confidentialite compte, RunPod
community est un compromis raisonnable ; le Secure Cloud de RunPod (datacentres
audites) coute environ le double et reste le choix le plus defendable.

Le facteur limitant n'est pas le tarif. A deux heures d'usage par jour on est
autour de 18 $ par mois. Ce sont le **stockage persistant facture a l'arret** et
le **temps de demarrage a froid** qui structurent l'experience : compter plusieurs
dizaines de secondes avant le premier mot si les modeles doivent etre recharges.
C'est une contrainte d'architecture, pas un detail d'exploitation.

## Mise en route

### 1. Creer le pod

Template RunPod : image `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`, volume
persistant de 80 Go monte sur `/workspace`.

**Retirer tous les ports exposes du template.** RunPod publie par defaut les
ports declares sur une adresse publique. L'acces se fera uniquement par Tailscale.

### 2. Rattacher le pod au tailnet

Creer une cle d'authentification ephemere et taguee sur
[login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys).
Ephemere signifie que le noeud se retire tout seul du tailnet quand le pod
disparait, ce qui evite d'accumuler des machines fantomes autorisees.

```bash
git clone <repo> /workspace/aigf && cd /workspace/aigf
export TS_AUTHKEY=tskey-auth-xxxx
bash infra/provision.sh
```

Le script installe Tailscale en mode userspace (les conteneurs de GPU loue n'ont
pas `/dev/net/tun`), verrouille l'entrant hors `tailscale0`, et signale tout
service qui ecouterait encore sur `0.0.0.0`.

### 3. Lancer les services

```bash
cp infra/.env.example infra/.env && $EDITOR infra/.env

# Essai A
docker compose -f infra/docker-compose.yml --profile spike-a up --build

# Essai A avec le temoin GPT-SoVITS
docker compose -f infra/docker-compose.yml --profile spike-a-control up -d
docker compose -f infra/docker-compose.yml --profile spike-a up --build

# Essai B
docker compose -f infra/docker-compose.yml --profile spike-b up --build
```

Tous les ports sont lies a `127.0.0.1`. Depuis le portable :

```bash
ssh -N -L 7860:localhost:7860 root@<ip-tailscale-du-pod>
```

### 4. Detruire le pod

```bash
docker compose -f infra/docker-compose.yml --profile spike-a down -v
tailscale logout
```

Puis supprimer le pod depuis la console du fournisseur et revoquer la cle. Avec
une cle ephemere la revocation est automatique, mais une verification coute
moins cher qu'un noeud oublie avec un acces permanent au tailnet.

## Verification de l'etancheite reseau

A faire une fois, apres la premiere mise en route, pour confirmer que rien ne
sort vers une API tierce pendant une conversation :

```bash
# Sur le pod, pendant un tour de conversation
tcpdump -n -i eth0 'tcp and not net 100.64.0.0/10' | head -50
```

Le trafic attendu est vide, hors telechargement initial de modeles. Toute
connexion sortante vers un domaine d'API commercial est un defaut a corriger,
pas un comportement a tolerer.
