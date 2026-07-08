# Migration du schéma : 2.0.0 → 2.1.0

> **Statut** : implémenté (plan Houdini shots, Incrément 2). Générateur
> (`create_project.py`) à jour. **Aucune migration de fichiers requise** — changement
> strictement additif.

## Pourquoi un bump MINEUR

2.1.0 introduit **une seule** nouveauté : le champ optionnel `frame_range` sur le manifeste
d'un **shot** (`asset.schema.json`). Rien d'autre ne change. La constante `SCHEMA_VERSION`
est partagée par les deux manifestes (`project.json` et `manifest.json`), donc les nouveaux
`project.json` portent aussi `"2.1.0"` — mais **`project.schema.json` ne change pas d'un
octet** : le bump n'y est que la conséquence mécanique de la constante partagée.

La compatibilité de version ne teste que le MAJEUR (`validate_manifest`) : un manifeste 2.0
reste valide vis-à-vis d'un outil 2.1 (et inversement pour les champs qu'il connaît).

## `frame_range` (nouveau, shots uniquement)

```json
"frame_range": { "start": 1001, "end": 1100, "fps": 24 }
```

- **Optionnel.** `additionalProperties: true` sur le manifeste + champ absent des `required`
  → **aucun manifeste 2.0 existant n'est invalidé**.
- Les trois clés (`start`, `end`, `fps`) sont requises **si l'objet est présent**
  (`additionalProperties: false` dans le sous-schéma). Contrainte `start < end` vérifiée
  côté code (`set_frame_range`), non exprimable en JSON Schema draft-07.
- Posé par défaut à la **création** d'un shot (`build_asset_manifest`,
  `{start: 1001, end: 1100, fps: DEFAULT_SCENE["fps"]}`). Édité ensuite via
  `create_project.set_frame_range(project_root, shot, start, end, fps=None)` (ou la CLI
  `set-frame-range`), qui recompose `shot_root.usda` (timecodes).
- Alimente les timecodes du `shot_root.usda` (`startTimeCode` / `endTimeCode` /
  `timeCodesPerSecond`, cf. `build_shot_root`, Incrément 1).

## Consommateurs : traiter l'absence

Un shot créé en 2.0 **n'a pas** de `frame_range`. Tout consommateur (n8n, DCC, composeur)
doit traiter la clé manquante par un **fallback**, jamais par un crash :

- `build_shot_root` / `refresh_entity_root` : `frame_range` absent → shot_root sans
  timecodes (déjà le comportement, cf. `_compose_entity_root`).
- Convention future (rendu Karma, Incrément 6) : `frame_range` absent → fallback sur le
  range du hip, avec warning.

## Ce qui NE change pas

- `project.schema.json` : aucune propriété ajoutée / modifiée / retirée.
- Arborescence source et cache : identiques.
- Manifestes existants : **rien à réécrire**. Un shot 2.0 sans `frame_range` reste valide ;
  il n'en acquiert un que si `set_frame_range` est appelé dessus.
