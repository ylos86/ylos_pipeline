# Plugins Blender — Ylos Prod

Couche **UI uniquement**. Ces fichiers n'embarquent aucune logique : ils importent
`create_project.py` et `migrate_to_2.0.py` du repo (source de vérité unique) et appellent
leurs fonctions. Principe « logique unique, jamais dupliquée ».

| Fichier | Rôle | Va dans |
|---------|------|---------|
| `ylos_pipeline.py` | Bootstrap : ajoute le repo au `sys.path` de Blender (`import create_project` marche) | `scripts/startup/` |
| `ylos_pipeline_ui.py` | Addon : panneau N-sidebar « Ylos Prod » (créer projet/asset, convertir) | `scripts/addons/` |

## Installation (lien symbolique = versionné ET installé, sans copie)

Remplacer `5.1` par ta version de Blender. Sur macOS :

```bash
REPO="$HOME/Desktop/Claude/YlosPipeline"
BL="$HOME/Library/Application Support/Blender/5.1/scripts"

ln -sf "$REPO/plugins/blender/ylos_pipeline.py"    "$BL/startup/ylos_pipeline.py"
ln -sf "$REPO/plugins/blender/ylos_pipeline_ui.py" "$BL/addons/ylos_pipeline_ui.py"
```

Puis dans Blender : Preferences > Add-ons > activer « Ylos Prod Pipeline ».
Le panneau : vue 3D > touche `N` > onglet **Ylos Prod**.

> Comme c'est un lien symbolique, éditer le fichier dans le repo met à jour Blender
> directement. Désinstaller = supprimer les liens.
