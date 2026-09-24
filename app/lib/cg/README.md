# Cartograph widgets, as consumed by the app

Each entry here is a symlink to a widget's `src/` under `app/cg/`, so the app
imports widget code as its own source:

    import '../cg/inline_editable_text/inline_editable_text.dart';

This is the Flutter analogue of the Python side's
`from cg.<widget>.src.<module> import …`. The widget directories under
`app/cg/` are Cartograph's; never edit them here — change the widget, validate,
check in, and the symlink picks it up.

| symlink | widget |
| --- | --- |
| `signature_pad` | `frontend-signature-pad-flutter` (themed by `widgets/signature.dart`) |
| `syntax_highlighted_code` | `frontend-syntax-highlighted-code-flutter` (palette in `theme/code_palette.dart`; Files previews, Agents trace bodies) |
| `json_tree` | `frontend-json-tree-flutter` (Files: .json/.jsonl/.ipynb as a tree) |
| `delimited_table` | `frontend-delimited-table-flutter` (Files: .csv/.tsv as a table) |
| `archive_contents` | `data-archive-contents-flutter` (Files: zip/tar/tar.gz… browsed as a nested folder) |

## Blueprints

A blueprint imports its leaves as packages, so it cannot be symlinked like a
leaf. It is a path dependency in `pubspec.yaml`, with its leaves in
`dependency_overrides` pointing at the app's copies under `cg/`, and each of
those packages has a `lib ⇒ src` symlink so `package:` imports resolve.

| package | blueprint | leaves |
| --- | --- | --- |
| `midi_file_viewer` | `bp-midi-file-viewer-flutter` (Files tab, `.mid`/`.midi`) | `data-standard-midi-file-reader-flutter`, `data-general-midi-names-flutter`, `frontend-piano-roll-flutter`, `frontend-timeline-lanes-flutter` |
