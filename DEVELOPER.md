# Ylos Pipeline -- Developer Guide

Architecture and contribution notes for anyone picking up the codebase.
For user-facing docs see `README.md`; for version history see `CHANGELOG.md`.

Target: **Blender 4.2 LTS** (also runs on 5.x). Pure stdlib, no `pxr` in Blender.

---

## 1. Layout

```
ylos_pipeline/
├── __init__.py            Addon entry: bl_info, class registry, header button,
│                          register()/unregister(), scene-prop wiring.
├── core/                  Pure logic. NO bpy.ops. Unit-testable in isolation.
│   ├── project.py         Project creation, project.json I/O, scene presets,
│   │                      scene properties, step-vs-context validation.
│   ├── asset.py           Entity (asset/shot/set) folders, version detection,
│   │                      path resolution, entity listing + cache.
│   ├── usd_composer.py    Plain-text USDA writers/readers (sublayers + variants).
│   ├── scene_checker.py   Naming/readiness checks, auto-fixes, publish-set resolver.
│   └── thumbnails.py      Viewport thumbnail render + preview-collection icons.
├── operators/             bpy.types.Operator subclasses (one concern per file).
└── ui/                    bpy.types.Panel subclasses (N-panel) + the header popup
                           lives in operators/op_popup.py.
```

**Layering rule:** `ui` and `operators` may import from `core`; `core` never
imports from `ui`/`operators`. `core` modules avoid `bpy.ops` entirely and keep
`bpy` use to data access, so they can be exercised under a mock `bpy`
(see section 6). Keep it that way -- it is the only reason this code is testable.

---

## 2. State model

All session state lives on `bpy.types.Scene` as `ylos_*` properties, registered
in `core/project.py::register_properties()` and torn down in
`unregister_properties()`. There is no PropertyGroup; the operators read/write
scene props directly.

| Property | Type | Meaning |
|---|---|---|
| `ylos_project_path` | str (`subtype="NONE"`) | Absolute project root. |
| `ylos_project_name` | str | Display name. |
| `ylos_prod_type` | enum FILM/AR/VR | Drives scene presets + delivery. |
| `ylos_current_asset` | str | Active entity name. |
| `ylos_current_step` | enum (9 steps) | Active step -- see the gotcha below. |
| `ylos_context_type` | enum ASSET/SHOT/SET | Which folder family is active. |
| `ylos_asset_type` | enum PROP/CHARACTER/ENVIRONMENT | Asset sub-type. |
| `ylos_popup_tab` | enum PIPELINE/ASSETS/SCENE | Registered in `__init__.py`. |

> `ylos_popup_tab` is registered in `__init__.register()` rather than in
> `project.py` -- if you move property registration around, keep that in mind.

### The step enum is intentionally global

`ylos_current_step` lists all 9 steps across asset/shot/set for UI convenience.
That means an asset can have `composite` selected even though assets only have
`modeling/rigging/lookdev/fx`. The chosen design is **global enum + validate at
the boundary**, not a context-filtered enum. The guard is
`project.is_step_valid_for_context(step, context_type)`, called in
`op_publish.execute`. If you add a write path that resolves a step to a folder
(new save/export op), call that guard first.

---

## 3. Data flow: the two things that actually matter

### 3.1 WIP save (`op_save_wip`)

```
invoke: suggest version = get_latest_wip_version(...) + 1
execute:
  resolve_wip_save_path()  -> {root}/{step}/wip/{Asset}_{step}_vNNN.blend
  bpy.ops.wm.save_as_mainfile(copy=False)
  generate_thumbnail()     -> {same stem}_thumb.png  (render settings saved/restored)
  scene.ylos_current_step = step ; scene.name = SCENE_{asset}_{step}
```

Version detection (`asset.list_wip_versions`) is anchored to `.blend`
(`_v(\d{3})\.blend$`) and filters by extension, so autosaves (`.blend1`,
`.blend2`) and thumbnails never count as versions. Keep that anchor if you touch
the regex.

### 3.2 Publish (`op_publish`) -- the dangerous path, read before editing

```
execute:
  is_step_valid_for_context(step, ctx)            # guard
  resolve_publish_path()                          # {Asset}_{step}_vNNN[__Variant].usd
  _usd_export(filepath, asset_name, step, allow_full_scene)
  [optional] bpy.ops.wm.usd_import(load_after)
  [optional] compose_asset_root / compose_set_root (update_root)
```

`_usd_export` resolves the asset's objects via
`scene_checker.get_asset_objects_for_publish` (collection named after the asset,
else whole-field `PREFIX_AssetName` match), selects only those, exports with
`selected_objects_only=True`, then restores the prior selection in a `finally`.

**Hard rule, do not regress:** if an asset was targeted but no objects resolve,
the function returns `(False, ...)` -- it does **not** fall back to a full-scene
export. A silent full-scene fallback was the worst bug in 0.2.3: it published
the entire scene under the asset's name. Full-scene export is opt-in only via
the `allow_full_scene` flag (UI: "Allow Full-Scene Export").

---

## 4. USD composition (`usd_composer.py`)

No `pxr` in Blender's Python, so root files are written as plain-text USDA and
the subset we write is round-trip readable.

- **Unified convention:** `defaultPrim = "ROOT"` everywhere; the entity prim is
  `/ROOT/{EntityName}`. Per-step publishes compose as `subLayers`
  (weakest first in the file list, reversed on write so the strongest opinion is
  last in USD -- lookdev > rigging > modeling).
- **Variants:** when a step has >1 variant for its latest version,
  `write_usda_with_variants` emits a `variantSet "{step}Variant"` on the entity
  prim, each variant carrying a `references` arc to its publish. Syntax is
  canonical USDA and has been validated against `usd-core` (opens, lists
  variants, switches, resolves references).
- **Readers:** `read_root_sublayers()` and `read_root_variants()` parse back the
  exact subset we write. These are not general USD parsers -- if you change the
  writer format, change the readers in lockstep.

For anything heavier (payloads, deep edits) the intended path is an **external**
Python env with `usd-core`, not bundling `pxr` into Blender. See the
`blender-ta` skill notes on why pip-into-Blender-python is discouraged.

---

## 5. Operators and their bl_idnames

| File | Operator | `bl_idname` |
|---|---|---|
| op_new_project | NewProject | `ylos.new_project` |
| op_new_asset | NewAsset | `ylos.new_asset` |
| op_save_wip | SaveWip | `ylos.save_wip` |
| op_publish | Publish | `ylos.publish` |
| op_open_context | OpenContext / OpenFolder | `ylos.open_context` / `ylos.open_folder` |
| op_open_wip | OpenWipVersion / OpenWip / OpenLatestWip | `ylos.open_wip_version` / `ylos.open_wip` / `ylos.open_latest_wip` |
| op_switch_context | SwitchAsset / SwitchStep | `ylos.switch_asset_confirm` / `ylos.switch_step_confirm` |
| op_load_publish | LoadPublishFile / LoadLatestPublish / LoadPublish | `ylos.load_publish_file` / `ylos.load_latest_publish` / `ylos.load_publish` |
| op_asset_list | AssetBrowser / RefreshAssetList | `ylos.asset_browser` / `ylos.refresh_asset_list` |
| op_scene_check | RunSceneCheck / AutoFix / FixAll | `ylos.run_scene_check` / `ylos.auto_fix` / `ylos.fix_all` |
| op_popup | OpenPopup | `ylos.open_popup` |

The asset-switch action is `ylos.switch_asset_confirm` (with an unsaved-changes
dialog). There used to be a second `ylos.switch_asset`; it was removed. Use the
confirm variant or the searchable `ylos.asset_browser`.

The class registry is the `_classes` tuple in `__init__.py`. Add new operators
there and to the relevant `operators/__init__.py` import block.

---

## 6. Testing without Blender

`core` is import-safe under a mock `bpy` because it avoids `bpy.ops`. A minimal
mock that loads the whole addon and dry-runs register/unregister:

```python
# Build a fake 'bpy' package with bpy.props / bpy.types / bpy.utils(.previews),
# register_class that raises on duplicates, then:
spec = importlib.util.spec_from_file_location(
    "ylos_pipeline", ADDON/"__init__.py",
    submodule_search_locations=[str(ADDON)])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.register(); mod.unregister()   # asserts no duplicate bl_idname / class name
```

USDA output is validated for real with `usd-core` (pip-installable outside
Blender): open each generated root, assert `defaultPrim == /ROOT`, and that
variant sets list + switch + resolve references. This is the check you cannot do
inside Blender, so do it here before shipping a composer change.

Pure-logic checks worth keeping green:
- `is_step_valid_for_context` rejects `composite` for `asset`.
- `list_wip_versions` ignores `.blend1` / `_thumb.png`.
- `scene_checker._name_matches_asset` rejects `GEO_HeroSword` for asset `Hero`.
- `check_collection_membership` returns 0 issues when all objects are inside.

---

## 7. Conventions for contributors

- **ASCII only** in Python comments and strings (Windows compat). UI symbols go
  through Blender icons, not Unicode glyphs. CI-grep: `grep -rnP '[^\x00-\x7F]'`.
- **utf-8 declaration** on line 1 of every `.py`.
- When an operator gains multiple properties, **rewrite the whole file** rather
  than string-patching annotations -- partial edits have caused class-level
  annotation failures before.
- After any addon change in Blender: aggressive `.pyc` caching means you must
  fully remove the installed addon (`rm -rf .../addons/ylos_pipeline`) and
  restart Blender before reinstalling. Disable/re-enable is not enough.
- Blender's USD export raises `RuntimeError` (not `TypeError`) for unknown
  kwargs -- catch broadly and degrade, never assume the kwarg exists.

---

## 8. Known limitations / next work

- Shot root USD assembly is not implemented (assets + sets only); publishing a
  shot step exports USD but writes no shot_root.
- `compose_*` always uses the latest version per step; no pinning to a specific
  published version yet.
- Texture export on USD publish is not path-managed; pack or manage separately.
- `get_asset_step_status` hits disk per active row; fine at small scale, would
  need batching for very large projects (the entity list is already TTL-cached
  in `asset.py`).
- No delivery-target export (USDZ/glTF) yet despite the `delivery/` folders.
