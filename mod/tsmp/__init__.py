"""TSMP - The Sims 4 multiplayer mod.

The game imports this package when it loads tsmp.ts4script. Nothing here may
raise: a failing script mod can break the game's own startup.
"""

__version__ = "0.0.1"

try:
    from tsmp import bootstrap

    bootstrap.start()
except Exception:  # pragma: no cover - only reachable inside the game
    try:
        from tsmp import log

        log.exception("TSMP failed to start")
    except Exception:
        pass
