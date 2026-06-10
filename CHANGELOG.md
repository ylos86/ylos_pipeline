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
