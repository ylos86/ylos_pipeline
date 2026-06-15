# Pipeline Black Kite

Scaffolder de projet 3D/VFX. `create_project.py` est la **source de vérité unique** de la
logique de création : importable par les plugins DCC (Houdini, Blender), **stdlib seule**
(les interpréteurs embarqués hython / Blender ne doivent pas dépendre d'un `pip install`).

## Arborescence créée (cible schéma 2.0 — asset-centric)

> Le générateur émet encore l'arbre `1.0` (`_config/`, plat). L'alignement sur cette cible
> est l'**Incrément 2**.

```
$PROJ_ROOT/<projet>/          # SOURCE — disque externe, permanent, versionné
  _pipeline/                  #   project.json (manifeste)
  assets/                     #   COLONNE VERTÉBRALE (asset-centric)
    <asset>/
      manifest.json           #     entity_type / type / steps / publishes
      asset_root.usd          #     assemblage USD (compose les publishes versionnés)
      <step>/publish/         #     un dossier par step déclaré (modeling, uvs, lookdev…)
  sets/                       #   assemblage — optionnel (scaffold vide)
  shots/                      #   shots — optionnel (scaffold vide)
  references/                 #   refs projet (ai / photo / board)
  resources/                  #   hdri/ textures/ (réutilisable intra-projet)
  delivery/                   #   masters (cf. delivery.targets)
  .gitignore  .metadata_never_index

$PROJ_CACHE/<projet>/         # CACHE — NVMe interne, régénérable, hors Git
  houdini/  blender/  render/  alembic/  sim/  tmp/
```

## Variables d'environnement (design relocalisable)

```bash
export PROJ_ROOT=/Volumes/EXT_NVME/3D        # source (externe, permanent)
export PROJ_CACHE=$HOME/blackkite_cache      # cache (interne, jetable)
```

Les scènes DCC référencent `$PROJ_ROOT/...` — **jamais de chemin absolu en dur**. Un projet
se déplace entre disques sans casser les références. À terme, ces env vars devraient être
posées **par session** par un launcher / plugin (lecture du manifeste → set des vars),
pas exportées globalement : sinon collision quand Houdini et Blender pointent sur deux
projets différents en simultané.

## Usage

CLI :

```bash
python create_project.py "nom_projet"
python create_project.py "nom_projet" --root /Volumes/EXT/3D --cache ~/cache --force
```

Import (plugin DCC) :

```python
import create_project
info = create_project.create("nom_projet")
manifest = create_project.read_manifest(info["source"])
create_project.validate_manifest(manifest)
```

`create()` est non destructif : `force` lève seulement le garde-fou d'existence, il ne
supprime jamais rien.

## Le manifeste est un contrat

`project.json` est lu par le créateur, n8n et les 2 plugins. Schémas documentés (figés en
`2.0.0`) :
- `project.schema.json` — manifeste **projet** (`pipeline`/`scene`/`delivery` + principes).
- `asset.schema.json` — manifeste **par entité** (`<asset>/manifest.json` : `entity_type`,
  `type`, `steps`, `publishes`).

Tout changement de structure = **bump `schema_version` + migration**, pas édition
silencieuse. `validate_manifest()` refuse une version **majeure** incompatible. La migration
`1.0.0 → 2.0.0` est documentée dans [`docs/migration-1.0-to-2.0.md`](docs/migration-1.0-to-2.0.md).

## Décisions tranchées (2026-06-14, cf. `CLAUDE.md`)

- **Design cible** — hybride : principes verrouillés + modèle métier USD des projets réels.
- **Topologie** — **asset-centric**. `assets/` est la colonne vertébrale ; `shots/`/`sets/`
  optionnels (scaffold vide).
- **Bibliothèque transverse** — **aucune**. Chaque projet autonome, réemploi par copie.
- **Cache** — root interne séparé, **par projet** (`$PROJ_CACHE/<projet>`), jamais
  co-localisé avec la source.

Reste à figer au début de l'Incrément 2 : la **convention USD** (`references` vs
`subLayers`, `.usd` vs `.usda`, casse de `/ROOT`).

## Production ≠ pipeline

Ce repo ne gère **que** le pipeline technique (arbo, manifeste, versioning). La gestion de
production (statut client, deadlines) est un problème distinct, à traiter ailleurs
(n8n / outil dédié). Le manifeste expose `additionalProperties: true` pour qu'un autre
outil puisse l'étendre sans casser le contrat — mais ne fusionne pas les deux logiques.
