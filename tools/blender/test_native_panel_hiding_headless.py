"""Headless: hiding/restoring the native Scene panels re-registers them cleanly (no crash,
idempotent, every panel still registered, original polls restored). Run:
  blender --background --factory-startup --python tools/blender/test_native_panel_hiding_headless.py
Whether the window really shows nothing else is a visual check (see docs/ui-workstream.md)."""
import os, sys
import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "plugins"))


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def scene_panels():
    return [c for c in bpy.types.Panel.__subclasses__()
            if getattr(c, "bl_space_type", None) == "PROPERTIES"
            and getattr(c, "bl_context", None) == "scene" and not c.__name__.startswith("YLOS")]


from blender.ui import browser  # noqa: E402

before = {c.__name__: c.__dict__.get("poll") for c in scene_panels()}
if len(before) < 10:
    fail(f"expected >=10 native Scene panels, got {len(before)}")

for cycle in range(2):
    n = browser.hide_native_panels()
    if n != len(before):
        fail(f"cycle {cycle}: patched {n}, expected {len(before)}")
    if browser.hide_native_panels() != 0:
        fail("hide_native_panels is not idempotent")
    now = {c.__name__ for c in scene_panels()}
    if now != set(before):
        fail(f"panels lost/added after hide: {now ^ set(before)}")
    for c in scene_panels():
        if "_make_hidden_poll" not in getattr(c.poll, "__qualname__", ""):
            fail(f"{c.__name__}: poll not wrapped")
    print(f"ok  cycle {cycle}: {n} native Scene panels wrapped + re-registered, idempotent")
    browser.restore_native_panels()
    for c in scene_panels():
        if c.__dict__.get("poll") is not before[c.__name__]:
            fail(f"{c.__name__}: poll not restored")
    if {c.__name__ for c in scene_panels()} != set(before):
        fail("panels lost after restore")
    print(f"ok  cycle {cycle}: restored")

print("PASS: native Scene panel hiding re-registers/restores cleanly headless")
