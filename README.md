# Pipeline Black Kite

Scaffolder de projet 3D/VFX. `create_project.py` est la **source de vérité unique** de la
logique de création : importable par les plugins DCC (Houdini, Blender), **stdlib seule**
(les interpréteurs embarqués hython / Blender ne doivent pas dépendre d'un `pip install`).

## Arborescence créée

```
$PROJ_ROOT/<projet>/        # SOURCE — disque externe, permanent, versionné
  _config/                  #   project.json (manifeste)
  assets/                   #   asset-centric (réutilisable)
  shots/                    #   shot-centric (seq/shot créés à la demande)
  ref/ai/                   #   références IA (Midjourney / NanoBanana) + metadata
  ref/photo/                #   références photo
  ref/board/                #   moodboards / planches
  delivery/                 #   masters
  .gitignore  .metadata_never_index

$PROJ_CACHE/<projet>/       # CACHE — disque interne, régénérable, hors Git
  houdini/  blender/  render/  tmp/
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

`project.json` est lu par le créateur, n8n et les 2 plugins. Schéma documenté :
`project.schema.json`. Tout changement de structure = **bump `schema_version` + migration**,
pas édition silencieuse. `validate_manifest()` refuse une version **majeure** incompatible.

## Décisions encore ouvertes (cf. `CLAUDE.md`)

- **Topologie** — tree hybride `assets/` + `shots/`. À élaguer si le travail est purement
  l'un ou l'autre, ou à remonter `assets/` au-dessus des projets (bibliothèque transverse).
  Modifier `SOURCE_TREE`.
- **Cache** — `CACHE_PER_PROJECT = True` (par projet). Basculer en cache centralisé unique
  si pertinent.

## Production ≠ pipeline

Ce repo ne gère **que** le pipeline technique (arbo, manifeste, versioning). La gestion de
production (statut client, deadlines) est un problème distinct, à traiter ailleurs
(n8n / outil dédié). Le manifeste expose `additionalProperties: true` pour qu'un autre
outil puisse l'étendre sans casser le contrat — mais ne fusionne pas les deux logiques.
