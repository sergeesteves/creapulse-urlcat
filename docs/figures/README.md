# Figures

Sources SVG des schémas publiés avec urlcat. Elles sont ici pour pouvoir être **remesurées et
corrigées**, pas seulement conservées : une fois dans un article WordPress, un SVG n'est plus
éditable autrement qu'en remplaçant le bloc entier.

| Fichier | Où il sert | Ce qu'il montre |
|---|---|---|
| `regle-eclipsee-830.svg` | page outil, section `diagram` | Le premier motif gagnant, et la règle qui n'est jamais atteinte |
| `regle-eclipsee-720.svg` | article, bloc `html` | Le même schéma, **retypographié** pour une colonne plus étroite |
| `composition-du-crawl.svg` | article | Un total d'URLs, puis le même total réparti par groupe |
| `deux-axes-structure-univers.svg` | article | La grille structure × univers : une URL occupe une seule case |
| `crawl-contre-visites.svg` | article | Part du crawl contre part des visites, par groupe (dumbbell) |

## Pourquoi deux versions du même schéma

**Un SVG est dimensionné pour une largeur de rendu, pas dans l'absolu.** La taille réellement lue
vaut `font-size × (largeur de rendu ÷ largeur du viewBox)`.

- Une section `diagram` de page outil rend à **~830 px** → échelle 1,15 pour un `viewBox` de 720.
- Une colonne d'article Enfold rend à **~720 px** → échelle 1,0.

Le même fichier passe donc le plancher de lisibilité dans un cas et échoue dans l'autre : les
libellés à 11 px de la version 830 tombent à 11 px rendus dans un article, sous le plancher de
12 px. D'où la version 720, dont toute l'échelle typographique est remontée (11 → 12, 12,5 → 14,
13 → 14, 14 → 15) et dont la géométrie a été remesurée pour vérifier qu'aucun texte ne déborde de
sa boîte après l'agrandissement.

**Ne pas « réutiliser » l'une pour l'autre sans remesurer.**

## Comment remesurer

Les largeurs de texte ne se devinent pas : `<text>` ne se renvoie jamais à la ligne, et un libellé
trop long déborde sans que rien ne le signale — ni le navigateur, ni un test. On mesure donc le
rendu réel :

1. Servir le SVG en HTTP local (le navigateur intégré n'ouvre pas les `file://`) :
   `python -m http.server 8731`
2. L'inclure dans une page qui fixe la largeur cible (720 ou 830), puis, dans la console :
   `getComputedTextLength()` sur chaque `<text>`, converti en coordonnées du `viewBox`.
3. Contrôler trois choses : aucune taille rendue sous **12 px**, aucun `x0`/`x1` hors du cadre,
   aucun chevauchement entre deux textes de même ligne de base.

## Contraintes de charte appliquées

- **Le SVG porte son propre fond** (`#f7f7f7` plein cadre) **et son filet** (1 px `--cp-rule`). La
  lightbox du site ouvre le SVG seul sur un calque quasi noir qui ne peint aucun fond : un schéma
  sans papier y disparaît.
- **Un seul élément accentué par visuel** (`#e61781`). C'est la règle de la charte, et elle prime
  sur la spec `dumbbell` de `diagram-design`, qui accentue une série entière. D'où le point plein
  en `#027dba` et non en accent : c'est le **remplissage** (plein contre creux) qui distingue les
  deux séries, pas la teinte.
- **Pas de texte en `#029ae5`** (3,11:1 sur blanc) ni en `#e61781` (4,08:1 sur le papier) : ces
  teintes ne servent qu'en aplat, filet ou contour. Tout texte est en `#111111` ou `#595959`.
- **Plus de deux catégories : décliner le bleu, et porter l'identité par les libellés.** La rampe
  `#027dba → #029ae5 → #41b3ec → #80ccf2 → #c0e6f8` est un encodage **séquentiel** (une grandeur
  ordonnée). Passée au validateur de palette, elle échoue comme palette catégorielle — 7,6 d'écart
  perceptuel entre deux paliers voisins, indistinguables même en vision normale.

## Les chiffres

Ceux des trois figures d'article sont des **exemples**, pas les relevés d'un site réel, et chaque
légende le dit. Ils servent à montrer une forme de lecture. À remplacer par des chiffres réels
anonymisés dès qu'on en a : ce serait meilleur pour la crédibilité du propos.
