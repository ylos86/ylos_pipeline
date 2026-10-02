# Migration du schéma : 2.2.0 → 2.3.0

> **Statut** : implémenté le 2026-10-02 (branche `feat/schema-2.3-step-status`). Générateur
> (`create_project.py`) à jour. **Aucune migration de fichiers requise** : changement
> strictement additif, comme 2.0 → 2.1 et 2.1 → 2.2. État d'avant conservé sous le tag
> `snapshot/2026-10-02-before-schema-2.3`.

## Pourquoi

En 2.2, un statut explicite (`review` / `approved`) n'était lié à **aucune version** : dans
`get_step_status` la valeur explicite gagne toujours, et `finalize_publish_version` ne touche
jamais `step_status`. Approuver le lookdev puis publier une v008 faisait donc apparaître la v008
« approved » sans que personne ne l'ait vue.

Décision Sébastien (2026-10-02, refonte UI « direction C ») : **une approbation porte sa version**.

## Pourquoi une table voisine plutôt qu'un objet dans `step_status`

`step_status` garde **exactement** sa forme 2.2 :

```json
"step_status": { "lookdev": "approved" }
```

La version vit dans une table voisine, `step_status_meta`. Conséquence voulue : **un outil 2.2
relit un manifeste écrit en 2.3 sans perdre une seule approbation**. Revenir au tag
`snapshot/2026-10-02-before-schema-2.3` est donc sans risque pour les données. Un objet dans
`step_status` aurait été plus compact, mais le code 2.2 l'aurait lu comme une valeur inconnue et
serait retombé en silence sur le statut dérivé.

## `step_status_meta` (nouveau, toutes familles)

```json
"step_status":      { "lookdev": "approved" },
"step_status_meta": { "lookdev": { "version": 7, "set_utc": "2026-10-02T17:40:00+00:00" } }
```

- **Optionnel**, mêmes clés que `step_status`. Écrit **uniquement** par
  `create_project.set_step_status`, avec `step_status`, sous le même flock, dans la même écriture
  atomique.
- `version` : la version `complete` du step à laquelle le statut s'applique. Par défaut, la
  dernière version `complete` au moment de l'appel ; `null` si rien n'est encore publié (accepté,
  comme en 2.2). Une version explicite doit être un publish `complete` **de ce step** :
  sinon `ValueError`, et rien n'est écrit. Une réservation `pending` n'est pas un publish.
- `set_utc` : date du statut.
- Effacer le statut (`auto`) efface aussi son entrée ; une table vidée est retirée du manifeste.
  Passer une version en effaçant est refusé (`ValueError`).
- Un publish ne touche **jamais** ces deux tables.

## Lecture : `get_step_status` (clés ajoutées)

```
{step: {"status", "explicit", "derived", "version", "set_utc", "latest_version", "behind"}}
```

- `version` / `set_utc` : ceux de `step_status_meta` pour un statut explicite, sinon `None`.
- `latest_version` : dernière version `complete` du step, lue dans `step_publishes` du manifeste,
  sans scan disque (la fonction tourne à chaque poll du cockpit).
- `behind` : `True` quand un statut explicite porte une version et qu'un publish plus récent
  existe (« approved v007, v008 pas revue »). **Le statut reste `approved`** : c'est aux UI de
  signaler le retard, jamais à l'orchestrateur de rétrograder.

Les clés 2.2 ne changent pas. Seuls les tests qui comparaient le dict entier ont été mis à jour.

## Écriture : `set_step_status(project_root, entity, step, status, version=None)`

Point unique inchangé :
- web : `POST /api/set-step-status {entity, step, status, version?}` (adaptateur mince, la
  version par défaut est choisie par l'orchestrateur) ;
- CLI : `create_project.py set-step-status <projet> <entité> <step> approved [--version N]` ;
- Blender (`ylos.set_step_status`) et Houdini (panel) appellent sans version : ils enregistrent
  donc automatiquement la dernière version publiée. **Aucune UI n'a besoin de changer pour
  profiter de 2.3.**

## Manifestes existants

- **Rien à réécrire.** Un statut posé avant 2.3 n'a pas d'entrée dans `step_status_meta` :
  `version` vaut `None` (version inconnue), `behind` vaut `False`. Il retrouve une version dès
  qu'il est reposé.
- Un manifeste 2.0, 2.1 ou 2.2 reste valide pour un outil 2.3 (seul le MAJEUR est contrôlé par
  `validate_manifest`).
- Une entrée de `step_status_meta` sans statut correspondant (posée par un outil 2.3, puis
  effacée par un outil 2.2) est ignorée à la lecture. Une entrée malformée aussi : jamais
  d'exception.
- `SCHEMA_VERSION` passe à `2.3.0` : les nouveaux `project.json` et manifestes d'entité portent
  `2.3.0`. `project.schema.json` ne change pas d'un octet.

## Non couvert par 2.3

- L'**affichage** du retard (`behind`) dans le cockpit web, le browser Blender et le panel
  Houdini : prochain incrément UI.
- Les publishes legacy (fichiers plats, `publish_asset` déprécié) n'ont pas de version dans le
  manifeste : ils ne fixent pas de `version` et ne déclenchent pas `behind`.

## Tests

- `tests/test_step_status.py` : classe `TestVersionBoundStatus` (13 tests : version par défaut,
  version explicite validée, réservation `pending` exclue, `behind`, effacement des deux tables,
  forme 2.2 conservée, statut 2.2 sans version, meta orpheline ou malformée, schéma documenté).
- `tests/test_ylos_ui.py` : version validée par l'orchestrateur (400), approbation d'une version
  antérieure signalée `behind` de bout en bout, carte de grille inchangée (chaîne simple).
- Suite stdlib : 395 tests OK (1 skip), Python 3.10 ; modules touchés aussi verts en 3.11 et 3.13.
- **Non relancés** : tests headless Blender et e2e Houdini (ni Blender ni licence Houdini dans
  l'environnement). Aucun appelant DCC ne change de signature, et le test headless qui lit
  `step_status` brut voit toujours la chaîne `"review"`.
