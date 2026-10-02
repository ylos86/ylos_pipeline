# Ylos Pipeline — Charte du Project (Claude)

> Ce doc oriente une session Claude fraîche dans ce project. Il ne duplique PAS l'état
> technique détaillé : `CLAUDE.md` à la racine du repo est la source de vérité — à lire
> avant toute intervention (même règle que le workflow Claude Code : "Read CLAUDE.md first
> → cartographier l'état empiriquement → agir → commit"). Ce doc couvre ce que CLAUDE.md ne
> couvre pas : l'orientation rapide et le chantier UI/UX en cours.
>
> Écrit : 2026-08-01, après lecture complète du repo (CLAUDE.md 848 lignes, panel.py,
> app.html, git log). À remettre à jour aux jalons, pas à chaque session.

## Ce project, en une phrase

Reprendre le développement du Ylos Pipeline (pipeline solo Python/Houdini/Blender/web,
inspiré Prism, à l'échelle d'un freelance) après ~2,5 semaines de pause (dernier commit
2026-07-15). Chantier actif : rendre les 3 UI (web, Blender, Houdini) **friendly**, pas
juste fonctionnelles — la fonctionnalité, elle, est déjà largement là.

## État réel du repo (vérifié, pas supposé)

- Repo propre, branche `main` unique, rien en attente de commit — rien à récupérer en
  urgence, on repart sur une base saine.
- Architecture verrouillée (5 principes, cf. CLAUDE.md) : racine relocalisable, cache/source
  séparés (3 tiers), `project.json` = source de vérité machine, gestion de prod ≠ pipeline
  technique, logique unique dans `create_project.py` (importable par tous les DCC).
- Schéma **2.1.0**. Bridge Blender validé e2e (GLB → `sync_web_assets()` → Three.js). Bridge
  Houdini : **workflow shot/Solaris complet et validé e2e** (HDA `ylos::publish::0.2`,
  composeur `refresh_entity_root`, cache/render Karma → delivery) — terminé 2026-07-08.
- **Le plan de parité Prism référencé dans la mémoire du project Pachamama (top menu bar,
  panel refactor, verbes web, Save Version + commentaires, import states, pin API) est en
  réalité déjà livré**, côté Blender et web, sur 5 incréments + un vrai State Manager façon
  Prism (states empilables, un seul bouton Publish) + un panel Import/Export à la demande.
  Tout terminé et testé (commits du 2026-07-14 au 2026-07-15).
- Houdini : bridge + HDA + shelf (Setup File Cache, Render Shot, Deliver Render). **Pas de
  panel/browser dédié** — un "Python Panel / asset browser" a été explicitement envisagé et
  laissé hors scope à chaque revue.

## Les 3 chantiers UI — diagnostic à date, pas supposition

**1. Web (`app.html`, 1447 lignes)**
La base CSS est déjà solide : design tokens (`--bg`, `--accent`, `--border`...), thème
sombre cohérent, cards avec hover/transitions, badges, états vides stylés, banner d'erreur
dédié. Ce n'est pas une UI "brute". Donc "pas encore friendly" pointe probablement vers
l'UX des parcours (nombre de clics, clarté des actions, découvrabilité de pin/sync/versions)
plutôt que le visuel. À confirmer : quel écran ou quel parcours précis frotte ?

**2. Blender**
Le code de `plugins/blender/ui/panel.py` est déjà propre : sections groupées en boxes,
`use_property_split`, icônes contextuelles, séparateurs dosés, section Scene Check
repliable (`DEFAULT_CLOSED`), états d'alerte visuels (unsaved changes, erreurs bloquantes).
Les vraies pistes de friction sont probablement ailleurs : 4 panels empilés + un State
Manager en popup à ouvrir séparément, boutons de step abrégés à 3 lettres (`_abbrev()` →
"Mod", "Sur"...) potentiellement cryptiques, largeur contrainte du N-panel. Hypothèses à
valider, pas un diagnostic fermé.

**3. Houdini**
Pas de polish à faire ici — une vraie décision de scope. Actuellement shelf-only (3 outils).
Un Python Panel/asset browser a été mis de côté à chaque fois. Question à trancher :
construire ce panel maintenant, ou améliorer autre chose (paramètres HDA, retour visuel du
shelf) ?

## Journal de décisions / Questions ouvertes

> Mise à jour 2026-08-15 — session de diagnostic + refonte. Les hypothèses de la section
> précédente ont été **vérifiées empiriquement** (mesures sur `Ylos__Test`, pas suppositions).
> Le détail technique vit dans `CLAUDE.md` ; ici, le statut et ce qu'il reste à trancher.

| Question | Verdict / notes | Statut |
|---|---|---|
| Web UI : quel écran « pas friendly » ? | **Ce n'était pas l'UX, c'était une panne.** Les thumbnails étaient rendus plats (écart-type 0,0016 sur 255) par 3 bugs cumulatifs dans `render_publish_thumbnail`, plus un 4e trou : aucune vignette avant le 1er publish. Corrigé + backfill. | Résolu |
| Blender : friction = densité, largeur, ou abréviations ? | **Les abréviations, confirmé** — et surtout l'absence totale d'images. Steps en toutes lettres (Context + Assets), preview d'entité dans la section Assets, statut de publish par step. La densité (4 panels) n'a PAS été touchée : rien ne prouve encore qu'elle gêne. | Partiellement résolu |
| Houdini UI : Python Panel ou amélioration du shelf ? | Non traité cette session. `resolve_entity_thumbnail` est désormais dans l'orchestrateur : un futur panel Houdini aura les vignettes gratuitement. | Ouvert |
| Ordre d'attaque des 3 fronts | Web + Blender faits ensemble (même cause racine côté données). Houdini reste seul. | Résolu |

### Nouvelles questions ouvertes (2026-08-15)

| Question | Contexte | Statut |
|---|---|---|
| Que faire du publish vide `CHARACTER_Sissa02_Default/modeling/v001` ? | Le GLB ne contient **qu'un EMPTY**, zéro mesh, marqué `complete`. Le garde-fou d'intégrité empêche que ça se reproduise, mais l'entrée existante reste. Republier ? Marquer `broken` dans le manifeste ? | Ouvert |
| Que faire de `sets/lecube` (entité orpheline) ? | Dossier sans manifeste, hors convention de nommage. `save_wip` refuse désormais d'en créer, la web UI la signale. Supprimer, ou la recréer proprement via `+ New` et déplacer le WIP ? | Ouvert |
| `POLL_MS` de la web UI vs coût du scan | `/api/assets` scanne maintenant aussi les `wip/` pour la cascade de vignettes. Négligeable à 7 entités ; à re-mesurer au-delà de ~100. | À surveiller |
| Densité du N-panel (4 sections + popup) | Hypothèse jamais vérifiée. À trancher **après** usage réel de la nouvelle section Assets, pas avant. | Ouvert |

## Mode de collaboration (déjà défini dans CLAUDE.md — référence, pas dupliqué)

Lire avant d'écrire. Planifier avant toute action destructive ou changement de schéma. Ne
jamais casser l'importabilité (pas d'import lourd niveau module dans ce que les DCC
chargent). Challenger l'archi — nommer une implication downstream avant d'exécuter,
réframer une intention mal posée plutôt que de la suivre aveuglément. S'applique aussi dans
ce project Claude.

## Comment utiliser ce project Claude

- `CLAUDE.md` (repo) = vérité technique de la pipeline. Ce doc = orientation + suivi du
  chantier UI en cours — les deux ne se recouvrent pas, ne pas dupliquer l'un dans l'autre.
- Une conversation = un front UI (web / Blender / Houdini) ou une question de scope précise,
  pas un fourre-tout.
- Recherche d'emploi et sujets non liés à la pipeline → un autre project.
- Mettre à jour ce doc quand un chantier UI change de statut, pas à chaque message.

## Mise à jour 2026-10-01 — Blender : N-panel supprimé, modèle Prism (menu + fenêtres)

Retour d'usage de Sébastien : les autres assets étaient visibles dans l'espace de travail, alors
que c'est de l'info de browser. Diagnostic code : 3 points d'entrée pour switcher d'asset
(Switch, liste Assets, Browse), steps et `ylos_context_type` dessinés deux fois, 5 sections empilées.
Hypothèse « densité » du tableau ci-dessus : **confirmée**.

Décision : popups Blender (`invoke_popup`, pas de fenêtre persistante), N-panel supprimé.
`ui/panel.py` et `ui/panel_asset_list.py` retirés ; remplacés par `ui/browser.py` (Project Browser
grille de vignettes), `ui/scenefile.py`, `ui/scene_check.py`, `ui/common.py`. Menu top bar
réordonné (Project Browser… / Scenefile… / State Manager… / Import-Export… / Scene Check…).
Header 3D : pill `asset - step` = lanceur du Project Browser.

**Non vérifié** : rendu visuel en live Blender 5.2 (pont MCP muet pendant la session) et
`tools/blender/test_panel_draw_headless.py` adapté mais non exécuté. `ylos_asset_type` n'est plus
éditable à la main (posé par New Asset / Create Scene) — à confirmer que ça ne manque pas.
Ouvert : modèle de browser pur partagé avec Houdini (`ylos_browser_model.py`) non évalué.

### Suite 2026-10-01 (retour d'usage sur le Project Browser)

- **Sélectionner ≠ ouvrir.** Le clic sur le nom ne faisait que `ylos_current_asset = …` : « rien ne se
  passe ». Ajout de `ylos.open_entity` (`operators/op_open_entity.py`) : icône Open sur chaque carte
  (WIP le plus récent du step courant ou du 1er step déclaré) et sur chaque step de l'entité active.
  Le fichier à ouvrir vient de `create_project.resolve_open_target` (même résolveur que le web) ;
  seul le cas `.blend` WIP est ouvert tel quel, confirmation si le fichier courant est modifié. Sans
  WIP : contexte basculé + message vers Scenefile > New Scene (pas d'import deviné).
- **Fenêtre déplaçable.** Un popup Blender ne se déplace pas : `ylos.browser_window` ouvre une vraie
  fenêtre (`wm.window_new`) dont l'area est un éditeur Properties (onglet Scene) ; le panel
  `YLOS_PT_BrowserWindow` n'y apparaît que sur l'écran nommé `YLOS_ProjectBrowser`. Repli automatique
  sur le popup (renommé « Quick Switch ») si la création échoue.
- **Non vérifié en live (pont Blender muet)** : (1) l'onglet Scene de cette fenêtre risque d'afficher
  les panels natifs de la scène sous le browser ; (2) `load_ui=False` à l'ouverture d'un fichier garde-t-il
  la fenêtre ? ; (3) la fenêtre est sauvegardée dans le .blend. À trancher à l'usage.
- **Open = choix de version** (2026-10-01, soir). `ylos.open_entity` ouvre un popup : gros bouton
  « Open Latest — vNNN » puis grille des versions antérieures (vignette, date, commentaire, 12 max).
  Pas de WIP → bascule de contexte + message. Fichier modifié → bandeau d'alerte (l'ouverture jette
  le non-sauvegardé). Fenêtre browser : colonne d'onglets Properties et header masqués via
  `show_region_navigation_bar` / `show_region_header` (gardés par `hasattr`, **non vérifiés en live** :
  fermer et rouvrir la fenêtre pour les appliquer).
- **Incident live 2026-10-01** : `ylos.reload_pipeline` lancé avec la fenêtre Project Browser ouverte
  a coupé la connexion du MCP Blender (très probablement un crash : le panel hôte est dé-enregistré
  pendant qu'il est dessiné — même famille que le crash des export states). Garde ajoutée : reload refusé
  tant que la fenêtre est ouverte. Vérifié en live avant l'incident : `SpaceProperties` n'a PAS
  `show_region_navigation_bar` en 5.2 ; la colonne d'onglets se ferme avec
  `screen.region_toggle(region_type='NAVIGATION_BAR')` (29 px → 1 px, testé). Le code de
  `ylos.browser_window` utilise désormais cet appel.
- **Vérifié en live le 2026-10-01 (Blender 5.2, MCP)** : fenêtre Project Browser OK (grille de
  vignettes, icône Open par carte, colonne d'onglets repliée par `region_toggle`, header masqué) ;
  ouverture d'un WIP depuis le browser OK et **la fenêtre survit** (`load_ui=False`) ; popup de versions OK
  (Open Latest v005 + grille). Dates du popup normalisées (`_fmt_date`, ex. « 16 Jul 16:07 ») — correctif
  non revu en live.
- **Deuxième crash au reload (même soirée), fenêtre browser FERMÉE** : l'hypothèse « panel hôte affiché »
  ne suffit donc pas. `ylos.reload_pipeline` déclenché depuis le MCP a fermé Blender deux fois ; cause
  non identifiée (pas de crash log lu). Tant que ce n'est pas élucidé : **redémarrer Blender pour charger
  le code**, ne pas utiliser Reload Pipeline. Piste : lire `blender.crash.txt` (dossier temp) après le prochain cas.

## 2026-10-01 — Publish scopé à l'asset actif + check pré-publish (re-import)
- **Vocabulaire** : « Export » du State Manager devient **Publish**. La section est verrouillée sur l'asset actif
  (plus de sélecteur d'entité) ; Import Product liste par défaut les seuls products de l'asset actif
  (toggle « All assets » = `ylos_io_all_entities`, recherche visible seulement dans ce mode). Le bloc brut
  « Export Selection (raw file) » reste, renvoyant vers Publish.
- **Panneaux natifs Scene masqués** (wrap des `poll`, restaurés à l'unregister) — non revu visuellement
  hors poll ; deux lignes dessinées en C subsistent.
- **Check pré-publish** : `core/publish_check.py` (`pre_publish_check`, `fingerprint`, `reimport_fingerprint`,
  `verify_artifact`) + règle pure `create_project.validate_publish_roundtrip` (tests stdlib
  `tests/test_publish_roundtrip.py`). Avant `allocate_publish_version` : refus si mesh absent/vide,
  texture manquante (ERROR) ; après export : ré-import dans une collection jetable, comparaison
  fingerprint (meshes, tris, extents triés, UV, matériaux, noms) ; échec = rien commité, staging conservé.
  `ylos.check_publish` = dry run (export en temp, aucune version allouée), résultats cachés pour l'UI.
- **Tests** : `tools/blender/test_publish_roundtrip_headless.py` (GLB + USD, exporteur avec perte simulé,
  30 checks) ; `test_panel_draw_headless.py` couvre check_publish + Import scopé. Suite stdlib : 380 OK.
- **Limites** : ne vérifie pas shading/normales/rig ; la perte d'exporteur est simulée par monkeypatch.
  `ylos.check_publish` lève une erreur bpy (RuntimeError) quand le dry run trouve des erreurs bloquantes (contrat voulu).

## 2026-10-02 — Web : refonte « direction C » (maquettes) et schéma 2.3

Maquettes sur les données réelles de `Ylos__Test` (canvas Claude « Ylos Prod Home Mockup ») :
accueil en matrice entités × steps avec panneau d'aperçu, page entité, page shot (données
d'exemple), langage de statut, contrat d'écran (chaque écran : sa question, ses données, ses actions).

| Décision | Statut |
|---|---|
| Une approbation porte sa version | Implémenté : schéma 2.3 (`docs/migration-2.2-to-2.3.md`), branche `feat/schema-2.3-step-status` |
| Une cellule de step montre le rendu de SA version ; l'image d'entité peut rester un preview, étiqueté et capturé sans overlays | Implémenté côté web : `entity_overview` apparie version et rendu, la cellule et l'aperçu affichent la paire ; l'image d'entité porte son étiquette (« Publish render », « Preview · custom », « Legacy publish », « WIP thumbnail ») |
| Direction C = base de la refonte web | Implémenté : `app.html` (branche `feat/ui-direction-c`, PR #3) ; l'ancien cockpit reste servi à `/classic` |
| Image d'entité = pointeur dans le manifeste vers un rendu de publish, plutôt qu'un `preview.png` copié | Proposé |

Incréments :
1. ~~Accueil en matrice et `behind` côté web~~ — fait (2026-10-02, PR #3). Reste `behind` et la
   vue d'ensemble dans les browsers Blender et Houdini, depuis le même `entity_overview`.
2. Signaux d'intégrité : ~~réservations abandonnées~~ — fait (`abandoned_reservations`, lecture
   seule, partage `_pending_reservations` avec `clean_stale_staging`). Reste : rendu de publish
   vide (le v001 de Sissa02), réservations LOP dans l'aperçu.
3. Page shot : ordre de composition de `shot_root`, playblasts, rendus du cache, livraisons ;
   `deliver_render` à déplacer du bridge Houdini vers `create_project.py` (il est pur : shutil, sans `hou`).
   Pas commencé : l'accueil des shots montre un état vide avec « New shot ».
4. Pré-production (References, Boards) : entrées « planned » dans la navigation, rien derrière.

Ouvert : nommage des shots (`TYPE_Name_Variant` fige un département) ; `comp` vs `composite` ;
commentaire de publish vide en dur dans `op_publish.execute`.
