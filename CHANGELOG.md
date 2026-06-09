# Changelog

All notable changes to Ylos Pipeline are documented here.
Format: [Semantic Versioning](https://semver.org) -- `MAJOR.MINOR.PATCH`

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
