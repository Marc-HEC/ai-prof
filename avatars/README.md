# avatars

Modeles 3D, animations et scenes. Construit en phase 3, vide pour l'instant.

## Format retenu : VRM

Decision detaillee en [D6](../docs/decisions.md). En resume : le passage buste
vers plein pied est un simple changement de camera, les animations Mixamo se
retargettent sur le squelette VRM qui est standardise, et Live2D imposerait un
`.moc3` produit par Cubism Editor, payant, avec une licence SDK commerciale.

Ecarte : MuseTalk, SadTalker, Wav2Lip. Ces modeles generent une video de tete
parlante. Pas de corps, cout GPU par image, et incapacite structurelle a
produire "l'avatar se leve et fait des pompes".

## Structure cible

```
avatars/
  <persona>/
    model.vrm             modele, avec blendshapes de visemes
    expressions.json      mapping tag d'emotion -> blendshapes
    animations/           .fbx Mixamo retargetes
  scenes/                 decors, eclairage, cadrages camera
```

Les binaires lourds sont exclus du depot.

## Sources de modeles

VRoid Studio (gratuit, exporte du VRM nativement) pour un personnage sur mesure.
VRoid Hub et Booth pour des modeles existants ; verifier la licence, beaucoup
interdisent la modification ou l'usage commercial.

Mixamo pour les animations : les telecharger sans skin, puis retargeter sur le
squelette VRM.

## Le point a ne pas rater

Le modele doit exposer les blendshapes de visemes du standard VRM (`aa`, `ih`,
`ou`, `ee`, `oh`) et un jeu d'expressions (`happy`, `angry`, `sad`, `relaxed`,
`surprised`). Un modele sans ces blendshapes ne peut pas faire de lip-sync, et
le decouvrir en phase 3 coute une reprise complete de l'asset.
