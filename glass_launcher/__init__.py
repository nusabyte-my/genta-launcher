"""Genta launcher: Ulauncher-style glass launcher with widgets, AI and desktop tips.

Imported by Genta (see Genta's ``load_launcher``) so the launcher shares Genta's
process, database connection, focus clock and media bridge. Submodules such as
``glass_launcher.wl`` stay importable without Genta (the desktop watcher uses them).
"""
__all__ = ["GlassLauncher", "TITLE"]


def __getattr__(name):
    if name in __all__:
        from . import window
        return getattr(window, name)
    raise AttributeError(name)
