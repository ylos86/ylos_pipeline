# -*- coding: utf-8 -*-
# ylos_core/locking.py
# Atomic write helpers for the two categories of mutable files in the pipeline:
#   - entity roots  (asset_root.usd, set_root.usd)
#   - project.json
#   - publish sidecars (.manifest.json)
#
# Pattern: write to {path}.tmp then os.replace() -- atomic on POSIX and
# Windows (NTFS). Last-write-wins is explicitly assumed for solo use.
# Multi-user locking is out of scope for v0.4 (see DEVELOPER.md).

import json
import os


def atomic_write_text(path: str, content: str, encoding: str = "utf-8") -> None:
    """
    Write *content* to *path* atomically.
    Steps: write to path + '.tmp', then os.replace() (atomic rename).
    Raises OSError on failure.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding) as fh:
        fh.write(content)
    os.replace(tmp, path)


def atomic_write_json(path: str, data: dict, indent: int = 4) -> None:
    """
    Serialise *data* as JSON and write to *path* atomically.
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)
