# Plugin Houdini — Ylos Prod

Couche **adaptateur uniquement**. Aucune logique métier ici : tout passe par
`create_project.py` (source de vérité unique, principe 5 de `CLAUDE.md`). Le plugin
expose **deux points d'entrée qui coexistent** (décision verrouillée,
`docs/plan-usable-v1.md` Phase 4.1) : le **shelf** (gestes rapides) et le **Python Panel**
(cockpit). Le panel ne remplace pas le shelf.

## Carte des fichiers

| Fichier | Rôle | `hou` ? | Qt ? |
|---|---|---|---|
| `ylos.json` | Package Houdini : pose `YLOS_REPO`, `HOUDINI_PATH`, `HOUDINI_OTLSCAN_PATH`, `PYTHONPATH`, `HOUDINI_TOOLBAR_PATH` | — | — |
| `python/ylos_houdini.py` | **Bridge**. Fonctions pures (contexte, versions, chemins, `starter_plan`, `stage_layer_paths`, `dependencies_from_paths`) + actions `hou` (`create_scene`, `save_wip`, sublayer/reference, render, deliver) + outils shelf `tool_*()` | import **paresseux** dans les fonctions | non |
| `python/ylos_browser_model.py` | **Modèle de vue pur** du cockpit : toutes les lignes affichées viennent de l'orchestrateur. Détient aussi le vocabulaire d'affichage (libellés de famille, couleurs de statut) — équivalent Houdini de `plugins/blender/core/vocab.py` | **jamais** | **jamais** |
| `python/ylos_browser_panel.py` | **Vue Qt** du cockpit (`hutil.Qt`, PySide6 sur H21). Ne dérive aucune donnée : lit le modèle, agit via le bridge | paresseux (`_hou()`) | oui |
| `python_panels/ylos_browser.pypanel` | Déclaration du Python Panel (`interface name="ylos_browser"`), trouvée via `HOUDINI_PATH` | — | — |
| `toolbar/ylos_pipeline.shelf` | Shelf « Ylos » : 11 outils, chacun délègue à un `ylos_houdini.tool_*()` | — | — |
| `otls/ylos_publish.hdanc` | HDA `ylos::publish::0.2`. **Binaire régénéré, jamais édité en GUI** — la source est `tools/houdini/build_publish_hda.py` | — | — |

Ce découpage en trois (modèle pur / vue Qt / bridge `hou`) n'est pas cosmétique : il rend
le cockpit **testable sans licence Houdini** (`tests/test_houdini_browser_model.py`,
`tests/test_houdini_browser_panel.py`), ce qui est la seule façon de le valider en CI.

## Installation (package = versionné ET installé, sans copie)

```bash
mkdir -p "$HOME/Library/Preferences/houdini/21.0/packages"
ln -sf "$HOME/Desktop/Claude/YlosPipeline/plugins/houdini/ylos.json" \
       "$HOME/Library/Preferences/houdini/21.0/packages/ylos.json"
```

`YLOS_REPO` est défini dans `ylos.json` — l'ajuster si le repo n'est pas sous
`$HOME/Desktop/Claude/YlosPipeline`. Redémarrer Houdini après toute modification du
package (les variables d'environnement ne sont lues qu'au démarrage).

## Utilisation

- **Shelf « Ylos »** : Project Browser · New Scene · Save Version · Set Status ·
  New Asset · Load Asset · Load Shot · Load Step Publish · Setup File Cache ·
  Render Shot · Deliver Render.
- **Cockpit** : bouton *Project Browser* du shelf, ou n'importe quel pane →
  `New Pane Tab Type ▸ New Pane Tab ▸ Ylos Browser`.
  Projet actif (+ changement de projet) · entités par famille avec vignettes et recherche ·
  steps avec statut coloré et combo de statut · scenefiles (tous DCC, *Open* réservé aux
  `.hip*`) · products (*Sublayer* / *Reference into /stage*) · dépendances
  (« Used in » / « Uses », marqueur `UPDATE AVAILABLE`) · New Scene / Save Version / Publish.

Le **projet actif** est `~/.ylos/active_project` — même fichier que l'UI web et Blender.
« Change project » l'écrit exactement comme `ylos_ui._write_active`, et **refuse** un
dossier sans `_pipeline/project.json`.

## Régénérer le HDA (obligatoire après toute modification de son build)

```bash
hython tools/houdini/build_publish_hda.py
```

Jamais d'édition GUI : le `.hdanc` est un artefact, `build_publish_hda.py` est la source.

## Tests

```bash
# Logique pure, sans Houdini ni licence (ce que vérifie la CI) :
python3 -m unittest tests/test_ylos_houdini.py tests/test_houdini_browser_model.py \
                    tests/test_houdini_browser_panel.py -v

# Garde de syntaxe pour tout ce qui ne peut pas être exécuté ici (pas de licence) :
PY=/Applications/Houdini/Houdini21.0.631/Frameworks/Python.framework/Versions/3.11/bin/python3.11
$PY -m py_compile plugins/houdini/python/*.py tools/houdini/*.py

# e2e réel (hython, hors CI, exige une licence) :
hython tools/houdini/test_shot_workflow_e2e.py
```

## Limites connues

- **Apprentice** : extensions `.hipnc` / `.usdnc` / `.hdanc`. Un publish `.usdnc` est
  **chiffré** → le scan de layers USD ASCII de `build_dependency_index()` ne peut pas le
  lire. Côté Houdini, les dépendances enregistrées au manifeste par le publish
  (`dependencies`, schéma 2.2) sont donc la **seule** source fiable — c'est la raison
  d'être de `dependencies_from_paths()`.
- Le panel ne crée pas d'entité (ça reste *New Asset* au shelf) et ne déclenche pas le
  publish lui-même : il pose le nœud `ylos::publish` pré-rempli, l'artiste vérifie le
  stage puis presse *Publish* (le contrat deux-phases reste un geste humain).
