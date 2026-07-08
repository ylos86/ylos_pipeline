---
description: Implémente le plan Houdini shots/Solaris (docs/plan-houdini-shots.md), un incrément à la fois
argument-hint: [numéro d'incrément 0-7]
---

Lis intégralement docs/plan-houdini-shots.md et CLAUDE.md avant toute action.

Implémente UNIQUEMENT l'incrément $ARGUMENTS du plan. Si aucun numéro n'est fourni :
détermine le premier incrément non fait en inspectant l'état RÉEL du repo (git log,
fichiers, tests — jamais de supposition), et annonce-le avant de commencer.

Règles non négociables :
- Un incrément = tests + un commit. Ne jamais entamer le suivant dans la même passe.
- Le critère « Done » de l'incrément doit être vérifié avant le commit.
- « Hors scope explicite » et « Points de vigilance » s'appliquent intégralement —
  ne rien améliorer en passant.
- python3 -m unittest doit passer SANS Houdini installé avant tout commit (contrainte CI).
- create_project.py et ylos_houdini.py restent stdlib pure, sans import hou top-level.
- Contradiction entre le plan et l'état réel du code : arrête-toi et nomme-la,
  ne la contourne pas.
