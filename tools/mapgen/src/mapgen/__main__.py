"""``python -m mapgen``. Guarded, so a spawned worker importing this as ``__mp_main__`` runs nothing."""

if __name__ == "__main__":
    from mapgen.cli import main

    raise SystemExit(main())
