# Backlog

Ce qui est décidé mais pas fait. Une entrée = un problème réel, pas une idée en l'air.

## Générer les règles automatiquement

**Le manque.** urlcat classe des URLs **à partir d'un fichier de règles qu'on écrit soi-même**.
C'est le bon outil quand on sait déjà comment on veut ranger un site — mais sur un site qu'on
découvre, la première écriture de règles est justement le travail long. L'outil n'aide pas à la
faire : il aide seulement à la corriger.

**Ce qu'il faudrait.** À partir d'un corpus d'URLs seul, proposer un jeu de règles de départ :
repérer les segments de chemin récurrents, les paramètres de query qui marquent une pagination
ou un filtre, les motifs numériques (`/produit/42`), et en déduire des groupes nommés, qu'on
corrige ensuite à la main dans l'atelier existant. Le résultat n'a pas à être parfait : il doit
faire gagner la première heure.

**Pourquoi ça change le modèle économique.** C'est la partie qui demande du calcul et de
l'inférence, pas seulement de l'appariement de motifs. C'est donc elle qui justifierait un
paiement, là où la classification à partir de règles fournies ne le justifie pas — un Actor
Apify facturé à l'usage n'a de sens qu'avec cette étape (décision de Serge, 2026-10-14).

**Ce que ça n'est pas.** Ni une reclassification automatique sans relecture, ni un remplacement
du fichier de règles : le fichier reste la source de vérité, lisible et versionnable, et la
génération n'en est qu'un point de départ.

**Dépendances.** Rien côté moteur : la génération produit un fichier de règles, que `parse_rules`
lit déjà. Elle vit donc à côté du moteur, pas dedans.
