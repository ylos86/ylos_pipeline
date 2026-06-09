# Ylos Pipeline

**Blender 4.2 LTS -- Production Pipeline Addon**

A USD-first pipeline manager for solo short film, AR, and VR productions.
Handles project creation, asset management, versioned WIP saves,
USD publish, and matryoshka stage composition -- directly from the Blender N-panel.

---

## Features

- **One-click project creation** -- full folder structure, `project.json`, scene configured
- **Production presets** -- Film / AR / VR (FPS, resolution, renderer, color management)
- **Asset pipeline** -- Prop, Character, Environment with per-step wip/publish folders
- **Versioned WIP saves** -- manual version control, no auto-increment surprises
- **USD publish** -- export step -> USD, auto-rebuild `asset_root.usd` sublayer stack
- **Matryoshka composition** -- lookdev > rigging > modeling, strongest opinion on top
- **Zero external dependencies** -- stdlib Python only, works out of the box in Blender

---

## Requirements

- Blender **4.2 LTS** or later
- No external Python packages required

---

## Installation

### Option A -- Install from zip (recommended)

1. Download the latest release zip from the
   [Releases](../../releases) page
2. In Blender: `Edit > Preferences > Add-ons > Install`
3. Select the zip -> Enable **Ylos Pipeline**
4. The `Ylos` tab appears in the `View3D > N-Panel`

### Option B -- Clone directly into Blender addons folder

```bash
# Find your Blender addons folder:
# macOS:  ~/Library/Application Support/Blender/4.2/scripts/addons/
# Linux:  ~/.config/blender/4.2/scripts/addons/
# Windows: %APPDATA%\Blender Foundation\Blender\4.2\scripts\addons\

cd /path/to/blender/addons/
git clone https://github.com/YOUR_USERNAME/ylos_pipeline.git
```

Then enable in `Edit > Preferences > Add-ons` -> search "Ylos Pipeline".

---

## Project Structure

A project created by Ylos Pipeline looks like this:

```
YLOS_ProjectName/
├── _pipeline/
│   └── project.json          <- scene settings, step config, delivery targets
├── assets/
│   └── AssetName/
│       ├── modeling/
│       │   ├── wip/          <- .blend files (AssetName_modeling_v001.blend)
│       │   └── publish/      <- .usd files  (AssetName_modeling_v001.usd)
│       ├── rigging/
│       ├── lookdev/
│       ├── fx/
│       └── asset_root.usd    <- auto-assembled sublayer stack
├── shots/
│   └── SQ010_SH0010/
│       ├── layout/
│       ├── animation/
│       ├── lighting/
│       ├── fx/
│       ├── render/
│       └── composite/
├── sets/
│   └── SetName/
│       ├── modeling/
│       ├── lookdev/
│       ├── lighting/
│       └── set_root.usd
├── delivery/
│   ├── film/                 <- USD + EXR -> Unreal
│   ├── ar/                   <- USDZ -> iOS / glTF -> Android
│   └── vr/                   <- USD -> Unreal / glTF -> Unity
├── cache/
│   ├── alembic/
│   └── simulations/
├── references/
└── resources/
    ├── hdri/
    └── textures/
```

---

## USD Matryoshka (Poupee Russe)

Each asset's `asset_root.usd` is automatically rebuilt on every publish.
Layers stack weakest -> strongest opinion:

```
asset_root.usd
  subLayers = [
    modeling/publish/AssetName_modeling_v004.usd,   <- base geometry
    rigging/publish/AssetName_rigging_v003.usd,
    lookdev/publish/AssetName_lookdev_v005.usd,     <- strongest opinion
  ]
```

No `pxr` library required -- USDA files are written as plain text.
For advanced stage edits (variants, payloads), use an external Python
environment with `usd-core` installed.

---

## Naming Conventions

Based on Black Kite studio conventions, adapted for Ylos Prod.

| Type | Format | Example |
|---|---|---|
| Project folder | `YLOS_ProjectName` | `YLOS_ColonialHouse` |
| WIP file | `AssetName_step_vNNN.blend` | `HeroCharacter_modeling_v003.blend` |
| Publish file | `AssetName_step_vNNN.usd` | `HeroCharacter_lookdev_v001.usd` |
| Shot | `SQ###_SH####` | `SQ010_SH0030` |
| Blender scene | `SCENE_AssetName_step` | `SCENE_HeroCharacter_modeling` |
| Collections | `COL_TYPE_Name` | `COL_ENV_Vegetation` |
| Geometry | `GEO_Name_Variant` | `GEO_RockFormation_A` |
| Material | `MAT_Name_Variant` | `MAT_GroundDirt_Wet` |

---

## Workflow

```
1. N-Panel > Ylos > New Project
   -> choose name, root path, production type
   -> project folder created, scene configured

2. N-Panel > Ylos > New Asset / Shot / Set
   -> choose type (Prop / Character / Environment / Shot / Set)
   -> step folders created on disk

3. Work on the asset in Blender

4. Save WIP
   -> choose version number (suggested: latest + 1)
   -> saves AssetName_step_vNNN.blend
   -> Blender title bar updates to reflect the file

5. Publish Step
   -> exports USD to publish/
   -> asset_root.usd rebuilt automatically
   -> ready for Houdini / Unreal / Unity consumption
```

---

## Roadmap

See [CHANGELOG.md](CHANGELOG.md) for version history and planned features.

---

## License

MIT -- free to use, modify, and distribute.
Credit appreciated but not required.
