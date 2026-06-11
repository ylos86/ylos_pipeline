## [0.3.0] - 2026-06-10

### Architecture — Phase 1: monorepo extraction

This release restructures the codebase into a monorepo without any change to
Blender-visible behaviour. All v0.2.7 workflows pass unchanged.

#### New layout
```
ylos_pipeline/
  ylos_core/        pure stdlib — shared contract, no bpy/hou/pxr
  ylos_blender/     Blender addon (bpy layer), vendors ylos_core at build time
  tests/            116 pytest tests, bpy-free, run with: python3 -m pytest
  build.py          vendors core + produces installable zip
```

#### New modules in `ylos_core`
- `naming.py` — pure naming constants + helpers extracted from `scene_checker`
  and `asset` (`sanitize_entity_name`, `validate_entity_name`, `get_next_step`,
  `name_matches_asset`, `PREFIXES`, `STEP_ORDER`)
- `locking.py` — `atomic_write_text` / `atomic_write_json` for mutable files
  (entity roots and `project.json`); pattern: write `.tmp` then `os.replace()`
- `manifest.py` — publish sidecar writer/reader (`write_publish_sidecar`,
  `read_publish_sidecar`, `find_removed_prims`); implements §5 of arch doc

#### `project.json` schema v2
- Added `"schema_version": 2` to all newly created projects
- Added `"step_owners"` matrix (modeling/rigging/fx → blender,
  lookdev/layout → houdini, lighting/rendering → any)
- `load_project()` forward-guards: refuses with a clear error if
  `schema_version` is newer than the installed addon
- v1 projects (no `schema_version` key) load without error; missing keys
  are injected with defaults on first read

#### `ylos_blender` internal changes
- `core/` split into `ylos_core/` (pure) and `core_bpy/` (bpy-dependent):
  - `core_bpy/project_bpy.py` — `apply_scene_preset`, `setup_scene_collections`,
    `register_properties`, `unregister_properties`
  - `core_bpy/scene_checker.py` — all check functions; imports pure naming
    from `ylos_core.naming`
  - `core_bpy/thumbnails.py` — unchanged
- `__init__.py` injects `_vendor/` into `sys.path` at module load time
- Version bumped to `(0, 3, 0)`

---

## [0.3.3] - 2026-06-11

### Correctifs v0.3.1 — C1-C7

#### C1 — Atomicite : locking.py branche (code mort elimine)
- `ylos_core/usd_composer.py` : toutes les ecritures de root passent par
  `locking.atomic_write_text()`. Plus aucun `open(path, "w")` dans ce module.
- `ylos_core/project.py` : `_write_project_json()` delegue a
  `locking.atomic_write_json()`. La docstring qui renvoyait la responsabilite
  aux callers est supprimee.
- `ylos_core/manifest.py` : sidecar ecrit via `atomic_write_json`. Le guard
  `FileExistsError` reste AVANT l'ecriture.
- `ylos_core/locking.py` : `import json` remonte au niveau module.

#### C2 — Immutabilite des publishes
- `op_publish.execute()` : avant l'export, recherche le premier slot libre
  (USD + sidecar absents) en incrementant depuis la version demandee.
  Report si la version est changee : "v001 existe -- publie en v002".
  Aucun chemin de code ne peut plus ecraser un publish existant.
- `op_publish.draw()` : le label "WARNING: will overwrite" est remplace par
  une info non-anxiogene : "vNNN existe -- sera publie en vMMM".
- Le `except FileExistsError: pass` sur le sidecar est supprime. La garantie
  de slot libre en amont rend une collision impossible ; si elle survient
  malgre tout, c'est une vraie anomalie reportee en WARNING.

#### C3 — Purge de sys.modules a l'unregister
- `ylos_blender/__init__.py` : au chargement du module, si `ylos_core` est
  deja dans `sys.modules` avec un `__file__` ne pointant pas vers notre
  `_vendor`, purge avant injection. A l'unregister, purge complete de
  `ylos_core` et `ylos_core.*` de `sys.modules`.
- Hypothese documentee dans `DEVELOPER.md` : un seul addon Ylos par session.
- `CORE_VERSION = "0.3.1"` expose dans `ylos_core/__init__.py`.

#### C4 — step_owners : lu et applique
- `ylos_core/project.py` : ajout de `get_step_owner(config, step) -> str`.
- `op_publish` : si `step_owner not in ("blender", "any")`, le dialog affiche
  un avertissement avec une checkbox `confirm_foreign_step`. L'execute est
  bloque tant que la checkbox n'est pas cochee.

#### C5 — Conformite ASCII
- Remplacement de tous les caracteres section (U+00A7, present dans les
  commentaires) par "S-" dans l'ensemble du repo.
- Remplacement de U+21BA (fleche de rafraichissement dans panel.py) par "~".
- Ajout de `tests/test_ascii_compliance.py` : test parametrise qui ouvre
  chaque `.py` du repo en `encoding="ascii"` -- la regle est desormais
  auto-appliquee.

#### C6 — Harness Phase 1 complete
- `build.py` : `_VENDORED` contient desormais un hash SHA-256 (16 car.) de
  tous les `.py` du core plus un timestamp ISO. Detecte les edits manuels
  de `_vendor`.
- `ylos_core/__init__.py` : `CORE_VERSION = "0.3.1"`.
- `bl_info` bumpe a `(0, 3, 1)`.
- Ajout de `tests/test_composer_roundtrip.py` : tests USD round-trip
  specifiques a usd-core, incluant `HasAuthoredPayloads()` sur le prim FX,
  cycle Load/Unload, coexistence modeling + FX.

#### C7 — Ajout mineur
- `ylos_core/naming.py` : ajout de `validate_texture_paths_relative(paths,
  project_root)` -- retourne les chemins absolus ou hors-projet. Destine au
  publish lookdev Houdini (Phase 4, arch doc S-3.1). Couvert par tests.

#### Tests
- `tests/test_ascii_compliance.py` : nouveau, parametrise sur tous les .py.
- `tests/test_composer_roundtrip.py` : nouveau, 9 tests usd-core.
- `tests/test_project.py` : 6 nouveaux tests `TestGetStepOwner`.
- `tests/test_naming.py` : 5 nouveaux tests `TestValidateTexturePathsRelative`.
- Total : **209 tests verts**.

---

## [0.3.2] - 2026-06-11

### Architecture — Phase 3: FX payload + prim stability + sidecars

All changes are Blender-side. Zero behaviour change for non-FX workflows.

#### `ylos_core/usd_composer.py`
- FX step is now always emitted as a **payload arc** on `/ROOT/{Entity}/fx`,
  never as a sublayer. Heavy time-sampled caches are deferred until the
  consumer activates the payload (arch doc §3.2). Validated with usd-core:
  `Usd.Stage.Open(..., load=Usd.Stage.LoadNone)` confirms the FX scope is
  present but unloaded at open time.
- `write_usda_with_variants`: new `fx_payload_path` parameter; injects
  `def Scope "fx" (prepend payload = @rel@)` as a child of the entity prim
  in both no-variant and variant cases.
- `compose_asset_root`: separates FX from other steps; returns
  `fx_payload_path` in the result dict; FX note appended to the message.
- `read_root_fx_payload`: new reader, no pxr required.
- `FX_STEP = "fx"` constant exported.

#### `ylos_blender/operators/op_publish.py` (full rewrite)
- **Sidecar written after every successful publish** via
  `ylos_core.manifest.write_publish_sidecar` (entity, step, version,
  `dcc="blender"`, `dcc_version`, `prim_paths`, `variant`, `source_wip`,
  `frame_range` for FX).
- **Prim stability check** before modeling publish N>1 (arch doc §4):
  compares derived prim paths against the previous publish sidecar via
  `find_removed_prims`. Removed prims trigger a blocking-confirmable
  warning listing the missing paths with an explicit note that Houdini
  lookdev overs will be broken. A `confirm_stability` checkbox must be
  enabled to proceed.
- **FX animated export**: `export_animation=True` with explicit
  `fx_frame_start` / `fx_frame_end` (pre-populated from scene range,
  editable in the publish dialog).
- `_get_prim_paths(objects, entity_name)` — derives `/ROOT/{entity}/{obj.name}`
  for MESH/ARMATURE/CURVE objects.

#### Tests
- 17 new `TestFxPayloadOutput` tests (USDA text validation).
- 3 new `TestFxPayloadUsdCore` round-trip tests requiring `usd-core`.
- Total: **133 tests**.

---

## [0.3.1] - 2026-06-11

### Architecture — Phase 2: Houdini read-only adapter

All Houdini work. Zero change to Blender adapter behaviour.
Fully usable under Apprentice 21.0.631 (read USD, save .hipnc).
Publish actions (Phase 4) remain gated on Indie licence.

#### New package: `ylos_houdini/`
```
ylos_houdini/
  packages/ylos_pipeline.json    Houdini package — PYTHONPATH + HOUDINI_PATH
                                 via $YLOS_PIPELINE_PATH env variable
  startup/ylos_init.py           Registers afterLoad callback at Houdini start
  python_panels/ylos_pipeline.pypanel
  toolbar/ylos.shelf             Tools: "Ylos" (open panel) + "Save WIP"
  pythonlibs/ylos_houdini/       Python adapter modules (see below)
```

#### `ylos_houdini/session.py` — `YlosSession` singleton
- Holds active project/entity/step context for the Houdini session.
- **Hip persistence**: `hou.node('/').setUserData("ylos_context", ...)` written
  on every context mutation; restored by the `afterLoad` callback.
- **Conflict resolution**: hip userData always wins over `ylos_prefs.json`.
  Prefs are only consulted when opening a blank hip with no context.
- **`ylos_prefs.json`**: persists last-used project path in
  `$HOUDINI_USER_PREF_DIR/` for blank-hip auto-load.
- **License detection**: `hou.licenseCategory()` cached on first access.
  `can_publish()` returns False for Apprentice/Education.
- **Change notification**: `register_on_change(callback)` for panel refresh.

#### `ylos_houdini/wip.py` — versioned WIP save
- Extension determined by licence: Apprentice → `.hipnc`, Indie → `.hiplc`,
  Commercial → `.hip`.
- Version detection reuses `ylos_core.asset.list_wip_versions` parameterised
  by `HIP_EXTENSIONS` — no duplication of version logic.
- `save_wip_dialog()`: Houdini `hou.ui.readInput` dialog, callable from shelf.

#### `ylos_houdini/lop_utils.py` — LOP stage import
- `import_asset_to_stage()`: creates a Reference LOP node pointing to
  the entity's `asset_root.usd` in the active Solaris stage.
- `import_current_entity()`: panel button convenience wrapper.
- `hda_import_asset_cook()`: cook script for the `Ylos Import Asset` HDA.

#### `ylos_houdini/panel.py` — PySide2/6 Python Panel
Three tabs mirroring the Blender addon:
- **Pipeline**: project load, entity/step selectors, licence badge,
  Apprentice notice when publish is disabled.
- **Assets**: entity list with step-status colour grid (MOD/RIG/LKD/FX).
- **Scene**: Save WIP, Open Root in Solaris, WIP history, publish history.
Auto-refreshes on session change via `YlosSession.register_on_change`.

#### `create_hdas.py` (repo root)
- Run once from the Houdini Python Shell to generate
  `ylos_houdini/otls/ylos_import_asset.hda` (LOP type `ylos::import_asset::1.0`).

#### `ylos_core/asset.py` extensions (backward-compatible)
- `list_wip_versions`: new `exts` param (default `["blend"]`).
- `build_wip_filename`: new `ext` param (default `"blend"`).
- `resolve_wip_save_path`: new `ext` param.
- `get_latest_wip_version`: new `exts` param.
- `HIP_EXTENSIONS = ("hip", "hiplc", "hipnc")` exported.

#### `build.py`
- Houdini package build now functional: vendors `ylos_core` into
  `pythonlibs/ylos_core/`, copies to `dist/ylos_houdini/` with
  install instructions.

#### Installation (Houdini)
```bash
python3 build.py --houdini
# -> dist/ylos_houdini/
mv dist/ylos_houdini ~/tools/ylos_houdini
# houdini.env:
YLOS_PIPELINE_PATH = /Users/<you>/tools/ylos_houdini
cp $YLOS_PIPELINE_PATH/packages/ylos_pipeline.json $HOUDINI_USER_PREF_DIR/packages/
# Then inside Houdini Python Shell:
exec(open("/path/to/ylos_pipeline/create_hdas.py").read())
```

---

## [0.2.7] - 2026-06-10

### Added
- `sanitize_entity_name()` — strips spaces, hyphens, illegal chars; joins words PascalCase-style
- `validate_entity_name()` — enforces non-empty, >=2 chars, uppercase first letter
- `ASSET_TYPE_PARENT_COL` — maps PROP/CHARACTER/ENVIRONMENT to target COL_ hierarchy
- `ASSET_TYPE_PREFIXES` — USD file domain prefix per asset sub-type
- Asset `manifest.json` now stores `entity_type` (asset/shot/set) + `type` (PROP/CHARACTER/ENVIRONMENT)
  so the asset list panel shows the correct icon without inferring from name

### Changed
- `create_asset()` accepts new `asset_type` kwarg (default "PROP")
- `op_new_asset`: asset collection is now placed under the correct parent collection:
  - PROP -> COL_ENV / COL_ENV_Props
  - CHARACTER -> COL_CHAR
  - ENVIRONMENT -> COL_ENV
  - SHOT -> COL_SHOTS
  - SET -> COL_ENV / COL_SETS
- `op_new_asset`: dialog shows live preview of sanitized name and collection target
- Collection creation now applies to ASSET, SHOT, and SET (was ASSET only)

# Changelog

All notable changes to Ylos Pipeline are documented here.
Format: [Semantic Versioning](https://semver.org) -- `MAJOR.MINOR.PATCH`

---

## [0.2.6] -- 2026-06-09

### Fixed
- **Load Project lost folder browsing.** The 0.2.4 fix for the macOS trailing-`@`
  bug switched the path field to `subtype="NONE"`, which removed the browse
  button and forced users to paste a path by hand. Load Project now opens
  Blender's native file browser (`fileselect_add`) so you can navigate to the
  project folder. The native browser does not suffer the `@` corruption, and
  pasted/typed paths are still sanitized.

### Added
- **Tolerant project resolution.** When loading, you can select the project
  folder itself, any sub-folder inside it (resolved by walking up), or the
  parent folder when it contains exactly one project (resolved by walking down
  one level). Ambiguous parents are rejected with a clear message.

---

## [0.2.5] -- 2026-06-09

### Changed
- **UI readability pass on the header popup.**
  - Persistent state header shows project + production type, then the active
    asset and the current step spelled out in full (not just the abbreviation).
  - Errors now render in red via `alert`, with semantic icons (red X for
    errors, warning triangle for warnings, check for clean).
  - Scene-check issues are laid out on two lines (object + Fix on top, indented
    message below) so long messages no longer truncate inside the popup.
  - Primary actions (Save WIP, Publish Step, Scan, Fix All) are enlarged and
    visually separated from secondary picker buttons.
  - Empty states guide the next action instead of just stating "none".
  - Popup widened to 400px for breathing room.
- N-panel polish: version labels right-aligned with clearer "none yet"
  placeholders; unsaved-changes warning now uses the same red alert style.

---

## [0.2.4] -- 2026-06-09

### Fixed
- **Publish no longer silently exports the full scene.** When an asset was
  targeted but no objects resolved, the old fallback re-ran `usd_export` with
  only `filepath`, exporting the entire scene under the asset's publish name.
  The scoped export now aborts with a clear error instead, with an opt-in
  `Allow Full-Scene Export` toggle for the deliberate case.
- **Collection membership check inverted.** `obj.name not in coll.all_objects`
  compared a string against a collection of objects (always True), so the
  "not in collection" warning fired permanently. Now compares against the set
  of object names.
- **Duplicate `YLOS_OT_SwitchAsset` class** removed. Two same-named operator
  classes lived in `op_open_wip` and `op_switch_context`. The redundant browser
  variant is gone; the "Browse" button now opens the searchable asset popup.
- **Asset step toggles were misaligned.** `_ASSET_STEP_LABELS` still listed the
  removed "UVs" step (5 labels) against a 4-slot `BoolVectorProperty`, shifting
  every toggle. Labels resynced; a load-time assertion guards future drift.
- **WIP version detection picked up backups.** The version regex matched any
  `_vNNN.` token, so Blender `.blend1`/`.blend2` autosaves and thumbnails could
  register as versions. Now anchored to `.blend` with an extension filter.
- **`subtype="DIR_PATH"` reintroduced** on the Load Project path field (the
  macOS trailing-`@` corruption bug). Reverted to `subtype="NONE"`.
- **Object-to-asset name matching** required only a prefix match, so
  `GEO_HeroSword` matched asset `Hero`. Now requires a whole-field match.

### Changed
- **USDA composition unified and validated.** `defaultPrim` is now always
  `ROOT` (entity prim at `/ROOT/{Name}`) across asset and set roots; the dead
  `USDA_HEADER` constant was removed. The variantSet syntax was rewritten to
  canonical USDA and validated against `usd-core` (opens cleanly, variants list
  and switch correctly, references resolve). Added `read_root_variants()` so the
  variant blocks we write can be read back.
- **Step validation at publish.** `ylos_current_step` still lists every step for
  UI convenience, but publishing now refuses a step that has no folder for the
  active context (e.g. `composite` on an asset).
- All Python sources are now strictly ASCII (Windows compatibility); UI glyphs
  replaced with text or Blender icons.

---

## [0.1.0] -- 2026-06-08

### Added
- **Project creation** — full folder structure on disk from a single dialog
  (assets, shots, sets, delivery, cache, references, resources)
- **`project.json`** — project config with scene presets per production type
- **Production types** — Film (24fps / 2K / Cycles / AgX), AR (60fps / Quest),
  VR (90fps / Stereo)
- **Scene auto-setup** — FPS, units, resolution, renderer, color management
  applied on project creation
- **Collection hierarchy** — `COL_WORLD`, `COL_ENV`, `COL_CHAR`, `COL_FX`,
  `COL_CAM`, `COL_GUIDES` created automatically (COL_GUIDES excluded from render)
- **Asset creation** — per-asset step folders (modeling / uvs / rigging /
  lookdev / fx) with `wip/` and `publish/` sub-directories
- **Asset types** — Prop, Character, Environment sub-classification
- **Shot creation** — layout / animation / lighting / fx / render / composite
- **Set creation** — modeling / lookdev / lighting
- **WIP save** — manual versioned save (`AssetName_step_v001.blend`),
  version suggestion (latest + 1), overwrite warning, Blender title bar update
- **USD Publish** — export current step to USD via Blender native exporter,
  auto-recompose `asset_root.usd` after publish
- **USD composition** — poupée russe (matryoshka) USDA sublayer assembly,
  no `pxr` dependency (pure stdlib)
- **Load project** — restore scene context from existing `project.json`
- **Open folder shortcuts** — WIP and publish folders open in OS file manager
- **N-panel UI** — three panels under `View3D > N-Panel > Ylos`:
  Project, Asset Context, Scene Settings
- **Zero external dependencies** — stdlib only (json, pathlib, os, re)

### Fixed
- n/a (initial release)

### Known Issues
- USD texture export requires manual handling (Blender 4.x removed
  `export_textures` parameter — textures must be packed or path-managed separately)
- Shot root USD composition not yet implemented (assets and sets only)

---

## Roadmap

### [0.2.0] — planned
- Shot root USD assembly (multi-asset layout composition)
- Delivery export targets (USDZ for AR, glTF for VR/Unity)
- Asset browser integration (thumbnail generation per publish)
- Batch publish (all steps at once)

### [0.3.0] — planned
- USD variant support (LOD A/B/C, Day/Night states)
- Payload vs reference mode per asset
- Houdini path compatibility check

### [1.0.0] — planned
- Full production-tested pipeline on a short film project
- Multi-user mode (shared project.json on network drive)
