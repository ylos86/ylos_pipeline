# -*- coding: utf-8 -*-
# ylos_houdini/startup/ylos_init.py
# Executed by Houdini at startup via $HOUDINI_PATH/startup/*.py mechanism.
# Registers the hip file event callback so session context is restored on load.

def _init():
    try:
        from ylos_houdini.session import register_callbacks
        register_callbacks()
    except ImportError as e:
        # ylos_houdini not on path yet (race condition during very early startup).
        # The package JSON should prevent this but guard anyway.
        print(f"[Ylos] startup import failed (will retry on next load): {e}")
    except Exception as e:
        print(f"[Ylos] startup init error: {e}")

_init()
