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
