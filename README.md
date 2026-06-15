# Pipeline Ylos Prod

Scaffolder de projet 3D/VFX. `create_project.py` est la **source de vérité unique** de la
logique de création : importable par les plugins DCC (Houdini, Blender), **stdlib seule**
(les interpréteurs embarqués hython / Blender ne doivent pas dépendre d'un `pip install`).

## Arborescence créée (schéma 2.0 — asset-centric)

```
$PROJ_ROOT/<projet>/          # SOURCE — disque externe, permanent, versionné
  _pipeline/                  #   project.json (manifeste)
  assets/                     #   COLONNE VERTÉBRALE (asset-centric)
    <asset>/
      manifest.json           #     entity_type / type / steps / publishes
      asset_root.usda         #     assemblage USD (références des publishes versionnés)
      <step>/wip/             #     travail DCC (.blend versionnés)
      <step>/publish/         #     sorties USD versionnées (un dossier par step)
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
export PROJ_CACHE=$HOME/ylos_cache      # cache (interne, jetable)
```

Les scènes DCC référencent `$PROJ_ROOT/...` — **jamais de chemin absolu en dur**. Un projet
se déplace entre disques sans casser les références. À terme, ces env vars devraient être
posées **par session** par un launcher / plugin (lecture du manifeste → set des vars),
pas exportées globalement : sinon collision quand Houdini et Blender pointent sur deux
projets différents en simultané.

## Usage

CLI (sous-commandes `project` / `asset`) :

```bash
# Projet (coquille asset-centric)
python create_project.py project "nom_projet"
python create_project.py project "nom_projet" --root /Volumes/EXT/3D --cache ~/cache --force

# Entité dans un projet existant (asset / set / shot)
python create_project.py asset "<projet>" "Lina" --type CHARACTER
python create_project.py asset "<projet>" "Cuisine" --entity-type set --steps modeling,lookdev
```

Import (plugin DCC) :

```python
import create_project
info  = create_project.create("nom_projet")
asset = create_project.create_asset(info["source"], "Lina", asset_type="CHARACTER")
manifest = create_project.read_manifest(info["source"])
create_project.validate_manifest(manifest)
```

`create()` et `create_asset()` sont non destructifs : `force` lève seulement le garde-fou
d'existence, ils ne suppriment jamais rien. `create_asset()` reprend par défaut les `steps`
du `pipeline` du manifeste projet (sinon les défauts du module).

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

- **Convention USD** — figée : `subLayers` (asset stack), `.usda` compo / `.usdc` géo,
  `defaultPrim = /<NomAsset>`. Cf. [`docs/usd-convention.md`](docs/usd-convention.md).

## Production ≠ pipeline

Ce repo ne gère **que** le pipeline technique (arbo, manifeste, versioning). La gestion de
production (statut client, deadlines) est un problème distinct, à traiter ailleurs
(n8n / outil dédié). Le manifeste expose `additionalProperties: true` pour qu'un autre
outil puisse l'étendre sans casser le contrat — mais ne fusionne pas les deux logiques.
