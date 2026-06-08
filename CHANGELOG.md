# Changelog

All notable changes to Ylos Pipeline are documented here.
Format: [Semantic Versioning](https://semver.org) — `MAJOR.MINOR.PATCH`

---

## [0.1.0] — 2026-06-08

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
