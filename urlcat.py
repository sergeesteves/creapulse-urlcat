#!/usr/bin/env python3
"""Categorise des URLs par groupe / parent / univers a partir d'un fichier de regles.

Lit la grammaire des fichiers de categorisation EzCat (sections [crawl:categories]
et [crawl:univers]) et classe une liste d'URLs. Premier motif qui matche gagne.

Stdlib uniquement.

Deroulement : on compte d'abord les occurrences de chaque URL (une passe en flux),
puis on classe chaque URL UNIQUE une seule fois. Sur un log serveur ou Googlebot
repasse sur les memes URLs, c'est la difference entre classer 2 M de lignes et
50 000 URLs distinctes.

Les comptages portent toujours sur la totalite ; --panel ne reduit que ce qui est
exporte, pour relire a l'oeil sans ouvrir un CSV de 100 000 lignes.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

CATEGORIES = "crawl:categories"
UNIVERS = "crawl:univers"
FALLBACK = "autres"

# Jetons de la charte Creapulse (brand/tokens.json). #027dba echoue le 4,5:1
# en texte sur le papier (4,23:1, mesure) : il ne sert qu'en fond et en filets.
BRAND = {
    "paper": "#f7f7f7",
    "surface": "#ffffff",
    "ink": "#111111",
    "ink_muted": "#595959",
    "rule": "rgba(0, 0, 0, 0.12)",
    "surface_subtle": "rgba(0, 0, 0, 0.03)",
    "secondary": "#027dba",
    "accent": "#e2177f",
    "radius_box": "8px",
    "radius_button": "6px",
}


# --------------------------------------------------------------------------- #
# Modele
# --------------------------------------------------------------------------- #


@dataclass
class Pattern:
    kind: str  # "glob" ou "rx"
    raw: str
    regex: re.Pattern

    def matches(self, path_query: str) -> bool:
        # glob : ancre aux deux bouts (\A..\Z pose a la compilation).
        # rx   : recherche non ancree, les motifs portent leur propre ^.
        return bool(self.regex.search(path_query))


@dataclass
class Rule:
    name: str
    line: int
    section: str
    parent_id: str | None = None
    parent: str | None = None
    doms: set[str] = field(default_factory=set)
    any_dom: bool = False
    patterns: list[Pattern] = field(default_factory=list)
    urls: int = 0  # URLs uniques retenues
    hits: int = 0  # occurrences retenues
    shadowed_by: Counter = field(default_factory=Counter)
    shadow_example: dict[str, str] = field(default_factory=dict)

    def host_ok(self, host: str, ignore_dom: bool) -> bool:
        if ignore_dom or self.any_dom or not self.doms:
            return True
        return host in self.doms

    def match(self, host: str, path_query: str, ignore_dom: bool) -> Pattern | None:
        if not self.host_ok(host, ignore_dom):
            return None
        for pattern in self.patterns:
            if pattern.matches(path_query):
                return pattern
        return None

    @property
    def is_catch_all(self) -> bool:
        return any(p.kind == "glob" and p.raw == "*" for p in self.patterns)


@dataclass
class RuleSet:
    categories: list[Rule] = field(default_factory=list)
    univers: list[Rule] = field(default_factory=list)
    parents: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_rules(self) -> list[Rule]:
        return self.categories + self.univers


# --------------------------------------------------------------------------- #
# Compilation des motifs
# --------------------------------------------------------------------------- #


def compile_pattern(raw: str, line: int, warnings: list[str]) -> Pattern | None:
    if raw.startswith("rx:"):
        body = raw[3:].strip()
        try:
            return Pattern("rx", raw, re.compile(body))
        except re.error as exc:
            warnings.append(f"ligne {line} : regex invalide ({exc}) - motif ignore : {raw}")
            return None

    # glob : seul `*` est un joker, tout le reste est litteral (`?` compris).
    escaped = ".*".join(re.escape(part) for part in raw.split("*"))
    return Pattern("glob", raw, re.compile(r"\A" + escaped + r"\Z"))


def lint_pattern(pattern: Pattern, rule_name: str, line: int, warnings: list[str]) -> None:
    if pattern.kind != "rx":
        return
    body = pattern.raw[3:]
    # `.` non echappe vaut "n'importe quel caractere" : souvent une coquille
    # quand le motif vise un point litteral (ex. ^/www.*).
    if re.search(r"(?<!\\)\.", body) and ("www." in body or re.search(r"\w\.\w", body)):
        warnings.append(
            f"ligne {line} ({rule_name}) : `.` non echappe dans {pattern.raw} "
            "- il matche n'importe quel caractere, pas un point litteral"
        )


# --------------------------------------------------------------------------- #
# Parsing du fichier de regles
# --------------------------------------------------------------------------- #

DIRECTIVE = re.compile(r"^([A-Za-z][\w-]*)\s*(?:=|\s)\s*(.*)$")
CONTINUATION = re.compile(r"^\+\s*(.+)$")
SECTION = re.compile(r"^\[(.+)\]$")
NAME_ROW = re.compile(r"^(\d+)\s+(.+)$")


def parse_rules(path: Path) -> RuleSet:
    """Lit un fichier de regles. Delegue a parse_rules_text : un seul moteur."""
    return parse_rules_text(path.read_text(encoding="utf-8-sig", errors="replace"))


def parse_rules_text(text: str) -> RuleSet:
    """Meme grammaire, depuis une chaine deja decodee (service web, test, n8n)."""
    rules = RuleSet()

    section: str | None = None
    in_names = False
    block: dict | None = None

    def flush() -> None:
        nonlocal block
        if block is None:
            return
        name, raws = block.get("name"), block.get("paths", [])
        if name and raws and block.get("section") in (CATEGORIES, UNIVERS):
            rule = Rule(
                name=name,
                line=block["line"],
                section=block["section"],
                parent_id=block.get("parent-id"),
                doms={d.lower() for d in block.get("doms", []) if d != "*"},
                any_dom=any(d == "*" for d in block.get("doms", [])),
            )
            for raw, line in raws:
                pattern = compile_pattern(raw, line, rules.warnings)
                if pattern is not None:
                    lint_pattern(pattern, name, line, rules.warnings)
                    rule.patterns.append(pattern)
            if rule.patterns:
                target = rules.categories if rule.section == CATEGORIES else rules.univers
                target.append(rule)
        block = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        matched = SECTION.match(line)
        if matched:
            flush()
            section = matched.group(1).strip()
            in_names = False
            continue

        if line == "@":
            flush()
            block = {"line": lineno, "section": section, "paths": [], "doms": []}
            in_names = False
            continue

        if line.startswith("@"):
            # @names ouvre la table des parents ; @crawl, @robots_txt, etc.
            # appartiennent aux sections crawler : on ferme et on ignore.
            flush()
            in_names = line[1:].strip().lower() == "names"
            continue

        if in_names:
            row = NAME_ROW.match(line)
            if row:
                rules.parents[row.group(1)] = row.group(2).strip()
            continue

        cont = CONTINUATION.match(line)
        if cont and block is not None:
            block["paths"].append((cont.group(1).strip(), lineno))
            continue

        directive = DIRECTIVE.match(line)
        if not directive or block is None:
            continue

        key, value = directive.group(1).lower(), directive.group(2).strip()
        if key == "path":
            block["paths"].append((value, lineno))
        elif key == "dom":
            block["doms"].append(value)
        elif key == "name":
            block["name"] = value
        elif key == "parent-id":
            block["parent-id"] = value

    flush()

    for rule in rules.all_rules:
        if rule.parent_id is not None:
            rule.parent = rules.parents.get(rule.parent_id)
            if rule.parent is None:
                rules.warnings.append(
                    f"ligne {rule.line} ({rule.name}) : parent-id {rule.parent_id} "
                    "absent de @names"
                )

    _lint_ruleset(rules)
    return rules


def _lint_ruleset(rules: RuleSet) -> None:
    for label, group in (("categories", rules.categories), ("univers", rules.univers)):
        seen: dict[str, int] = {}
        for rule in group:
            if rule.name in seen:
                rules.warnings.append(
                    f"[{label}] nom de groupe en double : {rule.name} "
                    f"(lignes {seen[rule.name]} et {rule.line})"
                )
            else:
                seen[rule.name] = rule.line

        # Premier motif gagnant : tout ce qui suit un attrape-tout est mort.
        for index, rule in enumerate(group):
            if rule.is_catch_all and index < len(group) - 1:
                after = [r.name for r in group[index + 1 :]]
                rules.warnings.append(
                    f"[{label}] `path *` ligne {rule.line} ({rule.name}) rend "
                    f"inatteignable(s) : {', '.join(after)}"
                )
                break


# --------------------------------------------------------------------------- #
# Classement
# --------------------------------------------------------------------------- #


def split_url(url: str) -> tuple[str, str]:
    """Renvoie (hote minuscule, chemin+query). Le motif porte sur chemin+query."""
    if "://" not in url:
        url = "http://" + url.lstrip("/")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path_query = parts.path or "/"
    if parts.query:
        path_query += "?" + parts.query
    return host, path_query


def classify(rules: list[Rule], host: str, path_query: str, ignore_dom: bool):
    for rule in rules:
        pattern = rule.match(host, path_query, ignore_dom)
        if pattern is not None:
            return rule, pattern
    return None, None


def all_matches(rules: list[Rule], host: str, path_query: str, ignore_dom: bool) -> list[Rule]:
    return [r for r in rules if r.match(host, path_query, ignore_dom) is not None]


# --------------------------------------------------------------------------- #
# Lecture des URLs
# --------------------------------------------------------------------------- #


def iter_urls(path: Path, column: str | None):
    """Lit un fichier d'URLs. Delegue a iter_urls_text : un seul moteur.

    Traduit l'erreur de colonne en SystemExit, qui est le contrat de la CLI.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        yield from iter_urls_text(text, column)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def iter_urls_text(text: str, column: str | None):
    """Meme lecture, depuis une chaine deja decodee. Leve ValueError si la colonne manque."""
    if column is None:
        for line in text.splitlines():
            url = line.strip()
            if url and not url.startswith("#"):
                yield url
        return

    head = text.splitlines()[0] if text else ""
    delimiter = ";" if head.count(";") > head.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if reader.fieldnames is None or column not in reader.fieldnames:
        raise ValueError(
            f"colonne '{column}' absente. Colonnes disponibles : {reader.fieldnames}"
        )
    for row in reader:
        url = (row.get(column) or "").strip()
        if url:
            yield url


def _sniff(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        head = handle.readline()
    return ";" if head.count(";") > head.count(",") else ","


# --------------------------------------------------------------------------- #
# Analyse
# --------------------------------------------------------------------------- #

FIELDS = [
    "url",
    "groupe",
    "univers",
    "nombre",
    "parent",
    "hote",
    "chemin_query",
    "motif_groupe",
    "ligne_groupe",
    "motif_univers",
    "ligne_univers",
]


@dataclass
class Stat:
    urls: int = 0
    hits: int = 0


def analyse(rules: RuleSet, args: argparse.Namespace, urls=None):
    """Compte les occurrences, classe chaque URL unique, renvoie lignes + stats.

    `urls` : iterable d'URLs deja pret (service web). Absent, on lit args.urls.
    """
    occurrences: Counter = Counter()
    source = urls if urls is not None else iter_urls(Path(args.urls), args.url_column)
    for url in source:
        occurrences[url] += 1

    rows: list[dict] = []
    stats = {
        "groupe": defaultdict(Stat),
        "parent": defaultdict(Stat),
        "univers": defaultdict(Stat),
    }
    hosts: Counter = Counter()
    unmatched = Stat()

    for url, count in occurrences.items():
        host, path_query = split_url(url)
        hosts[host] += count

        group_rule, group_pattern = classify(rules.categories, host, path_query, args.ignore_dom)
        univ_rule, univ_pattern = classify(rules.univers, host, path_query, args.ignore_dom)

        if group_rule is not None:
            group_rule.urls += 1
            group_rule.hits += count
            if args.diagnose:
                # Qui eclipse qui : toute regle qui matchait sans gagner.
                for other in all_matches(rules.categories, host, path_query, args.ignore_dom):
                    if other is group_rule or other.is_catch_all:
                        continue
                    other.shadowed_by[group_rule.name] += 1
                    other.shadow_example.setdefault(group_rule.name, url)
        else:
            unmatched.urls += 1
            unmatched.hits += count

        if univ_rule is not None:
            univ_rule.urls += 1
            univ_rule.hits += count

        group_name = group_rule.name if group_rule else FALLBACK
        univ_name = univ_rule.name if univ_rule else FALLBACK
        parent_name = (group_rule.parent if group_rule else None) or "(sans parent)"

        for key, name in (("groupe", group_name), ("parent", parent_name), ("univers", univ_name)):
            stats[key][name].urls += 1
            stats[key][name].hits += count

        rows.append(
            {
                "url": url,
                "groupe": group_name,
                "univers": univ_name,
                "nombre": count,
                "parent": group_rule.parent if (group_rule and group_rule.parent) else "",
                "hote": host,
                "chemin_query": path_query,
                "motif_groupe": group_pattern.raw if group_pattern else "",
                "ligne_groupe": group_rule.line if group_rule else "",
                "motif_univers": univ_pattern.raw if univ_pattern else "",
                "ligne_univers": univ_rule.line if univ_rule else "",
            }
        )

    return rows, stats, hosts, unmatched, sum(occurrences.values())


def pick_panel(rows: list[dict], args: argparse.Namespace) -> tuple[list[dict], str]:
    """Reduit ce qui est exporte. Par defaut : tout."""
    rng = random.Random(args.seed)

    if args.panel_per_group:
        buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in rows:
            buckets[(row["groupe"], row["univers"])].append(row)
        panel: list[dict] = []
        for bucket in buckets.values():
            if len(bucket) <= args.panel_per_group:
                panel.extend(bucket)
            else:
                panel.extend(rng.sample(bucket, args.panel_per_group))
        return panel, f"stratifie, jusqu'a {args.panel_per_group} URLs par groupe x univers"

    if args.panel >= 100:
        return rows, "complet"

    keep = max(1, round(len(rows) * args.panel / 100.0))
    return (
        rng.sample(rows, min(keep, len(rows))),
        f"{args.panel:g}% des URLs uniques, tirage uniforme",
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: (r["parent"] or "zz", r["groupe"], r["url"]))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows(ordered)


# --------------------------------------------------------------------------- #
# Rapport texte
# --------------------------------------------------------------------------- #


def build_report(rules, stats, total_hits, total_urls, panel_size, panel_label,
                 hosts, unmatched, args) -> str:
    out: list[str] = []
    add = out.append

    add(f"URLs uniques         : {total_urls}")
    add(f"Occurrences lues     : {total_hits}")
    if total_urls and total_hits != total_urls:
        add(f"                       soit {total_hits / total_urls:.1f} occurrences par URL")
    add(f"Panel exporte        : {panel_size}  ({panel_label})")
    add(f"Fichier de sortie    : {args.out}")
    if args.html:
        add(f"Previsualisation     : {args.html}")
    add("")

    unknown = [h for h in hosts if not any(r.host_ok(h, False) for r in rules.categories)]
    if unknown and not args.ignore_dom:
        add("ATTENTION - aucune regle ne declare ces hotes, leurs URLs tombent en "
            f"'{FALLBACK}' : {', '.join(sorted(unknown)[:5])}")
        add("            relance avec --ignore-dom pour classer sans contrainte d'hote.")
        add("")

    if unmatched.urls:
        share = 100.0 * unmatched.urls / total_urls if total_urls else 0.0
        add(f"NON CLASSEES : {unmatched.urls} URL(s) ({share:.1f}%) ne matchent aucune regle.")
        add(f"               Gardees sous '{FALLBACK}', colonne motif_groupe vide")
        add("               (a distinguer d'une regle `path *` du meme nom).")
        add("")

    for label, key in (("PARENTS", "parent"), ("GROUPES", "groupe"), ("UNIVERS", "univers")):
        add(label)
        add(f"  {'uniques':>8} {'part':>6}   {'occurr.':>9} {'part':>6}   nom")
        for name, stat in sorted(stats[key].items(), key=lambda kv: -kv[1].urls):
            pu = 100.0 * stat.urls / total_urls if total_urls else 0.0
            ph = 100.0 * stat.hits / total_hits if total_hits else 0.0
            add(f"  {stat.urls:>8} {pu:>5.1f}%   {stat.hits:>9} {ph:>5.1f}%   {name}")
        add("")

    silent = [r for r in rules.all_rules if r.urls == 0]
    eclipsed = [r for r in silent if r.shadowed_by]
    barren = [r for r in silent if not r.shadowed_by]

    if eclipsed:
        add(f"REGLES ECLIPSEES ({len(eclipsed)}) - elles matchent des URLs mais une "
            "regle anterieure gagne")
        for rule in eclipsed:
            stolen = sum(rule.shadowed_by.values())
            add(f"  ligne {rule.line:>4}  {rule.name}  ({stolen} URL(s) captees ailleurs)")
            for thief, count in rule.shadowed_by.most_common(3):
                add(f"           {count:>6} -> {thief}")
                add(f"                  ex. {rule.shadow_example[thief]}")
        add("  Pour les recuperer : remonter ces regles avant celles qui les captent.")
        add("")

    if barren:
        add(f"REGLES SANS OBJET ({len(barren)}) - aucune URL du corpus ne les matche")
        for rule in barren:
            note = "  (normal : attrape-tout)" if rule.is_catch_all else ""
            add(f"  ligne {rule.line:>4}  [{rule.section.split(':')[-1]}]  {rule.name}{note}")
        if not args.diagnose:
            add("  Ajoute --diagnose pour savoir si certaines sont en fait eclipsees.")
        add("")

    if rules.warnings:
        add(f"AVERTISSEMENTS SUR LE FICHIER DE REGLES ({len(rules.warnings)})")
        for warning in rules.warnings:
            add(f"  {warning}")
        add("")

    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Previsualisation HTML
# --------------------------------------------------------------------------- #

HTML_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --paper: __PAPER__;
  --surface: __SURFACE__;
  --ink: __INK__;
  --ink-muted: __INK_MUTED__;
  --rule: __RULE__;
  --surface-subtle: __SURFACE_SUBTLE__;
  --secondary: __SECONDARY__;
  --accent: __ACCENT__;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 16px 64px;
  background: var(--paper); color: var(--ink);
  font: 16px/1.5 Archivo, "Open Sans", -apple-system, "Segoe UI", system-ui, sans-serif;
}
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--ink-muted); font-size: 14px; margin: 0 0 24px; word-break: break-word; }
.card {
  background: var(--surface); border: 1px solid var(--rule);
  border-radius: __RADIUS_BOX__; padding: 20px; margin-bottom: 20px;
}
.card > h2 {
  font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--ink-muted); margin: 0 0 14px; font-weight: 600;
}
.bar { height: 4px; background: var(--secondary); border-radius: 2px; margin: 0 0 18px; }
.controls { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; }
label { display: block; font-size: 13px; color: var(--ink-muted); margin-bottom: 4px; }
select, input[type=search] {
  font: inherit; font-size: 15px; padding: 8px 10px;
  border: 1px solid var(--rule); border-radius: __RADIUS_BUTTON__;
  background: var(--surface); color: var(--ink); min-width: 230px; max-width: 100%;
}
select:focus, input:focus { outline: 2px solid var(--secondary); outline-offset: 1px; }
.count { color: var(--ink-muted); font-size: 14px; padding-bottom: 9px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--rule); }
th { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ink-muted); }
tbody tr:nth-child(even) { background: var(--surface-subtle); }
td.num { text-align: right; font-variant-numeric: tabular-nums; width: 74px; }
td.url { word-break: break-all; }
td.pat { color: var(--ink-muted); font-family: "Liberation Mono", ui-monospace, monospace;
         font-size: 12px; }
.badge {
  display: inline-block; background: var(--secondary); color: #fff;
  font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: __RADIUS_BUTTON__;
}
.badge.unmatched { background: var(--accent); }
.note { border-left: 4px solid var(--accent); padding: 10px 14px; margin: 0 0 18px;
        background: var(--surface-subtle); font-size: 14px; }
.empty { color: var(--ink-muted); padding: 24px 10px; }
footer { color: var(--ink-muted); font-size: 13px; text-align: center; margin-top: 32px; }
@media (max-width: 640px) {
  select, input[type=search] { min-width: 0; width: 100%; }
  .controls > div { flex: 1 1 100%; }
}
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <p class="sub">__SUBTITLE__</p>

  __NOTE__

  <div class="card">
    <div class="bar"></div>
    <h2>Verifier ce qu'attrape une regle</h2>
    <div class="controls">
      <div>
        <label for="g">Groupe</label>
        <select id="g"></select>
      </div>
      <div>
        <label for="u">Univers</label>
        <select id="u"></select>
      </div>
      <div>
        <label for="q">Filtrer l'URL</label>
        <input id="q" type="search" placeholder="fragment de chemin">
      </div>
      <div class="count" id="count"></div>
    </div>
  </div>

  <div class="card">
    <table>
      <thead><tr>
        <th>URL</th><th>Groupe</th><th>Univers</th><th class="num">Nombre</th><th>Motif</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="empty" id="empty" hidden>Aucune URL pour cette combinaison.</div>
  </div>

  <footer>__FOOTER__</footer>
</div>
<script>
const DATA = __DATA__;
const TOTALS = __TOTALS__;
const LIMIT = 500;

const sel = (id) => document.getElementById(id);

function fill(node, values, totals) {
  node.innerHTML = '';
  const all = document.createElement('option');
  all.value = '*';
  all.textContent = 'Tous (' + values.length + ')';
  node.appendChild(all);
  for (const v of values) {
    const o = document.createElement('option');
    o.value = v;
    const t = totals[v];
    o.textContent = t ? v + '  (' + t + ')' : v;
    node.appendChild(o);
  }
}

const groups = [...new Set(DATA.map(r => r[1]))].sort();
const univers = [...new Set(DATA.map(r => r[2]))].sort();
fill(sel('g'), groups, TOTALS.groupe);
fill(sel('u'), univers, TOTALS.univers);

function render() {
  const g = sel('g').value, u = sel('u').value;
  const q = sel('q').value.trim().toLowerCase();
  const hits = DATA.filter(r =>
    (g === '*' || r[1] === g) &&
    (u === '*' || r[2] === u) &&
    (!q || r[0].toLowerCase().includes(q)));

  sel('rows').innerHTML = hits.slice(0, LIMIT).map(r => {
    const cls = r[4] ? 'badge' : 'badge unmatched';
    return '<tr>' +
      '<td class="url">' + r[0] + '</td>' +
      '<td><span class="' + cls + '">' + r[1] + '</span></td>' +
      '<td>' + r[2] + '</td>' +
      '<td class="num">' + r[3] + '</td>' +
      '<td class="pat">' + (r[4] || '(aucun)') + '</td>' +
      '</tr>';
  }).join('');

  sel('empty').hidden = hits.length > 0;
  sel('count').textContent = hits.length + ' URL(s) dans le panel' +
    (hits.length > LIMIT ? ' - ' + LIMIT + ' affichees' : '');
}

for (const id of ['g', 'u', 'q']) {
  sel(id).addEventListener(id === 'q' ? 'input' : 'change', render);
}
render();
</script>
</body>
</html>
"""


def _js_safe(payload: str) -> str:
    return payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def write_html(path: Path, rows: list[dict], stats, total_urls: int, total_hits: int,
               panel_label: str, rules_path: str, urls_path: str) -> None:
    data = [
        [
            html.escape(row["url"]),
            html.escape(row["groupe"]),
            html.escape(row["univers"]),
            row["nombre"],
            html.escape(row["motif_groupe"]),
        ]
        for row in sorted(rows, key=lambda r: (r["groupe"], r["univers"], -r["nombre"], r["url"]))
    ]
    totals = {
        "groupe": {name: stat.urls for name, stat in stats["groupe"].items()},
        "univers": {name: stat.urls for name, stat in stats["univers"].items()},
    }

    note = ""
    if len(rows) < total_urls:
        note = (
            '<div class="note">Cette page ne montre qu\'un panel de '
            f"<strong>{len(rows)}</strong> URLs sur {total_urls} uniques ({html.escape(panel_label)}). "
            "Les effectifs entre parentheses dans les listes deroulantes, eux, portent sur "
            "la totalite.</div>"
        )

    occ = f" - {total_hits} occurrences" if total_hits != total_urls else ""
    subtitle = (
        f"{total_urls} URLs uniques{occ} - regles : {Path(rules_path).name} "
        f"- source : {Path(urls_path).name}"
    )

    replacements = {
        "__TITLE__": "Categorisation des URLs",
        "__SUBTITLE__": html.escape(subtitle),
        "__NOTE__": note,
        "__FOOTER__": "urlcat - Creapulse",
        # html.escape en amont neutralise deja `</script>` dans une URL de log ;
        # U+2028/2029 restent des fins de ligne pour JS, on les echappe ici.
        "__DATA__": _js_safe(json.dumps(data, ensure_ascii=False)),
        "__TOTALS__": _js_safe(json.dumps(totals, ensure_ascii=False)),
        "__PAPER__": BRAND["paper"],
        "__SURFACE__": BRAND["surface"],
        "__INK__": BRAND["ink"],
        "__INK_MUTED__": BRAND["ink_muted"],
        "__RULE__": BRAND["rule"],
        "__SURFACE_SUBTLE__": BRAND["surface_subtle"],
        "__SECONDARY__": BRAND["secondary"],
        "__ACCENT__": BRAND["accent"],
        "__RADIUS_BOX__": BRAND["radius_box"],
        "__RADIUS_BUTTON__": BRAND["radius_button"],
    }
    page = HTML_TEMPLATE
    for token, value in replacements.items():
        page = page.replace(token, value)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Entree
# --------------------------------------------------------------------------- #


def explain(rules: RuleSet, args: argparse.Namespace) -> int:
    host, path_query = split_url(args.explain)
    print(f"URL          : {args.explain}")
    print(f"Hote         : {host}")
    print(f"Chemin+query : {path_query}")
    for label, group in (("GROUPES", rules.categories), ("UNIVERS", rules.univers)):
        print(f"\n{label}")
        winner, found = True, False
        for rule in group:
            pattern = rule.match(host, path_query, args.ignore_dom)
            if pattern is None:
                continue
            found = True
            mark = "RETENU " if winner else "ombre  "
            parent = f"  parent={rule.parent}" if rule.parent else ""
            print(f"  {mark} ligne {rule.line:>4}  {rule.name}{parent}")
            print(f"           motif {pattern.raw}  ({pattern.kind})")
            winner = False
        if not found:
            print(f"  aucune regle ne matche -> {FALLBACK}")
    return 0


def run(args: argparse.Namespace) -> int:
    rules = parse_rules(Path(args.rules))
    if not rules.categories:
        print("Aucune regle de categorie lue : verifie la section [crawl:categories].",
              file=sys.stderr)
        return 2

    if args.explain:
        return explain(rules, args)

    rows, stats, hosts, unmatched, total_hits = analyse(rules, args)
    total_urls = len(rows)
    panel, panel_label = pick_panel(rows, args)

    write_csv(Path(args.out), panel)
    if args.html:
        write_html(Path(args.html), panel, stats, total_urls, total_hits,
                   panel_label, args.rules, args.urls)

    report = build_report(rules, stats, total_hits, total_urls, len(panel), panel_label,
                          hosts, unmatched, args)
    print(report)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classe des URLs par groupe / parent / univers (premier motif gagnant).",
    )
    parser.add_argument("--rules", required=True, help="fichier de categorisation")
    parser.add_argument("--urls", help="liste d'URLs (une par ligne, ou CSV avec --url-column)")
    parser.add_argument("--out", default="classement.csv", help="CSV de sortie")
    parser.add_argument("--html", help="page de previsualisation (listes deroulantes)")
    parser.add_argument("--report", help="ecrit aussi le rapport texte dans ce fichier")
    parser.add_argument("--url-column", help="nom de la colonne si --urls est un CSV")
    parser.add_argument(
        "--panel",
        type=float,
        default=100.0,
        metavar="PCT",
        help="pourcentage d'URLs uniques exportees (defaut 100 = tout)",
    )
    parser.add_argument(
        "--panel-per-group",
        type=int,
        default=0,
        metavar="N",
        help="panel stratifie : jusqu'a N URLs par couple groupe x univers "
             "(les petits groupes restent representes)",
    )
    parser.add_argument("--seed", type=int, default=1,
                        help="graine du tirage (panel reproductible)")
    parser.add_argument("--ignore-dom", action="store_true",
                        help="ignore la directive dom (regles ecrites pour un autre hote)")
    parser.add_argument("--diagnose", action="store_true",
                        help="analyse les eclipses : quelle regle capte les URLs d'une autre")
    parser.add_argument("--explain", metavar="URL",
                        help="detaille le classement d'une seule URL")

    args = parser.parse_args(argv)
    if not args.explain and not args.urls:
        parser.error("--urls est requis (sauf avec --explain)")
    if not 0 < args.panel <= 100:
        parser.error("--panel doit etre dans (0, 100]")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
