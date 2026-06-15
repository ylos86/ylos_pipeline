# CLAUDE.md — Pipeline Ylos Prod

## Contexte
Pipeline de production 3D/VFX freelance (marque **Ylos Prod**). Objectif : un système
de **gestion de production + pipeline d'assets**, scalable et améliorable, destiné à être
étendu dans le temps par des agents d'automatisation (n8n) et des plugins DCC (Houdini,
Blender). On construit depuis zéro **en anticipant ces consommateurs futurs**, pas
seulement le workflow humain immédiat.

## Environnement
- **Machine** : MacBook Pro M2 Max, 64 Go, **macOS uniquement** (Apple Silicon).
  Toute dépendance native doit tourner sur arm64. Pas de solution Windows-only.
- **Stockage, 3 tiers** :
  - NVMe interne → **cache régénérable** (rapide, jetable)
  - NVMe externe Thunderbolt 4 → **source / projets actifs** (permanent)
  - Disques mécaniques → **archive froide uniquement** (I/O aléatoire inadapté à la 3D,
    jamais de projet actif)
- **Python** : **stdlib privilégiée** pour tout module qu'un DCC doit importer. Raison :
  les interpréteurs embarqués (hython, le Python de Blender) ne doivent pas dépendre d'un
  `pip install`. Une dépendance tierce dans le chemin d'import = friction.

## Principes d'architecture (verrouillés)
1. **Racine relocalisable** : variables d'environnement `$PROJ_ROOT`, `$PROJ_CACHE`.
   Jamais de chemin absolu en dur. Les scènes référencent via env → un projet se déplace
   entre interne et externe sans casser les références.
2. **Séparation cache / source** : interne rapide pour le régénérable, externe pour le
   permanent. Les deux tiers ne se mélangent pas.
3. **Source de vérité lisible par machine** : le manifeste `project.json` fait foi, pas
   les conventions de nommage de dossiers.
4. **Séparation des responsabilités** : la **gestion de production** (jobs, statut,
   deadlines) et le **pipeline technique d'assets** (arborescence, ingestion, versioning)
   sont deux problèmes distincts. Ne pas les fusionner dans l'archi.
5. **Logique unique** : `create_project.py` est importable par les plugins DCC. La logique
   de création vit à **un seul endroit**, jamais dupliquée entre outils.

## État actuel
`create_project.py` (stdlib, importable par les DCC) émet la structure **schéma 2.0.0** via
deux sous-commandes / fonctions :
- `project` / `create()` → coquille asset-centric (`_pipeline/project.json`, `assets/`,
  `sets/`+`shots/` vides, `references/`, `resources/`, `delivery/`, `edit/`) + cache séparé
  sous `$PROJ_CACHE/<projet>`.
- `asset` / `create_asset()` → scaffolde une entité (asset/set/shot) : steps → dossiers
  (+`publish/`), `manifest.json`, stub `asset_root.usda` (convention USD).
- `.metadata_never_index` (anti-Spotlight) + `.gitignore` (cache, rendus, `*.usdc` hors Git).

Contrats figés : `project.schema.json`, `asset.schema.json`, `docs/usd-convention.md`,
`docs/migration-1.0-to-2.0.md`. Validé end-to-end (arbre + JSON conformes aux schémas).

**Reste :** migrer les projets réels existants (`YLOS__TEST`, `Pachamama`) vers 2.0
(Incrément 3) ; retirer le `~/Desktop/create_project.py` mort (Incrément 4) ; vérifier
l'up-axis Blender↔USD à l'usage.

## Décisions tranchées (2026-06-14)
1. **Design cible : hybride.** Garder les principes verrouillés, absorber le modèle métier
   des projets réels (USD, `scene`, steps, manifeste par asset). Schéma `1.0.0` → `2.0.0`.
2. **Topologie : asset-centric.** L'asset est la colonne vertébrale (step-folders +
   `manifest.json` + `asset_root.usd`). `shots/` et `sets/` = scaffolding optionnel (créés
   vides), non first-class.
3. **Pas de bibliothèque transverse.** Chaque projet est autonome, réemploi par copie.
   Pas de `$ASSET_LIB`. (Doublon `Casa` entre projets assumé.)
4. **Cache : root interne séparé, par projet** (`$PROJ_CACHE/<projet>`). Le `cache/`
   co-localisé des projets réels est abandonné (corrige la violation du principe 2).

### Conventions tranchées
- Nom du dossier config : **`_pipeline/`** (≠ `_config/` du code committé).
- **Convention USD figée** (`subLayers` intra-asset, `.usda` compo / `.usdc` géo,
  `defaultPrim = /<NomAsset>`) : cf. `docs/usd-convention.md`. Reste à vérifier à l'Inc. 2 :
  l'**up-axis** (fichiers réels en Y-up alors que renderer = Cycles/Blender Z-up).

## Conventions
- Communication : **français**. Code, identifiants, noms de fichiers : **anglais**.
  (À inverser si le code existant est déjà en français.)
- `pathlib` partout, pas de concaténation manuelle de chemins.
- `project.json` est un **contrat**, pas juste un fichier : lu par le créateur, n8n et
  2 plugins. Tout changement de schéma casse des lecteurs → champ `schema_version`
  obligatoire + schéma documenté. Le faire évoluer = **migration**, pas édition silencieuse.

## Tensions connues (à garder en tête)
- **Collision d'env vars** : `$PROJ_ROOT` est global au shell. Si Houdini et Blender
  pointent sur deux projets différents en simultané, la variable globale entre en conflit.
  Piste : env posée **par-session** par le plugin/launcher, plutôt qu'un export global.
  À résoudre avant que le multi-projet simultané devienne réel.

## Mode de collaboration attendu
- **Lis avant d'écrire.** Avant toute mutation de fichier, mappe l'état réel du repo.
- **Plan avant action** pour toute opération destructive ou tout changement de schéma.
- Ne casse pas l'**importabilité** : pas d'import lourd au niveau module dans ce que les
  DCC chargent.
- **Challenge l'archi.** Si une décision a une implication downstream que je n'ai pas vue,
  nomme-la avant d'exécuter. Réframe une intention mal posée plutôt que de la suivre
  aveuglément.
