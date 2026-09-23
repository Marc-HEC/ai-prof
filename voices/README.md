# voices

Datasets vocaux et modeles finetunes, une arborescence par persona.

**Ce dossier est exclu du depot.** Il contient une donnee biometrique : la voix
d'une personne identifiable. Elle ne quitte pas le laptop, et n'est montee sur
le pod qu'en lecture seule, le temps de l'essai A.

## Structure

```
voices/
  <persona>/
    raw/              enregistrements bruts, tels que sortis du micro
    dataset/          produit par tools/prepare_dataset.py
      segments/       decoupe sur les silences, 48 kHz mono
      reference.wav   meilleur extrait, choisi sur le SNR
      reference.txt   optionnel, voir ci-dessous
      manifest.json   mesures de qualite par fichier source
    models/           modeles finetunes, si le moteur retenu en produit
```

Le banc a besoin de la transcription de `reference.wav`. Il la genere lui-meme et
la met en cache dans son dossier de resultats, parce que `voices/` est monte en
lecture seule sur le pod : une donnee biometrique n'a pas a etre modifiable par
le calcul. Deposer un `reference.txt` a la main dans le dataset reste possible et
prioritaire, ce qui permet de corriger une transcription approximative.

## Constituer un dataset

1. Lire [`tools/record_script_fr.md`](../tools/record_script_fr.md) avant
   d'enregistrer. Les consignes de prise de son y pesent plus lourd que le
   choix du moteur : un moteur moyen sur un bon enregistrement battra un bon
   moteur sur des vocaux compresses.
2. Deposer les fichiers dans `voices/<persona>/raw/`.
3. Lancer :

```
python tools/prepare_dataset.py voices/<persona>/raw --out voices/<persona>/dataset
```

L'outil refuse le dataset si la bande passante est tronquee, si le bruit de fond
est trop haut ou si la duree de parole est insuffisante. Ces refus ne sont pas
des formalites : ils correspondent a des plafonds de qualite dont aucun moteur
ne peut se remettre en aval.

## Consentement

Cloner la voix d'une personne reelle suppose son accord. La question se pose
avant de remplir ce dossier.
