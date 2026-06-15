# Convention USD — Pipeline Black Kite

> **Statut** : figée (2026-06-15). S'applique à la génération des `asset_root` et des
> publishes par `create_project.py` (Incrément 2) et par les plugins DCC.

Résout les incohérences observées dans les projets réels (`Lina` en `references`/`.usda`/
`</root>` vs `lecube` en `subLayers`/`.usd`/`defaultPrim`).

## 1. Composition

### Intra-asset : `subLayers` (asset stack)
Les étapes d'un même asset sont des **opinions sur les mêmes prims**. `asset_root.usda`
les empile en `subLayers`, **du plus fort au plus faible = étape la plus downstream en
premier** (USD : le premier sublayer de la liste est le plus fort).

Ordre de force (haut = fort) : `fx` › `lookdev` › `rigging` › `uvs` › `modeling`.
Ainsi lookdev peut surcharger modeling. **Tous les publishes d'un asset authorent sous
`/<NomAsset>`** (sinon les opinions ne s'empilent pas).

### Asset → set / shot : `references` (ou `payload`)
Un set/shot **référence** l'`asset_root.usda` de l'asset (pas ses publishes). Pas de chemin
de prim explicite : on s'appuie sur le `defaultPrim`. Utiliser un **`payload`** pour les
assets lourds (déchargeables). Cet axe est distinct de la composition intra-asset.

## 2. Extensions

| Usage | Extension | Raison |
|-------|-----------|--------|
| Composition / root / assemblage (`asset_root`, stages set/shot, layout) | **`.usda`** | ASCII lisible, diffable en git, éditable à la main |
| Géométrie / publishes lourds (modeling, rigging, caches) | **`.usdc`** | Crate binaire, rapide à charger |
| Lookdev (réseaux de matériaux légers) | `.usda` toléré | Lisible ; bascule en `.usdc` si volumineux |

**`.usd` nu est banni** : il est ambigu (ASCII ou binaire selon l'écriture).

## 3. Prim racine / `defaultPrim`

- **Chaque asset** : `defaultPrim = "<NomAsset>"`, prim racine `/<NomAsset>`. Référencé dans
  un set, il atterrit nommé (`Lina`).
- **Stages d'assemblage** (sets, shots) : prim racine **`/ROOT`** = la valeur de
  `pipeline.usd_root_prim` dans `project.json`. `/ROOT` ne s'applique **pas** aux assets
  individuels.
- Le `</root>` minuscule de `Lina/asset_root.usd` est une erreur → corrigé par cette règle
  (on ne cible plus un prim explicite ; on s'appuie sur le `defaultPrim` de l'asset).

## 4. Versioning des publishes

- Chemin : `<step>/publish/<entity>_<step>_v###.usdc` (padding **3 chiffres**, ex `v002`).
- `asset_root.usda` pointe une **version figée** (pin) ; la mise à jour de pin se fait au
  publish, pas par « latest » implicite.

## 5. `asset_root.usda` canonique

```usda
#usda 1.0
(
    defaultPrim = "Lina"
    upAxis = "Y"
    metersPerUnit = 1
    subLayers = [
        @lookdev/publish/Lina_lookdev_v001.usda@,
        @rigging/publish/Lina_rigging_v001.usdc@,
        @modeling/publish/Lina_modeling_v002.usdc@
    ]
)
def Xform "Lina"
{
}
```

## À vérifier à l'Incrément 2 (pas un blocage de convention)

- **Up axis** : les fichiers réels déclarent `upAxis = "Y"` alors que le renderer est
  `CYCLES` (Blender natif Z-up). Décider l'axe d'échange USD et la cohérence avec Blender
  (export Y-up vs conservation Z-up). `metersPerUnit` doit s'aligner sur `scene.unit_scale`.
