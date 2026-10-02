#!/bin/bash
# relocate_repo.sh - move the YlosPipeline repo out of a folder macOS protects. One-time.
#
# Why: Ylos.app is an app made of a script. macOS privacy (TCC) refuses it, without any prompt,
# every file under ~/Desktop, ~/Documents, ~/Downloads, iCloud Drive and external / network
# disks (field report 2026-10-01, docs/pipeline-log.md). With the repo on the Desktop the button
# can never start the server. A Desktop synced by iCloud is no place for a .git directory either.
#
# Usage:  bash tools/macos/relocate_repo.sh [DEST]        (default: ~/Developer/YlosPipeline)
#
# Nothing is deleted. In this order, stopping at the first problem:
#   1. refuse if DEST exists or sits in a protected place, or if Blender / Houdini run (they
#      loaded the Ylos add-on / package from the current folder and would keep pointing at it);
#   2. stop the Ylos server (launch_ui.command --stop: a Ylos server only, never a stranger);
#   3. copy the repo (ditto reads every file, so files kept only in iCloud are downloaded; the
#      extended attributes stay behind - iCloud and privacy markers belong to the old place);
#      __pycache__ is left out (regenerated on first import);
#   4. check the copy: same commit, same working-tree status, git fsck;
#   5. re-point what pointed at the old folder: the Blender add-on link(s), the Houdini package
#      link(s) (a package file that is a copy gets its path rewritten, .bak kept), YLOS_REPO in
#      the copy's plugins/houdini/ylos.json, ~/.ylos/repo_path;
#   6. rename the old folder "<name> (old copy - moved to <parent of DEST>)": a backup, to
#      delete by hand once everything works.
#
# bash 3.2-clean: this runs under the /bin/bash of macOS.

SRC="$(cd "$(dirname "$0")/../.." && pwd -P)"
SRC_L="$(cd "$(dirname "$0")/../.." && pwd -L)"   # the same folder as links may spell it
DEST="${1:-$HOME/Developer/YlosPipeline}"
DEST="${DEST%/}"

say()  { printf '[relocate] %s\n' "$*"; }
fail() { printf '[relocate] STOPPED: %s\n' "$*" >&2; exit 1; }

# is_old PATH: does PATH name the old folder or something inside it?
is_old() {
    case "$1" in
        "$SRC"|"$SRC"/*|"$SRC_L"|"$SRC_L"/*) return 0 ;;
    esac
    return 1
}

# new_path PATH: the same place, in the new folder (PATH must satisfy is_old).
new_path() {
    case "$1" in
        "$SRC"|"$SRC"/*) printf '%s\n' "$DEST${1#"$SRC"}" ;;
        *)               printf '%s\n' "$DEST${1#"$SRC_L"}" ;;
    esac
}

# relink LINK: if LINK is a symbolic link into the old folder, point it at the same place in
# the new one. Returns 1 when LINK is not such a link (nothing done).
relink() {
    local link="$1" target
    [ -L "$link" ] || return 1
    target="$(readlink "$link")"
    is_old "$target" || return 1
    ln -sfn "$(new_path "$target")" "$link" || fail "could not re-point $link"
    say "Re-pointed $link"
    say "        -> $(readlink "$link")"
    return 0
}

# rewrite_file FILE: replace the old folder by the new one in a text file - absolute form and
# "$HOME/..." form, whole path components only. A .bak copy is kept. Returns 1 if nothing matched.
rewrite_file() {
    grep -q -F -e "$SRC" -e "$SRC_L" -e "\$HOME${SRC#"$HOME"}" "$1" 2>/dev/null || return 1
    python3 - "$1" "$SRC" "$SRC_L" "$DEST" "$HOME" <<'PY'
import re, shutil, sys
path, src, src_l, dest, home = sys.argv[1:6]
pairs = [(src, dest), (src_l, dest)]
if dest.startswith(home + "/"):
    for old in (src, src_l):
        if old.startswith(home + "/"):
            pairs.append(("$HOME" + old[len(home):], "$HOME" + dest[len(home):]))
text = open(path, encoding="utf-8").read()
new = text
for old, rep in pairs:
    new = re.sub(re.escape(old) + r"(?=$|[/\"'\s])", lambda m: rep, new, flags=re.M)
if new == text:
    sys.exit(1)
shutil.copy2(path, path + ".bak")
open(path, "w", encoding="utf-8").write(new)
PY
}

# --- 0. what and where ------------------------------------------------------------------------
[ -f "$SRC/ylos_ui.py" ] && [ -f "$SRC/launch_ui.command" ] && [ -d "$SRC/.git" ] \
    || fail "$SRC does not look like the YlosPipeline repo."
case "$DEST" in
    /*) ;;
    *) fail "the destination must be an absolute path (got: $DEST)." ;;
esac
[ "$DEST" != "$SRC" ] && [ "$DEST" != "$SRC_L" ] || fail "the repo is already in $DEST."
case "$DEST/" in
    "$SRC"/*|"$SRC_L"/*) fail "the destination cannot be inside the repo." ;;
esac
case "$DEST/" in
    "$HOME/Desktop/"*|"$HOME/Documents/"*|"$HOME/Downloads/"*|"$HOME/Library/Mobile Documents/"*|/Volumes/*)
        fail "macOS protects $DEST too. Pick a folder outside Desktop, Documents, Downloads, iCloud Drive and external disks (the default is ~/Developer/YlosPipeline)." ;;
esac
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
    fail "$DEST already exists. Nothing was changed."
fi
say "Moving the repo out of $SRC"

# --- 1. nothing may be using the current folder -------------------------------------------------
if command -v pgrep >/dev/null 2>&1; then
    busy=""
    for proc in Blender houdini houdinifx houdinicore happrentice hindie hython; do
        if pgrep -i -x "$proc" >/dev/null 2>&1; then busy="$busy $proc"; fi
    done
    [ -z "$busy" ] || fail "quit first:$busy (it loaded Ylos from the current folder). Nothing was changed."
else
    say "WARNING: pgrep is missing, cannot check that Blender and Houdini are closed. Close them."
fi

# --- 2. stop the Ylos server ----------------------------------------------------------------------
/bin/bash "$SRC/launch_ui.command" --stop
case "$(/bin/bash "$SRC/launch_ui.command" --status 2>/dev/null)" in
    running|stale) fail "the Ylos server could not be stopped. Nothing was copied." ;;
esac

# --- 3. copy ----------------------------------------------------------------------------------------
parent="$(dirname "$DEST")"
mkdir -p "$parent" || fail "cannot create $parent."
say "Copying to $DEST (files kept only in iCloud are downloaded first)..."
if command -v ditto >/dev/null 2>&1; then
    ditto --noextattr --noqtn "$SRC" "$DEST"
else
    cp -R "$SRC" "$DEST"
fi
rc=$?
if [ "$rc" -ne 0 ]; then
    rm -rf "$DEST"
    fail "the copy failed (code $rc; offline, with files kept only in iCloud?). The original is untouched."
fi
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

# --- 4. check the copy --------------------------------------------------------------------------------
head_src="$(git -C "$SRC" rev-parse HEAD 2>/dev/null)"
head_dst="$(git -C "$DEST" rev-parse HEAD 2>/dev/null)"
[ -n "$head_src" ] && [ "$head_src" = "$head_dst" ] \
    || fail "the copy is not on the same commit. The original is untouched; inspect or delete $DEST."
[ "$(git -C "$SRC" status --porcelain 2>&1)" = "$(git -C "$DEST" status --porcelain 2>&1)" ] \
    || fail "the copy's working tree differs from the original. The original is untouched; inspect or delete $DEST."
git -C "$DEST" fsck --no-progress >/dev/null 2>&1 \
    || fail "git fsck failed on the copy. The original is untouched; inspect or delete $DEST."
say "Copy checked: commit $(git -C "$DEST" rev-parse --short HEAD), same working tree, git fsck clean."

# --- 5. re-point what pointed at the old folder -------------------------------------------------------
for link in "$HOME/Library/Application Support/Blender"/*/scripts/addons/ylos_pipeline; do
    if [ -L "$link" ]; then
        relink "$link" || say "Left alone (not a link into the old folder): $link"
    elif [ -e "$link" ]; then
        say "WARNING: $link is a copy, not a link: it will not follow the repo (plugins/blender/README.md)."
    fi
done
for pkg in "$HOME/Library/Preferences/houdini"/*/packages/*.json; do
    if [ -L "$pkg" ]; then
        relink "$pkg" || true
    elif [ -f "$pkg" ]; then
        if rewrite_file "$pkg"; then say "Rewrote the repo path in $pkg (backup: $pkg.bak)"; fi
    fi
done

# YLOS_REPO in the copy's Houdini package: the folder the repo now lives in ($HOME form if possible).
case "$DEST" in
    "$HOME"/*) repo_value="\$HOME${DEST#"$HOME"}" ;;
    *)         repo_value="$DEST" ;;
esac
pkg_json="$DEST/plugins/houdini/ylos.json"
if [ -f "$pkg_json" ] && ! grep -q -F "\"YLOS_REPO\": \"$repo_value\"" "$pkg_json"; then
    python3 - "$pkg_json" "$repo_value" <<'PY' && say "plugins/houdini/ylos.json: YLOS_REPO = $repo_value (tracked file: commit it)"
import re, sys
path, value = sys.argv[1:3]
text = open(path, encoding="utf-8").read()
new = re.sub(r'("YLOS_REPO"\s*:\s*")[^"]*(")', lambda m: m.group(1) + value + m.group(2), text)
open(path, "w", encoding="utf-8").write(new)
PY
fi

repo_file="$HOME/.ylos/repo_path"
if [ -f "$repo_file" ] && is_old "$(head -n 1 "$repo_file")"; then
    printf '%s\n' "$DEST" > "$repo_file" && say "Updated $repo_file"
fi

for rc_file in "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.bash_profile" "$HOME/.bashrc"; do
    if [ -f "$rc_file" ] && grep -q -F -e "$SRC" -e "$SRC_L" -e "\$HOME${SRC#"$HOME"}" "$rc_file"; then
        say "NOTE: $rc_file still mentions the old folder: update it by hand."
    fi
done

# --- 6. retire the old folder ---------------------------------------------------------------------------
backup="$SRC (old copy - moved to $(basename "$parent"))"
if [ -e "$backup" ]; then backup="$backup $(date '+%Y%m%d-%H%M%S')"; fi
if mv "$SRC" "$backup"; then
    say "Old folder kept as: $backup"
    say "Delete it once everything works."
else
    say "WARNING: could not rename the old folder. Do not use it anymore; delete it once everything works."
fi

say ""
say "Done. The repo now lives in $DEST"
say "Next: double-click Ylos in the Finder window that opens (drag it to the Dock to keep it)."
say "If an older Ylos is in the Dock, remove it: it points at the old folder."
if [ "$(uname -s)" = Darwin ]; then open -R "$DEST/Ylos.app"; fi
exit 0
