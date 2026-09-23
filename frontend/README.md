# frontend

Interface web. Construite en phase 1 pour l'audio, etendue en phase 3 pour
l'avatar. Vide pour l'instant.

Pendant l'essai B, c'est le client de developpement fourni par Pipecat qui sert
d'interface : il suffit a mesurer la latence, et ecrire une UI avant de savoir
si la boucle tient ses seuils serait prematuré.

## Pourquoi le navigateur plutot qu'une application native

Ce n'est pas un choix par defaut. Le navigateur apporte gratuitement
l'annulation d'echo, la suppression de bruit, le controle automatique de gain et
une pile WebRTC eprouvee. Les reimplementer dans Electron ou Tauri serait un
travail considerable pour un resultat inferieur.

L'annulation d'echo en particulier n'est pas optionnelle : sans elle, en mains
libres, le micro capte la voix de l'avatar, le STT la transcrit, et le systeme
se repond a lui-meme. Elle ne fonctionne qu'au plus pres de la capture, avant
l'encodage.

## Pile envisagee

React ou Svelte, three.js avec `three-vrm` pour l'avatar, l'API WebRTC native
pour l'audio. Aucune de ces briques n'est encore engagee.

## Organisation de l'ecran

L'avatar occupe la zone principale, avec bascule buste et plein pied. Le chat
textuel est dans une colonne laterale : historique, images, extraits audio. Les
controles (micro, scene, persona) sont en bas.

Un element a ne pas oublier : **l'etat du pod**. Eteint, en demarrage, pret. Le
demarrage a froid prend plusieurs dizaines de secondes, et sans indicateur
l'utilisateur parle dans le vide.

## Contrainte technique

Le HTTPS est obligatoire des que la page n'est pas servie depuis `localhost` :
les navigateurs n'autorisent l'acces au micro qu'en contexte securise. En
developpement, passer par un tunnel SSH vers `localhost` evite d'avoir a gerer
un certificat.
