# urlcat — catégorisation d'URLs par groupe / parent / univers

Classe une liste d'URLs à partir d'un fichier de règles au format EzCat
(sections `[crawl:categories]` et `[crawl:univers]`), et **dit lesquelles de vos
règles ne gagnent jamais**.

Deux façons de l'utiliser, **un seul moteur** :

| | |
|---|---|
| **CLI** — `urlcat.py` | Python 3, **stdlib uniquement**, aucune installation, aucune limite d'URLs. |
| **Service web** — `app/` | FastAPI, pour l'outil en ligne embarqué dans une page du site. Plafonné. |

Le service n'a **aucune logique de classement à lui** : il appelle `urlcat.py`, le
même fichier que la CLI, couvert par les 54 vérifications de `tests/verifie.py`.
Un défaut de classement se corrige dans `urlcat.py`, jamais dans `app/`.

---

## La CLI

```bash
python urlcat.py --rules cat-client.txt --urls urls.txt --out classement.csv
```

Avec la page de contrôle et le diagnostic des règles :

```bash
python urlcat.py --rules cat-client.txt --urls urls.txt --out classement.csv --html apercu.html --diagnose
```

### Options

| Option | Effet |
| --- | --- |
| `--rules` | fichier de catégorisation (obligatoire) |
| `--urls` | liste d'URLs, une par ligne — ou CSV avec `--url-column` |
| `--out` | CSV de sortie (défaut `classement.csv`) |
| `--html` | page de contrôle avec listes déroulantes par groupe / univers |
| `--report` | écrit aussi le rapport texte dans un fichier |
| `--panel PCT` | **défaut 100** = tout. Abaisse pour n'exporter qu'un échantillon |
| `--panel-per-group N` | panel stratifié : jusqu'à N URLs par couple groupe × univers |
| `--seed` | graine du tirage — même graine, même panel |
| `--ignore-dom` | ignore la directive `dom` (règles écrites pour un autre hôte) |
| `--diagnose` | dit quelle règle capte les URLs d'une autre |
| `--explain URL` | détaille le classement d'une seule URL, règles éclipsées comprises |

---

## Le fichier de règles

Grammaire reconstituée depuis des fichiers EzCat existants. Les sections autres que
`[crawl:categories]` et `[crawl:univers]` sont ignorées sans erreur : un fichier de
config crawler complet passe tel quel.

```
[crawl:categories]

@names
100	site
200	inutile

@
path /offers/*
+	/fr/offers/*
dom exemple.fr
name fiches-offers
parent-id	100
```

| Élément | Règle |
| --- | --- |
| Séparateur | espace, tabulation **ou** `=` (`dom = *`) |
| `@names` | table `id → nom de parent` |
| `@` | ouvre un bloc de règle |
| `path` | motif principal ; `+` ajoute des motifs alternatifs (OU) |
| `dom` | hôte exigé, `*` = n'importe lequel |
| `parent-id` | **optionnel** — une règle peut n'avoir aucun parent |
| `#` et `;` | commentaires ; le BOM UTF-8 est toléré |

### Les deux sémantiques de motif

- **glob** (`/offers`, `/fr/offers/*`, `*/agencies?*`) : `*` est le **seul** joker, et
  le motif est **ancré aux deux bouts**. `/offers` ne matche que `/offers` — c'est
  pourquoi `/offers` et `/offers/*` sont deux règles distinctes. `?` est littéral.
- **`rx:`** (`rx:^/fr/[\w-]+`) : vraie regex Python, en **recherche non ancrée** — les
  motifs portent leur `^` explicite.

Dans les deux cas le motif s'applique au **chemin + query string**, pas au seul chemin :
sans ça `*set_locale=*` n'attraperait rien.

---

## Le service web

```bash
pip install -r requirements.txt
ROOT_PATH=/outils/urlcat SESSION_SECRET=... uvicorn app.main:app --port 8000
```

Puis `http://127.0.0.1:8000/outils/urlcat/`.

> **Sous Git Bash / MSYS sous Windows**, `ROOT_PATH=/outils/urlcat` est réécrit en
> `C:/Program Files/Git/outils/urlcat` par la conversion de chemins POSIX, et le préfixe
> ne correspond plus à rien. Préfixer par `MSYS_NO_PATHCONV=1`, ou poser la variable
> depuis PowerShell.

### Variables d'environnement

Voir `.env.example`. Celles qui comptent :

| Variable | Rôle |
| --- | --- |
| `ROOT_PATH` | préfixe public (`/outils/urlcat`). Doit correspondre au chemin du FQDN Coolify. |
| `WP_TOKEN_SECRET` | **même valeur** que `CREAPULSE_TOOLS_TOKEN_SECRET` côté WordPress, 64 caractères. Sans elle, tout le monde est anonyme. |
| `AUTH_MODE` | `optional` (défaut) ou `required`. Voir ci-dessous. |
| `WP_LOGIN_URL`, `WP_REGISTER_URL` | secours, en `required`, quand l'app est atteinte hors iframe : l'iframe porte ces adresses dans sa query string, un accès direct non. |

### Le mode d'accès

`AUTH_MODE=optional` : l'outil s'utilise sans compte, avec le plafond anonyme ; un membre
reconnu par son jeton obtient le plafond membre.

`AUTH_MODE=required` : l'outil **reste gratuit**, mais il faut un compte. Sans session,
l'app rend une invitation qui dit ce que l'outil fait, avec les boutons de connexion et
d'inscription — et `POST /classer` est refusé en 403.

Ce refus porte sur l'**accès direct** à l'app, pas seulement sur l'affichage du formulaire
dans la page : sans lui, quiconque connaît l'adresse de l'app s'en sert sans compte
(leçon `modes-acces-outil-2-barrieres`). Il **rend la page avec les champs intacts** : une
session qui expire au milieu d'une mise au point ne doit jamais emporter le fichier de
règles en cours.

Les adresses `login_url` et `register_url` arrivent par la query string de l'iframe. Elles
ne sont reprises dans un lien **que si elles mènent au même hôte** que la page : les
refléter telles quelles laisserait fabriquer une adresse de l'outil dont le bouton « Se
connecter » mène ailleurs. Une valeur étrangère est ignorée, et le secours s'applique.

Toute autre valeur que `optional` ou `required` retombe sur `optional` : une faute de frappe
ne doit ni ouvrir ni fermer l'accès par accident. `/health` annonce `mode_acces`, pour
vérifier après un déploiement sans avoir à se connecter.

### Le préfixe

L'app est servie sous un **sous-chemin** (`/outils/urlcat`), jamais un sous-domaine : c'est
ce qui met l'iframe en **même origine** que la landing, donc cookies et téléchargement
fonctionnent sans réglage cross-origin. Un middleware retire le préfixe s'il est présent, si
bien que l'app répond que Traefik le retire ou non — et `/health` reste joignable sans
préfixe, ce dont le `HEALTHCHECK` Docker a besoin.

### Les plafonds, et d'où ils viennent

Le service garde toutes les URLs **uniques** en mémoire (c'est ce qui permet la colonne
`nombre`). Mesuré sur cette machine, en pic de tas Python :

| URLs uniques | RAM (pic) | Temps |
| --- | --- | --- |
| 10 000 | 13 Mo | 0,2 s |
| 50 000 | 55 Mo | 0,7 s |
| 100 000 | 109 Mo | 1,35 s |
| 500 000 | 536 Mo | 6,8 s |

Soit **~1,1 ko par URL unique**, linéaire. D'où `MAX_URLS_ANON=50000` (≈ 55 Mo) et
`MAX_URLS_MEMBRE=150000` (≈ 165 Mo). Le chiffre à tenir n'est pas le plafond seul mais
**`MAX_CONCURRENT` × plafond × 1,1 ko** : deux classements membres simultanés demandent
déjà ~330 Mo. Prévoir **512 Mo** de conteneur, et ne monter les plafonds qu'en baissant
`MAX_CONCURRENT`, ou l'inverse.

Au-delà du plafond, le service refuse en 413 et renvoie vers la CLI, qui n'a pas de limite.

---

## Déploiement sur Coolify

1. Application → dépôt public `sergeesteves/creapulse-urlcat`, branche `main`, Dockerfile.
2. **Deux domaines**, séparés par une virgule :
   `https://www.creapulse.fr/outils/urlcat,https://staging.creapulse.fr/outils/urlcat`
   Sans le second, l'outil embarqué dans une page du **staging** reçoit la page 404 de
   WordPress : le plugin ne transmet le jeton qu'à un `src` commençant par
   `https://<hôte du site>/outils/`.
3. Variables d'environnement, dont `WP_TOKEN_SECRET` = celle de WordPress.
4. Healthcheck `GET /health` (déjà dans le Dockerfile).
5. Dépôt public → **pas de webhook** → redéploiement manuel après chaque push.
6. Côté WordPress : déclarer le slug `urlcat` dans Creapulse Tools → Webhooks formulaires
   → Outils déclarés, **sur l'environnement où le slug est déclaré**, et faire pointer
   `WP_TOOL_EVENT_URL` vers ce même environnement. Les deux doivent coïncider : un outil déclaré
   sur le staging alors que l'app poste vers la prod fait échouer l'attribution (la route répond,
   mais le slug lui est inconnu). À la migration, les deux se basculent ensemble vers `www`.
   Puis poser le shortcode dans la page :

```
[creapulse_tool src="https://www.creapulse.fr/outils/urlcat/?embed=1" access="optional" register_level="1" height="620" autoheight="1"]
```

Après déploiement, tester `/outils/urlcat/health` **depuis le poste de Serge et depuis
l'extérieur** : les deux doivent rendre le JSON de l'outil, pas une page WordPress. Et
tester l'affichage **connecté** dans la page — c'est le seul test qui prouve le jeton.

---

## Comptages, occurrences et `nombre`

L'outil compte d'abord les occurrences de chaque URL, puis classe chaque URL **unique** une
seule fois. Sur un log serveur où Googlebot repasse, c'est la différence entre classer 2 M de
lignes et 50 000 URLs distinctes.

La colonne `nombre` du CSV porte le nombre d'occurrences. Le rapport donne les deux poids, et
**ils ne disent pas la même chose** :

```
GROUPES
   uniques   part     occurr.   part   nom
         1   0.0%        1051   1.0%   en-surmenu-footer
```

Une seule URL, 1 051 passages de crawl. Invisible dans un décompte d'URLs.

---

## Ce que le diagnostic trouve

| Signal | Sens |
| --- | --- |
| **Règles éclipsées** | elles matchent des URLs, mais une règle antérieure gagne. Le rapport nomme la voleuse, le nombre d'URLs captées et un exemple |
| **Règles sans objet** | aucune URL du corpus ne les matche — règle périmée ou motif faux |
| **Hôte non déclaré** | aucune règle ne couvre l'hôte des URLs chargées → propose `--ignore-dom` |
| **`.` non échappé** | dans un `rx:`, `^/www.*` matche n'importe quel caractère, pas un point |
| **Noms en double**, **règles inatteignables** après un `path *` | détectés au chargement |

---

## Vérifications

```bash
python tests/verifie.py              # 54 verifications du moteur, stdlib seule
python -m pytest tests/test_app.py   # 27 verifications du service web
```

Les 54 du moteur : grammaire, sémantique des motifs, premier-match-gagnant, agrégation des
occurrences, panel et reproductibilité, intégrité de la page HTML (dont une URL contenant
`<script>`), contrastes WCAG recalculés, et bout-en-bout CLI.

Les 27 du service : plafonds, CSV (en-tête et BOM), routes avec et sans préfixe, en-têtes de
sécurité, et le **vecteur de référence du jeton** — un secret de test, un payload et le jeton
attendu octet pour octet. Toute divergence d'ordre de clés, d'échappement ou de padding entre
l'émetteur PHP et ce vérificateur fait échouer ce test, et lui seul.

---

## Charte

La page de contrôle de la CLI et l'interface web appliquent la charte Creapulse. Les valeurs
vivent dans la constante `BRAND` d'`urlcat.py` et dans `app/static/urlcat.css` ;
`tests/verifie.py` **recalcule les contrastes** à chaque exécution.

Garde-fou mesuré : `#027dba` en **texte** sur le papier `#f7f7f7` donne 4,22:1 et échoue le
4,5:1. Il ne sert donc qu'en fond (blanc dessus : 4,52:1) et en filets. Le blanc sur l'accent
`#e2177f` passe à 4,50:1 — la limite exacte, aucune marge.

---

## Figures

Les sources SVG des schémas publiés sont dans [`docs/figures/`](docs/figures/), avec le détail de
leur dimensionnement : **le même schéma existe en deux versions**, une par largeur de rendu
(colonne d'article à 720 px, section de page outil à 830 px). Un SVG est dimensionné pour une
largeur, pas dans l'absolu.

## Limites connues

- Pas de normalisation d'URL : `/offers` et `/offers/` sont deux URLs distinctes, et une
  majuscule compte. Volontaire — normaliser en silence changerait les comptages d'un audit.
- La colonne `nombre` impose de tenir toutes les URLs uniques en mémoire (voir les plafonds).
- La page HTML de la CLI affiche au plus 500 lignes par filtre ; le CSV est complet. Le
  service web applique la même limite d'aperçu.
- Le service ne lit pas un access log brut : il attend des URLs, pas des lignes de log.
- Zéro appel LLM, par construction. L'outil est déterministe de bout en bout, et doit le
  rester : c'est ce qui le rend reproductible sur un audit.
