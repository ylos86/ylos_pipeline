# Migration du schéma `project.json` : 1.0.0 → 2.0.0

> **Statut** : contrat figé (Incrément 1). Le générateur (`create_project.py`) et le
> migrateur des projets existants sont l'**Incrément 2/3**, non encore implémentés.

## Pourquoi un bump MAJEUR

Trois designs concurrents coexistaient :

- **A** — `create_project.py` committé : propre, stdlib, relocalisable, schéma `1.0.0`,
  arbre mince (`_config/`, `assets/`, `shots/`, `ref/`, `delivery/`). **N'a jamais produit
  les projets réels.**
- **B** — projets réels (`YLOS__TEST`, `YLOS_Pachamama_TEST`) : `_pipeline/`, assemblage
  **USD** versionné, `assets/<A>/{modeling,uvs,rigging,lookdev,fx}`, tier `sets/`, manifeste
  par asset, bloc `scene` (AgX/Cycles/2048×1152), `cache/` co-localisé, **`path` absolu en
  dur**, **pas de `schema_version`**. Générateur introuvable.
- **C** — `~/Desktop/create_project.py` : arbre numéroté Blender+Unreal. Expérience morte.

Le schéma `2.0.0` **réconcilie** : il garde les principes verrouillés de A et absorbe le
modèle métier éprouvé de B, en corrigeant les violations de principe de B.

## Décisions appliquées (2026-06-14)

| Axe | Choix |
|-----|-------|
| Design cible | Hybride (principes de A + modèle métier de B) |
| Topologie | **asset-centric** (asset = colonne vertébrale ; `shots/`, `sets/` optionnels) |
| Bibliothèque transverse | **Aucune** (chaque projet autonome, réemploi par copie) |
| Cache | Root interne séparé, **par projet** (`$PROJ_CACHE/<projet>`) |

## `project.json` — table de migration

### Depuis B (projets réels, sans `schema_version`)

| Champ B (réel) | → 2.0.0 | Action |
|----------------|---------|--------|
| *(absent)* | `schema_version` | **AJOUTER** `"2.0.0"` (requis). |
| `project.name` | `name` | Aplatir au top-level. |
| `project.display_name` | `display_name` | Aplatir. |
| `project.prod_type` | `prod_type` | Aplatir. |
| `project.created` (`"2026-06-08"`) | `created_utc` | Convertir en date-time ISO UTC. |
| `project.version` | *(supprimé)* | La version de schéma fait foi, pas une version projet. |
| **`project.path` (absolu)** | *(supprimé)* | **VIOLE le principe 1.** Jamais de chemin absolu : résolution via `$PROJ_ROOT`. |
| `pipeline.*` | `pipeline.*` | Conservé tel quel (`asset_steps`, `shot_steps`, `set_steps`, `usd_root_prim`). |
| `scene.*` | `scene.*` | Conservé tel quel. |
| `delivery.targets` | `delivery.targets` | Conservé. |
| *(absent)* | `env` | **AJOUTER** `{"root":"$PROJ_ROOT","cache":"$PROJ_CACHE"}`. |
| *(absent)* | `structure` | **AJOUTER** la trace de l'arbre réellement créé. |
| *(absent)* | `cache_per_project` | **AJOUTER** `true`. |
| *(absent)* | `topology` | **AJOUTER** `"asset-centric"`. |

### Depuis A (committé `1.0.0`)

| Champ A | → 2.0.0 | Action |
|---------|---------|--------|
| `schema_version` `"1.0.0"` | `"2.0.0"` | Bump majeur. |
| `env`, `structure`, `cache_per_project`, `dcc`, `status` | identiques | Conservés. |
| *(absent)* | `pipeline` | **AJOUTER** (requis) — défauts de steps. |
| *(absent)* | `scene` | **AJOUTER** (requis) — défauts DCC. |
| *(absent)* | `delivery`, `topology` | **AJOUTER**. |

## Manifeste d'asset (`<asset>/manifest.json`) — nouveau contrat `asset.schema.json`

Incohérence observée dans B à corriger :

- `Lina` : `entity_type: "asset"` + `type: "CHARACTER"` ✅ cohérent.
- `lecube` : `type: "asset"` seul, **pas de `entity_type`** ❌ (confond famille et sous-type).

Règle 2.0 : `entity_type` ∈ `{asset, set, shot}` (la **famille**) ; `type` = **sous-type
métier** (`CHARACTER`, `ENVIRONMENT`, `PROP`, …). Migration `lecube` → `entity_type:"asset"`,
`type:"PROP"` (ou `OTHER`). Ajouter `schema_version:"2.0.0"` à chaque manifeste.

## Convention USD à figer (incohérence B)

Les `asset_root.usd` de B ne sont pas cohérents et doivent être normalisés avant que le
générateur les produise :

| Point | `Lina` | `lecube` | À trancher |
|-------|--------|----------|-----------|
| Composition | `references` | `subLayers` | **Choisir une règle** (refs pour assets discrets, subLayers pour layering de steps ?). |
| Extension | `.usda` | `.usd` | **Une seule** (ASCII `.usda` lisible, ou binaire `.usd`). |
| Prim racine | `</root>` | (defaultPrim) | Aligner sur `pipeline.usd_root_prim` (`/ROOT`) — **casse incluse**. |

> ⚠️ Décision USD non figée par cette migration : à trancher au début de l'Incrément 2,
> avant que `create_project.py` n'émette des stubs `asset_root.usd`.

## Cache : co-localisé → root interne séparé

B mettait `cache/{alembic,simulations}` **dans** le projet (même disque que la source). En
2.0, le cache vit sous `$PROJ_CACHE/<projet>/` (NVMe interne). Migration = déplacer le cache
hors de l'arbre source et le recréer sous `$PROJ_CACHE`. Le cache étant régénérable, on peut
aussi simplement le supprimer et le laisser se reconstruire.
