# Script d'enregistrement - dataset vocal francais

Objectif : 5 a 10 minutes de parole propre, couvrant l'inventaire phonetique du
francais, pour servir de base au clonage vocal (essai A).

La qualite de cet enregistrement pese plus lourd que le choix du modele TTS.
Un modele mediocre sur un bon dataset battra systematiquement un bon modele sur
des vocaux WhatsApp.

## 1. Conditions techniques

| Parametre | Cible | Pourquoi |
|---|---|---|
| Format | WAV PCM 16 ou 24 bits | Aucune compression avec perte |
| Echantillonnage | 48 kHz | Les modeles resamplent eux-memes vers le bas, jamais vers le haut |
| Canaux | Mono | Le stereo double la taille sans rien apporter |
| Niveau crete | entre -6 et -3 dBFS | De la marge avant saturation |
| Bruit de fond | <= -50 dBFS | En dessous, le modele apprend le souffle |
| Distance micro | 15 a 20 cm, legerement hors axe | Evite les plosives |

A eviter absolument :

- Le micro du casque Bluetooth ou du laptop. Bande passante tronquee, traitement
  agressif, compression. Un micro USB d'entree de gamme fait mieux.
- Toute piece avec de l'echo (carrelage, murs nus, grande piece vide). Enregistrer
  dans une piece meublee, ou face a un lit / une penderie ouverte.
- La climatisation, le ventilateur du PC, le frigo. Les couper pendant la prise.
- Le traitement logiciel amont : desactiver reduction de bruit, AGC, "clarte vocale"
  du pilote audio. Le preprocessing se fait apres, de facon controlee.

## 2. Consignes de lecture

- Ton conversationnel, pas ton de lecture de journal televise. Le modele reproduira
  exactement le registre enregistre : lire de facon plate donne une voix plate.
- Debit naturel. Ne pas ralentir artificiellement pour bien articuler.
- Laisser 0,5 a 1 seconde de silence entre chaque phrase. Cela permet la decoupe
  automatique.
- En cas d'erreur : marquer 2 secondes de silence et reprendre la phrase entiere.
  Ne pas couper l'enregistrement.
- Faire une pause toutes les 2 minutes. La voix fatiguee derive en timbre.
- Enregistrer en une seule session. Deux sessions a deux jours d'ecart donnent
  deux timbres legerement differents, ce qui brouille l'apprentissage.

## 3. Script de lecture

### Bloc A - voyelles orales

1. La lumiere du matin filtre a travers les rideaux de la chambre.
2. Il a pris le dernier train pour rentrer chez lui avant minuit.
3. Elle repete que cette idee ne mene nulle part.
4. Le bateau glisse doucement sur l'eau calme du port.
5. Tu devrais gouter cette soupe, elle est vraiment excellente.
6. Nous avons visite le musee du Louvre un mardi pluvieux.
7. Il pleut sur la ville depuis le debut de la semaine.
8. Ce vieux fauteuil bleu vient de la maison de ma grand-mere.

### Bloc B - voyelles nasales

9. Pendant longtemps, j'ai pense que le temps arrangeait tout.
10. Un bon vin blanc accompagne bien ce plat du dimanche.
11. Cinq cents personnes attendaient devant l'entree principale.
12. Mon cousin Vincent habite maintenant en Bretagne.
13. L'enfant chantait une chanson ancienne dans le jardin.
14. Comment comptes-tu rentrer sans argent ni telephone ?

### Bloc C - semi-voyelles et groupes difficiles

15. Hier soir, la pluie tombait sur les tuiles du toit.
16. Lui aussi croyait que le travail serait fini aujourd'hui.
17. Le bruit du moteur diesel resonnait dans la ruelle.
18. Il faudrait que tu essaies avant de juger.
19. Cette huile d'olive vient directement d'un village italien.
20. Nous nous voyions chaque juillet au bord de la riviere.

### Bloc D - consonnes et enchainements

21. Le chercheur suisse a publie ses resultats jeudi dernier.
22. Georges cherche un logement proche de la gare Saint-Lazare.
23. La grange rouge se dresse au bout du chemin de terre.
24. Quatre-vingt-dix pour cent des reponses etaient incorrectes.
25. Ce champagne coute beaucoup trop cher pour ce qu'il est.
26. L'agneau et la montagne sont deux mots avec le meme son.

### Bloc E - liaisons obligatoires et interdites

27. Les enfants ont attendu pendant deux heures et demie.
28. Nous avons ete tres heureux d'apprendre cette nouvelle.
29. C'est un grand homme, mais un mauvais ami.
30. Ils ont oublie leurs affaires dans un ancien hotel.
31. Quand il arrive, tout le monde se tait immediatement.
32. Vous etes arrives en avance, ce qui est rare.

### Bloc F - chiffres, dates, noms propres

33. Le rendez-vous est fixe au 17 mars 2026 a 14 h 30.
34. Il me doit encore 1 245 euros depuis novembre.
35. Appelle-moi au 06 12 34 56 78 des que tu peux.
36. Marie, Sophie et Alexandre viendront de Bordeaux en voiture.
37. La temperature est descendue a moins 8 degres cette nuit.
38. Le fichier fait 3,7 gigaoctets, ce qui est enorme.

### Bloc G - registre conversationnel et emotion

Ces phrases comptent double : elles determinent si la voix clonee sait faire autre
chose que reciter. Les jouer, ne pas les lire.

39. Attends, tu es serieux la ? Tu as vraiment fait ca ?
40. Bon... d'accord. Je crois que j'ai compris ou tu veux en venir.
41. Franchement, ca me fait super plaisir de te voir.
42. Non mais je revais ou il vient de raccrocher au nez ?
43. Ecoute, je suis creve, on en reparle demain d'accord ?
44. Ah oui ! J'avais completement oublie ce detail, merci.
45. Ca va aller, ne t'inquiete pas. Vraiment, ca va aller.
46. Mmh, je sais pas trop... laisse-moi y reflechir un peu.

### Bloc H - phrases longues

Pour apprendre la prosodie sur la duree et la respiration.

47. Ce que je trouve fascinant dans cette histoire, c'est qu'elle a commence par
    un simple malentendu entre deux personnes qui ne se connaissaient meme pas,
    et qu'elle s'est terminee des annees plus tard de la maniere la plus
    inattendue qui soit.
48. Si tu prends l'autoroute jusqu'a la sortie 23, puis la departementale sur
    environ douze kilometres, tu vas tomber sur un petit rond-point avec une
    fontaine au milieu ; prends la deuxieme a droite et c'est la troisieme maison.
49. Je ne dis pas que tu as tort, je dis simplement que la situation est
    probablement plus compliquee que ce que tu imagines, et qu'il vaudrait
    peut-etre mieux attendre d'avoir tous les elements avant de decider.
50. Elle m'a explique, avec une patience que je ne lui connaissais pas, pourquoi
    il etait important de recommencer depuis le debut plutot que de rafistoler
    quelque chose qui de toute facon finirait par lacher.

## 4. Apres l'enregistrement

Deposer le ou les fichiers bruts dans `voices/<persona>/raw/`, puis lancer :

```
python tools/prepare_dataset.py voices/<persona>/raw --out voices/<persona>/dataset
```

Le script convertit au bon format, mesure la qualite reelle, refuse le dataset
s'il est en dessous des seuils, et extrait automatiquement le meilleur extrait de
reference pour les modeles zero-shot.
