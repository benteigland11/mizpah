# Mizpah app

The desktop app: the inbox, briefs, tasks, providers and the Deputy. It runs the engine from the
checkout it sits in (the `.venv` made by `uv sync` at the repository root); set `MIZPAH_ENGINE` to
point it at another checkout.

```bash
flutter run -d linux     # run
flutter analyze          # lint
flutter test             # tests
```

Build dependencies on Linux: GTK 3, mpv, libnotify and ayatana-appindicator development packages,
plus clang, cmake and ninja. See the [repository README](../README.md) for the full setup.

`cg/` holds the app's Cartograph widgets (reusable Flutter pieces); `lib/cg/` links to them.
