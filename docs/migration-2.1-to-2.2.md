# Migration du schéma : 2.1.0 → 2.2.0

> **Statut** : implémenté (plan « pipeline utilisable v1 », Phases 2.1 et 3.1). Générateur
> (`create_project.py`) à jour. **Aucune migration de fichiers requise** — changement
> strictement additif, comme 2.0 → 2.1.

## Pourquoi un bump MINEUR

2.2.0 introduit **deux** champs optionnels sur le manifeste d'entité (`asset.schema.json`) :

1. `step_status` — statut de production **explicite** par step (décision Sébastien
   2026-09-09 : « nouveau champ de schéma, pas une dérivation seule »).
2. `dependencies` — sur chaque entrée de `step_publishes[step]`, la liste des produits
   publiés dont ce publish a été construit (suivi de dépendances façon Prism).

Rien d'autre ne change. La constante `SCHEMA_VERSION` est partagée par les deux manifestes,
donc les nouveaux `project.json` portent aussi `"2.2.0"` — mais **`project.schema.json` ne
change pas d'un octet** (conséquence mécanique, exactement comme en 2.1).

La compatibilité de version ne teste que le MAJEUR (`validate_manifest`) : un manifeste 2.0
ou 2.1 reste valide vis-à-vis d'un outil 2.2.

## `step_status` (nouveau, toutes familles)

```json
"step_status": { "modeling": "approved", "lookdev": "review" }
```

- **Optionnel.** `additionalProperties: true` sur le manifeste + champ absent des `required`
  → **aucun manifeste 2.0/2.1 existant n'est invalidé**.
- Seules les valeurs **explicites** `review` et `approved` sont persistées
  (`STEP_STATUS_EXPLICIT`). Les statuts `empty` / `wip` / `published` sont **dérivés du
  disque à la lecture** et **jamais écrits** :
  - `published` : un publish `complete` (avec artefact) existe pour le step ;
  - `wip` : au moins un scenefile versionné (`.blend`, `.hip*`) dans `<step>/wip/` ;
  - `empty` : rien.
- Lecture : `create_project.get_step_status(project_root, entity, step=None)` →
  `{step: {"status", "explicit": bool, "derived"}}`. L'explicite gagne ; `derived` reste
  disponible pour l'UI (ex. « approved » mais un nouveau WIP est apparu).
- Écriture : `create_project.set_step_status(project_root, entity, step, status)` — **point
  unique** (web `POST /api/set-step-status`, Blender, Houdini, CLI `set-step-status`).
  `status="auto"` (ou `None`) **efface** l'explicite (retour au dérivé). Écriture atomique
  sous le flock du manifeste, `modified_utc` mis à jour.
- Un step non déclaré dans `steps` → `ValueError` (jamais de création implicite).

## `dependencies` (nouveau, entrées de `step_publishes`)

```json
"step_publishes": {
  "layout": [
    {
      "version": 2, "status": "complete",
      "artifact": "layout/publish/EXTERIOR_Village_Default_layout_v002/EXTERIOR_Village_Default_layout_v002.usda",
      "dependencies": [
        {"entity": "PROP_Tente_Default",  "step": "modeling", "version": 7},
        {"entity": "PROP_Barrel_Default", "step": "modeling", "version": null}
      ]
    }
  ]
}
```

- **Optionnel.** Posé par `finalize_publish_version(..., dependencies=[...])` (nouveau
  paramètre nommé, défaut `None` → clé absente ; `[]` → enregistré vide). Validé **avant**
  le commit (`_normalize_dependencies`) : un payload malformé n'écrit rien.
- `version: null` = référence de root **non épinglée** (`asset_root.usda` / `shot_root.usda`,
  suit toujours le latest) — jamais « en retard ».
- Qui remplit : le DCC au moment du publish, à partir de ce qu'il a importé — Blender :
  les collections d'import taguées du State Manager (`ylos_import_entity/step/version`) ;
  Houdini : les LOPs `reference` / `sublayer` posés par le bridge.

## Index de dépendances (consommateur du champ)

`create_project.build_dependency_index(project_root)` fusionne **deux sources**, sans
fichier annexe à maintenir (le manifeste et les layers sur disque font foi) :

1. `manifest` — les `dependencies` ci-dessus (tous les publishes `complete`) ;
2. `usda` — les asset paths `@…@` des layers USD **ASCII** sur disque (root composé +
   publishes `.usda`) qui pointent dans une **autre** entité du projet (`$PROJ_ROOT`
   expansé comme `ylos_houdini.env_relative` l'écrit). Les layers binaires (`.usdc`) ou
   chiffrés (`.usdnc` Apprentice) sont ignorés silencieusement — d'où l'intérêt de la
   source 1 pour Houdini.

`entity_dependencies(project_root, entity)` → `{"uses", "used_in", "outdated"}` ;
`outdated` = « update disponible » (le dernier publish du consommateur épingle une version
plus ancienne que le latest `complete` de la dépendance). CLI : `dependencies <projet>
[--entity X] [--json]`.

## Consommateurs : traiter l'absence

- `step_status` absent (manifeste 2.0/2.1, ou step jamais statué) → statut **dérivé**,
  jamais un crash ni une écriture implicite.
- `dependencies` absent → le publish n'a pas de dépendances connues côté manifeste ; la
  source `usda` peut encore en révéler. Un consommateur ne suppose jamais la clé présente.

## Ce qui NE change pas

- `project.schema.json` : aucune propriété ajoutée / modifiée / retirée.
- Arborescence source et cache : identiques.
- Manifestes existants : **rien à réécrire**. Ils acquièrent `step_status` /
  `dependencies` uniquement quand `set_step_status` / `finalize_publish_version` sont appelés.
