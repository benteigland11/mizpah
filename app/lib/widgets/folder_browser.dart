import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart';
import 'package:midi_file_viewer/midi_file_viewer.dart';
import 'package:pdfrx/pdfrx.dart';

import '../models/environment.dart';
import '../cg/archive_contents/archive_contents.dart';
import '../cg/delimited_table/delimited_table.dart';
import '../cg/json_tree/json_tree.dart';
import '../cg/syntax_highlighted_code/syntax_highlighted_code.dart';
import '../theme/app_theme.dart';
import '../theme/code_palette.dart';
import '../theme/kit_styles.dart';

/// A folder, browsed read-only: an explorer tree on the left (folders read
/// as they are unfolded), the picked file on the right with line numbers.
/// Each entry says how long ago it changed, and what changed within
/// [recent] reads in ink — the files being worked on. With [refreshEvery]
/// the open folders and the shown file are read again on that beat (a live
/// task). [openInEditor], when given, puts OPEN IN EDITOR beside the file.
class FolderBrowser extends StatefulWidget {
  const FolderBrowser({
    super.key,
    required this.rootLabel,
    required this.rootPath,
    required this.list,
    required this.read,
    this.readBytes,
    this.mediaUri,
    this.openInEditor,
    this.refreshEvery,
    this.recent = const Duration(minutes: 5),
    this.emptyHint = 'Pick a file to read it.',
    this.title = 'FILES',
  });

  /// The heading over the tree.
  final String title;

  /// What the root is called (a task's title) and where it is (shown as `~/…`).
  final String rootLabel;
  final String rootPath;

  /// One folder's entries by path inside the root ('' is the top), and a file.
  final Future<List<FileNode>> Function(String path) list;
  final Future<EnvFile> Function(String path) read;

  /// A file's bytes, for the viewers (PDF, SVG, pictures); without it those
  /// show only their size.
  final Future<List<int>> Function(String path)? readBytes;

  /// Where the player reads audio and video from (a `file://` URI on the
  /// desk); without it they show only their size.
  final String Function(String path)? mediaUri;
  final Future<void> Function(String path)? openInEditor;
  final Duration? refreshEvery;
  final Duration recent;
  final String emptyHint;

  @override
  State<FolderBrowser> createState() => _FolderBrowserState();
}

class _FolderBrowserState extends State<FolderBrowser> {
  final _folders = <String, List<FileNode>>{};
  final _unfolded = <String>{''};
  String? _file;
  EnvFile? _preview;
  Uint8List? _bytes;
  DateTime? _previewAt;
  String? _problem;
  Timer? _timer;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _refresh();
    _arm();
  }

  @override
  void didUpdateWidget(covariant FolderBrowser old) {
    super.didUpdateWidget(old);
    if (old.refreshEvery != widget.refreshEvery) _arm();
  }

  void _arm() {
    _timer?.cancel();
    final every = widget.refreshEvery;
    _timer = every == null ? null : Timer.periodic(every, (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  /// Read every open folder again, and the shown file if it changed.
  Future<void> _refresh() async {
    if (_busy) return;
    _busy = true;
    try {
      for (final p in _unfolded.toList()) {
        _folders[p] = await widget.list(p);
      }
      final f = _file;
      if (f != null) {
        final node = _nodeOf(f);
        if (node == null) {
          _file = null;
          _preview = null;
        } else if (_preview == null || node.modified != _previewAt) {
          await _load(f);
          _previewAt = node.modified;
        }
      }
      _problem = null;
    } catch (e) {
      _problem = '$e';
    } finally {
      _busy = false;
    }
    if (mounted) setState(() {});
  }

  FileNode? _nodeOf(String path) {
    final i = path.lastIndexOf('/');
    final dir = i < 0 ? '' : path.substring(0, i);
    final name = i < 0 ? path : path.substring(i + 1);
    return _folders[dir]?.where((n) => n.name == name).firstOrNull;
  }

  Future<void> _tap(String path, FileNode node) async {
    if (node.isDir && node.link == null) {
      if (!_unfolded.remove(path)) {
        _unfolded.add(path);
        try {
          _folders[path] = await widget.list(path);
        } catch (e) {
          _problem = '$e';
        }
      }
      setState(() {});
      return;
    }
    setState(() {
      _file = path;
      _preview = null;
      _bytes = null;
    });
    try {
      await _load(path);
      _previewAt = node.modified;
    } catch (e) {
      _problem = '$e';
    }
    if (mounted) setState(() {});
  }

  /// What kind of viewer a file gets, by its extension; null for text or
  /// anything else.
  static String? kindOf(String path) {
    final ext = path.contains('.') ? path.split('.').last.toLowerCase() : '';
    return switch (ext) {
      'pdf' => 'pdf',
      'svg' => 'svg',
      'png' || 'jpg' || 'jpeg' || 'gif' || 'webp' || 'bmp' => 'image',
      'mp3' || 'wav' || 'flac' || 'ogg' || 'oga' || 'm4a' || 'aac' || 'opus' => 'audio',
      'mp4' || 'mkv' || 'webm' || 'mov' || 'avi' || 'm4v' => 'video',
      'mid' || 'midi' => 'midi',
      'zip' || 'tar' || 'tgz' || 'gz' || 'bz2' || 'tbz2' || 'xz' || 'txz' || 'jar' || 'whl' => 'archive',
      _ => null,
    };
  }

  /// Read a file for the pane: its bytes for a viewer, else its text (or size).
  Future<void> _load(String path) async {
    final kind = kindOf(path);
    final bytes = widget.readBytes;
    if (kind == 'audio' || kind == 'video') {
      // The player streams it from disk; the pane needs only its size.
      _bytes = null;
      _preview = await widget.read(path);
    } else if (kind != null && bytes != null) {
      final b = Uint8List.fromList(await bytes(path));
      _bytes = b;
      _preview = EnvFile(size: b.length, binary: true);
    } else {
      _bytes = null;
      _preview = await widget.read(path);
    }
  }

  static String _size(int bytes) => bytes >= 1e9
      ? '${(bytes / 1e9).toStringAsFixed(1)} GB'
      : bytes >= 1e6
      ? '${(bytes / 1e6).toStringAsFixed(1)} MB'
      : bytes >= 1e3
      ? '${(bytes / 1e3).toStringAsFixed(0)} KB'
      : '$bytes B';

  /// How long ago: `now`, `4m`, `3h`, `2d`.
  static String _age(DateTime at) {
    final d = DateTime.now().toUtc().difference(at);
    if (d.inSeconds < 45) return 'now';
    if (d.inMinutes < 60) return '${d.inMinutes}m';
    if (d.inHours < 48) return '${d.inHours}h';
    return '${d.inDays}d';
  }

  bool _isRecent(FileNode n) => n.modified != null && DateTime.now().toUtc().difference(n.modified!) < widget.recent;

  List<(String, FileNode, int)> _lines() {
    final out = <(String, FileNode, int)>[];
    void folder(String path, int depth) {
      for (final n in _folders[path] ?? const <FileNode>[]) {
        final p = path.isEmpty ? n.name : '$path/${n.name}';
        out.add((p, n, depth));
        if (n.isDir && n.link == null && _unfolded.contains(p)) folder(p, depth + 1);
      }
    }

    folder('', 0);
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final lines = _lines();
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(
          width: 320,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(Sp.l, Sp.m, Sp.s, Sp.xs),
                child: Row(
                  children: [
                    Expanded(child: Text(widget.title, style: opsLabelStyle(context))),
                    if (widget.refreshEvery != null)
                      Padding(
                        padding: const EdgeInsets.only(right: Sp.xs),
                        child: Text('LIVE', style: opsKeyStyle(context).copyWith(color: AppTheme.ink(context), fontWeight: FontWeight.w700)),
                      ),
                    IconButton(
                      tooltip: 'Read again',
                      iconSize: 16,
                      visualDensity: VisualDensity.compact,
                      icon: const Icon(Icons.refresh),
                      onPressed: _refresh,
                    ),
                    if (widget.openInEditor != null)
                      IconButton(
                        tooltip: 'Open the folder in the editor',
                        iconSize: 16,
                        visualDensity: VisualDensity.compact,
                        icon: const Icon(Icons.open_in_new),
                        onPressed: () => widget.openInEditor!(''),
                      ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(Sp.l, 0, Sp.l, Sp.s),
                child: Text(
                  widget.rootPath,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: AppTheme.mono.copyWith(fontSize: 11, color: cs.onSurfaceVariant),
                ),
              ),
              if (_problem != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(Sp.l, 0, Sp.l, Sp.s),
                  child: Text(_problem!, style: AppTheme.mono.copyWith(fontSize: 11.5, color: cs.error)),
                ),
              Divider(height: 1, color: cs.outlineVariant),
              Expanded(
                child: ListView.builder(
                  padding: const EdgeInsets.symmetric(vertical: Sp.xs),
                  itemCount: lines.length,
                  itemBuilder: (context, i) {
                    final (path, node, depth) = lines[i];
                    return _row(context, path, node, depth);
                  },
                ),
              ),
            ],
          ),
        ),
        VerticalDivider(width: 1, color: cs.outlineVariant),
        Expanded(child: _pane(context)),
      ],
    );
  }

  Widget _row(BuildContext context, String path, FileNode n, int depth) {
    final cs = Theme.of(context).colorScheme;
    final dir = n.isDir && n.link == null;
    final open = _unfolded.contains(path);
    final recent = !dir && _isRecent(n);
    final mono = AppTheme.mono.copyWith(fontSize: 12.5, height: 1.3);
    return InkWell(
      onTap: () => _tap(path, n),
      onDoubleTap: widget.openInEditor == null || dir ? null : () => widget.openInEditor!(path),
      child: Container(
        color: path == _file ? cs.surfaceContainerHighest : null,
        padding: EdgeInsets.fromLTRB(8 + 14.0 * depth, 3, Sp.m, 3),
        child: Row(
          children: [
            SizedBox(width: 18, child: dir ? Icon(open ? Icons.expand_more : Icons.chevron_right, size: 15, color: cs.onSurfaceVariant) : null),
            Icon(
              dir ? (open ? Icons.folder_open_outlined : Icons.folder_outlined) : n.link != null ? Icons.link : Icons.description_outlined,
              size: 14,
              color: recent ? AppTheme.ink(context) : cs.onSurfaceVariant,
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                '${n.name}${n.link != null ? ' → ${n.link}' : ''}',
                overflow: TextOverflow.ellipsis,
                style: mono.copyWith(
                  color: recent ? AppTheme.ink(context) : dir ? cs.onSurface : cs.onSurfaceVariant,
                  fontWeight: recent ? FontWeight.w700 : null,
                ),
              ),
            ),
            if (n.modified != null && !dir)
              Text(_age(n.modified!), style: mono.copyWith(fontSize: 11, color: recent ? AppTheme.ink(context) : cs.outline)),
          ],
        ),
      ),
    );
  }

  Widget _pane(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final f = _file;
    if (f == null) {
      return Center(child: Text(widget.emptyHint, style: opsKeyStyle(context)));
    }
    final node = _nodeOf(f);
    final p = _preview;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.m, Sp.l, Sp.m),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  [widget.rootLabel, ...f.split('/')].join('  /  '),
                  overflow: TextOverflow.ellipsis,
                  style: AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurface),
                ),
              ),
              if (node?.modified != null)
                Text(_age(node!.modified!) == 'now' ? 'changed just now  ·  ' : 'changed ${_age(node.modified!)} ago  ·  ', style: opsKeyStyle(context)),
              if (p != null) Text(_size(p.size), style: opsKeyStyle(context)),
              IconButton(
                tooltip: 'Copy path',
                iconSize: 15,
                icon: const Icon(Icons.copy),
                onPressed: () => Clipboard.setData(ClipboardData(text: '${widget.rootPath}/$f')),
              ),
              if (widget.openInEditor != null)
                OutlinedButton.icon(
                  onPressed: () => widget.openInEditor!(f),
                  icon: const Icon(Icons.open_in_new, size: 14),
                  label: const Text('OPEN IN EDITOR'),
                ),
            ],
          ),
        ),
        Divider(height: 1, color: cs.outlineVariant),
        Expanded(
          child: p == null
              ? Center(child: Text('Reading…', style: opsKeyStyle(context)))
              : (kindOf(f) == 'audio' || kindOf(f) == 'video') && widget.mediaUri != null
              ? _MediaView(key: ValueKey('$f@$_previewAt'), uri: widget.mediaUri!(f), video: kindOf(f) == 'video')
              : _bytes != null
              ? _Viewer(key: ValueKey('$f@$_previewAt'), kind: kindOf(f)!, name: f, bytes: _bytes!)
              : p.text == null
              ? Center(child: Text('${p.binary ? 'A binary file' : 'Too big to show here'} · ${_size(p.size)}', style: opsKeyStyle(context)))
              : _FilePreview(name: f, text: p.text!),
        ),
      ],
    );
  }
}

/// A file shown read-only: line numbers, no wrapping (long lines scroll
/// sideways), coloured by its language when its name or its first bytes
/// say what it is. JSON opens as a tree and CSV/TSV as a table, each with
/// the text a click away.
class _FilePreview extends StatefulWidget {
  const _FilePreview({required this.name, required this.text});
  final String name;
  final String text;

  @override
  State<_FilePreview> createState() => _FilePreviewState();
}

class _FilePreviewState extends State<_FilePreview> {
  /// The structured view of the text, when it has one: a decoded JSON value
  /// or a table. Null when the file is neither or does not parse.
  Object? _shape;
  bool _parsed = false;
  bool _asText = false;
  String? _shapedText;

  static String _ext(String name) => name.contains('.') ? name.split('.').last.toLowerCase() : '';

  void _parse() {
    if (_parsed && identical(_shapedText, widget.text)) return;
    _parsed = true;
    _shapedText = widget.text;
    _shape = null;
    final ext = _ext(widget.name);
    try {
      if (ext == 'json' || ext == 'jsonl' || ext == 'ndjson' || ext == 'ipynb' || ext == 'geojson') {
        _shape = _Json(parseJsonDocument(widget.text));
      } else if (ext == 'csv' || ext == 'tsv' || ext == 'tab') {
        final t = parseDelimited(widget.text, delimiter: ext == 'csv' ? null : '\t');
        if (t.columns > 0) _shape = t;
      }
    } on FormatException {
      _shape = null;
    }
  }

  @override
  void didUpdateWidget(_FilePreview old) {
    super.didUpdateWidget(old);
    if (old.name != widget.name) _asText = false;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    _parse();
    final shape = _shape;
    final text = SingleChildScrollView(
      padding: const EdgeInsets.symmetric(vertical: Sp.s),
      child: HighlightedCode(
        text: widget.text,
        language: languageForPath(widget.name) ?? guessLanguage(widget.text),
        palette: codePalette(context),
        style: AppTheme.mono.copyWith(fontSize: 13, height: 1.5, color: cs.onSurface),
        lineNumbers: true,
        lineNumberColor: cs.outline,
        gutterPadding: const EdgeInsets.symmetric(horizontal: Sp.m),
      ),
    );
    if (shape == null) return text;
    final label = shape is DelimitedTable ? 'TABLE' : 'TREE';
    final note = shape is DelimitedTable
        ? '${shape.rows.length}${shape.truncated ? '+' : ''} rows · ${shape.columns} columns · '
            '${shape.delimiter == '\t' ? 'tab' : shape.delimiter} separated${shape.hasHeader ? '' : ' · no header row'}'
        : null;
    Widget tab(String l, bool on) => InkWell(
          onTap: () => setState(() => _asText = l == 'TEXT'),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: Sp.s, vertical: 3),
            child: Text(l, style: opsKeyStyle(context).copyWith(
              color: on ? AppTheme.ink(context) : null,
              fontWeight: on ? FontWeight.w700 : null,
            )),
          ),
        );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Sp.m, Sp.xs, Sp.m, Sp.xs),
          child: Row(children: [
            tab(label, !_asText),
            tab('TEXT', _asText),
            const SizedBox(width: Sp.m),
            if (note != null && !_asText)
              Expanded(child: Text(note, maxLines: 1, overflow: TextOverflow.ellipsis, style: opsKeyStyle(context))),
          ]),
        ),
        Divider(height: 1, color: cs.outlineVariant),
        Expanded(
          child: _asText
              ? text
              : shape is DelimitedTable
              ? DelimitedTableView(table: shape, style: _tableStyle(context))
              : JsonTree(value: (shape as _Json).value, expandDepth: 2, style: _jsonStyle(context)),
        ),
      ],
    );
  }
}

/// A decoded JSON document (a box, since null is a valid document).
class _Json {
  const _Json(this.value);
  final Object? value;
}

JsonTreeStyle _jsonStyle(BuildContext context) {
  final cs = Theme.of(context).colorScheme;
  final p = codePalette(context);
  return JsonTreeStyle(
    text: AppTheme.mono.copyWith(fontSize: 12.5, height: 1.55, color: cs.onSurface),
    keyColor: p.key ?? cs.onSurface,
    indexColor: cs.outline,
    stringColor: p.string ?? cs.onSurface,
    numberColor: p.number ?? cs.onSurface,
    literalColor: p.keyword ?? cs.onSurface,
    punctuationColor: cs.onSurfaceVariant,
    summaryColor: cs.outline,
    guideColor: cs.outlineVariant,
    selectedColor: cs.surfaceContainerHighest,
    padding: const EdgeInsets.symmetric(vertical: Sp.s, horizontal: Sp.m),
  );
}

DelimitedTableStyle _tableStyle(BuildContext context) {
  final cs = Theme.of(context).colorScheme;
  final mono = AppTheme.mono.copyWith(fontSize: 12.5, color: cs.onSurface);
  return DelimitedTableStyle(
    text: mono,
    headerText: mono.copyWith(fontSize: 12, fontWeight: FontWeight.w700, color: AppTheme.ink(context)),
    rowNumberText: mono.copyWith(fontSize: 11, color: cs.outline),
    background: cs.surfaceContainerLowest,
    headerBackground: cs.surfaceContainer,
    stripe: cs.surfaceContainerLow,
    gridLine: cs.outlineVariant,
  );
}

/// An archive (zip, tar, tar.gz…) browsed as a folder: its own tree beside
/// its own preview, so every viewer works on the files inside. Nothing is
/// unpacked to disk.
class _ArchiveView extends StatefulWidget {
  const _ArchiveView({required this.name, required this.bytes});
  final String name;
  final Uint8List bytes;

  @override
  State<_ArchiveView> createState() => _ArchiveViewState();
}

class _ArchiveViewState extends State<_ArchiveView> {
  ArchiveContents? _contents;
  String? _problem;

  @override
  void initState() {
    super.initState();
    // Let "Reading…" draw before a big archive is decoded.
    Future<void>.delayed(Duration.zero, () {
      if (!mounted) return;
      setState(() {
        try {
          _contents = ArchiveContents.decode(widget.bytes, name: widget.name);
        } on ArchiveContentsException catch (e) {
          _problem = e.message;
        }
      });
    });
  }

  static const _textLimit = 2 << 20;

  static EnvFile _file(Uint8List b) {
    final head = b.length > 8192 ? b.sublist(0, 8192) : b;
    if (head.contains(0)) return EnvFile(size: b.length, binary: true);
    if (b.length > _textLimit) return EnvFile(size: b.length, tooBig: true);
    return EnvFile(size: b.length, text: utf8.decode(b, allowMalformed: true));
  }

  @override
  Widget build(BuildContext context) {
    final c = _contents;
    if (c == null) {
      return Center(child: Text(_problem == null ? 'Reading…' : 'Not readable as an archive: $_problem', style: opsKeyStyle(context)));
    }
    return FolderBrowser(
      title: 'ARCHIVE',
      rootLabel: widget.name,
      rootPath: '${widget.name} · ${c.format} · ${c.fileCount} files · ${_FolderBrowserState._size(c.totalSize)} unpacked',
      list: (p) async => [
        for (final e in c.children(p))
          FileNode(name: e.name, isDir: e.isDir, size: e.isDir ? null : e.size, link: e.link, modified: e.modified),
      ],
      read: (p) async => _file(c.bytes(p) ?? Uint8List(0)),
      readBytes: (p) async => c.bytes(p) ?? Uint8List(0),
      emptyHint: 'Pick a file inside the archive to read it.',
    );
  }
}

class _Viewer extends StatelessWidget {
  const _Viewer({super.key, required this.kind, required this.name, required this.bytes});
  final String kind;
  final String name;
  final Uint8List bytes;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (kind == 'midi') return MidiFileViewer(bytes: bytes, style: _midiStyle(cs));
    if (kind == 'archive') return _ArchiveView(name: name, bytes: bytes);
    if (kind == 'pdf') {
      return ColoredBox(
        color: cs.surfaceContainerLowest,
        child: PdfViewer.data(bytes, sourceName: name, params: const PdfViewerParams(margin: 12)),
      );
    }
    final picture = kind == 'svg' ? SvgPicture.memory(bytes, fit: BoxFit.contain) : Image.memory(bytes, fit: BoxFit.contain);
    return ColoredBox(
      color: cs.surfaceContainerLowest,
      child: InteractiveViewer(
        maxScale: 12,
        child: Padding(padding: const EdgeInsets.all(Sp.l), child: Center(child: picture)),
      ),
    );
  }
}

/// The MIDI viewer in the desk's colours: surfaces from the scheme, parts
/// in a palette that reads on either brightness.
MidiViewerStyle _midiStyle(ColorScheme cs) {
  final dark = cs.brightness == Brightness.dark;
  final parts = dark
      ? const [Color(0xFF5B9BFF), Color(0xFFFF8A5B), Color(0xFF6BD68A), Color(0xFFE0C35B),
          Color(0xFFC77DFF), Color(0xFF5BD6D0), Color(0xFFFF6B9A), Color(0xFFA8B45B)]
      : const [Color(0xFF2F6FD6), Color(0xFFD9602E), Color(0xFF2E9A55), Color(0xFFB08A12),
          Color(0xFF8E4FD1), Color(0xFF1D9A94), Color(0xFFD13F72), Color(0xFF6F7A23)];
  final mono = AppTheme.mono.copyWith(fontSize: 10, color: cs.onSurfaceVariant);
  final text = TextStyle(fontFamily: AppTheme.uiFamily, fontSize: 12, color: cs.onSurface);
  final grid = cs.outlineVariant;
  PianoRollStyle roll(double ruler) => PianoRollStyle(
        background: cs.surfaceContainerLowest,
        blackKeyRow: cs.surfaceContainerLow,
        octaveLine: grid,
        beatLine: grid.withValues(alpha: 0.35),
        barLine: grid,
        whiteKey: dark ? const Color(0xFFD9DCE1) : const Color(0xFFFAFAFA),
        blackKey: dark ? const Color(0xFF2A2D33) : const Color(0xFF3A3D42),
        keyLabel: mono.copyWith(fontSize: 9, color: const Color(0xFF6A6F78)),
        rulerBackground: cs.surfaceContainer,
        rulerLabel: mono,
        partColors: parts,
        noteOutline: cs.shadow.withValues(alpha: 0.3),
        cardBackground: cs.inverseSurface.withValues(alpha: 0.95),
        cardText: mono.copyWith(fontSize: 11, color: cs.onInverseSurface, height: 1.4),
        rulerHeight: ruler,
        keyboardWidth: 56,
      );
  return MidiViewerStyle(
    roll: roll(20),
    drums: roll(0),
    lanes: LaneStyle(
      background: cs.surfaceContainerLowest,
      labelBackground: cs.surfaceContainer,
      divider: grid,
      color: parts.first,
      beatLine: grid.withValues(alpha: 0.35),
      barLine: grid,
      label: mono,
      value: mono.copyWith(color: cs.onSurface),
      labelWidth: 56,
    ),
    background: cs.surfaceContainerLowest,
    panel: cs.surfaceContainer,
    divider: grid,
    text: text,
    muted: mono,
    accent: cs.primary,
  );
}

/// Audio or video, played in place by the native player (media_kit on
/// libmpv). A video gets the player's own controls; audio a bar of play,
/// scrubber, time and volume. The player is released when the file is left.
class _MediaView extends StatefulWidget {
  const _MediaView({super.key, required this.uri, required this.video});
  final String uri;
  final bool video;

  @override
  State<_MediaView> createState() => _MediaViewState();
}

class _MediaViewState extends State<_MediaView> {
  late final Player _player = Player();
  late final VideoController? _video = widget.video ? VideoController(_player) : null;

  @override
  void initState() {
    super.initState();
    _player.open(Media(widget.uri), play: false);
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  static String _clock(Duration d) {
    final s = d.inSeconds;
    return '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final v = _video;
    if (v != null) {
      return ColoredBox(color: Colors.black, child: Video(controller: v));
    }
    final mono = AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant);
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Padding(
          padding: const EdgeInsets.all(Sp.xl),
          child: Row(
            children: [
              StreamBuilder<bool>(
                stream: _player.stream.playing,
                initialData: _player.state.playing,
                builder: (context, s) => IconButton.filledTonal(
                  tooltip: s.data == true ? 'Pause' : 'Play',
                  icon: Icon(s.data == true ? Icons.pause : Icons.play_arrow),
                  onPressed: _player.playOrPause,
                ),
              ),
              const SizedBox(width: Sp.m),
              Expanded(
                child: StreamBuilder<Duration>(
                  stream: _player.stream.position,
                  initialData: _player.state.position,
                  builder: (context, pos) => StreamBuilder<Duration>(
                    stream: _player.stream.duration,
                    initialData: _player.state.duration,
                    builder: (context, dur) {
                      final total = dur.data ?? Duration.zero;
                      final at = pos.data ?? Duration.zero;
                      final max = total.inMilliseconds <= 0 ? 1.0 : total.inMilliseconds.toDouble();
                      return Row(
                        children: [
                          Text(_clock(at), style: mono),
                          Expanded(
                            child: Slider(
                              value: at.inMilliseconds.clamp(0, max).toDouble(),
                              max: max,
                              onChanged: (x) => _player.seek(Duration(milliseconds: x.round())),
                            ),
                          ),
                          Text(_clock(total), style: mono),
                        ],
                      );
                    },
                  ),
                ),
              ),
              const SizedBox(width: Sp.m),
              Icon(Icons.volume_up_outlined, size: 16, color: cs.onSurfaceVariant),
              SizedBox(
                width: 110,
                child: StreamBuilder<double>(
                  stream: _player.stream.volume,
                  initialData: _player.state.volume,
                  builder: (context, vol) =>
                      Slider(value: (vol.data ?? 100).clamp(0, 100), max: 100, onChanged: (x) => _player.setVolume(x)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
