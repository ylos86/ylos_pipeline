# CLAUDE.md — Pipeline Black Kite

## Contexte
Pipeline de production 3D/VFX freelance (marque **Black Kite**). Objectif : un système
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
`create_project.py` (stdlib) existe et est validé. Il :
- génère l'arborescence complète
- écrit `project.json` (manifeste, source de vérité)
- crée `.metadata_never_index` (supprime l'indexation Spotlight)
- génère un `.gitignore` (cache + renders)
- est importable par les plugins DCC

## Questions de design ouvertes — à traiter, pas à présupposer
1. **Topologie du workflow** : travail centré **asset**, centré **shot**, ou les deux ?
   Détermine s'il faut une **bibliothèque d'assets transverse**, à un niveau au-dessus des
   projets individuels. (Pour un solo freelance, interroger d'abord le YAGNI : reuse
   inter-projets assez fréquent pour justifier une lib first-class dès maintenant, ou un
   simple chemin « publier vers la lib » suffit ?)
2. **Placement du cache** : cache interne **centralisé** vs cache **par projet** sur le
   disque externe.

Tant que ces deux points ne sont pas tranchés, ne fige aucune décision qui en dépend.

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
