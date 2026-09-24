import 'dart:convert';
import 'dart:io';

import '../models/document.dart';
import 'engine.dart';

/// The desk's own paper: what comes to the Administrator from above and
/// from beside, not from any project. Two senders. **The Board**, the
/// office above the Administrator's, sends mandate paper: the files under
/// `notices/board/` in the engine home, each sent to every desk once; the
/// record of what was sent when is `board.json` beside the read/dismissed
/// marks, and the text is read from the file every time, so an edit shows
/// on every desk. **The Deputy** reports up: what the automated ops under
/// the desk did or could not do, one line each in the engine's
/// `deputy/notices.jsonl`, filed by the host where a condition holds,
/// never on the seat's word (`mizpah.notices.send`; nothing calls it yet).
/// Both read into the tray with the project paper and are dismissed the
/// same way.
class DeskNotices {
  DeskNotices({required this.boardDir, required this.boardFile, required this.deputyFile});

  /// The Board's notice files (`notices/board/*.md`), or null when the
  /// engine home is not known yet.
  final Directory? boardDir;

  /// The sent record: number → when, ISO-8601.
  final File boardFile;
  final File deputyFile;

  static const board = BriefSummary(id: 'board', title: 'The Board', path: '');
  static const deputy = BriefSummary(id: 'deputy', title: 'The Deputy', path: '');

  /// Who the tray names as the sender of a notice.
  static BriefSummary senderOf(InboxDocument d) => d.kind == DocKind.deputyNotice ? deputy : board;

  List<AttentionItem> read() => [
    for (final d in _board()) AttentionItem(project: board, document: d),
    for (final d in _deputy()) AttentionItem(project: deputy, document: d),
  ];

  /// Every file in the Board's folder, in name order, with the time it was
  /// sent to this desk — now, for one not on the record yet, and the record
  /// is written. Files sent in one batch are dated a second apart, the
  /// first newest, so it sits on top of the tray and is the one that opens.
  List<InboxDocument> _board() {
    final dir = boardDir;
    if (dir == null || !dir.existsSync()) return const [];
    final files = dir.listSync().whereType<File>().where((f) => f.path.endsWith('.md')).toList()
      ..sort((a, b) => a.path.compareTo(b.path));
    final sent = _sent();
    final now = DateTime.now();
    var changed = false;
    final out = <InboxDocument>[];
    for (final (i, f) in files.indexed) {
      final parsed = parseNotice(f.readAsStringSync(), from: board.title, kind: DocKind.boardNotice);
      if (parsed == null) continue;
      var at = DateTime.tryParse(sent[parsed.number] ?? '');
      if (at == null) {
        at = now.subtract(Duration(seconds: i));
        sent[parsed.number] = at.toUtc().toIso8601String();
        changed = true;
      }
      out.add(parsed.dated(at));
    }
    if (changed) _writeSent(sent);
    return out;
  }

  Map<String, String> _sent() {
    if (!boardFile.existsSync()) return {};
    try {
      final j = jsonDecode(boardFile.readAsStringSync());
      // An older desk kept the sent documents whole; their numbers and
      // times carry over as the record.
      if (j is List) return {for (final d in j) (d as Map)['number'] as String: d['at'] as String};
      return (j as Map).cast<String, String>();
    } catch (_) {
      return {};
    }
  }

  void _writeSent(Map<String, String> sent) {
    boardFile.parent.createSync(recursive: true);
    final tmp = File('${boardFile.path}.tmp');
    tmp.writeAsStringSync(jsonEncode(sent));
    tmp.renameSync(boardFile.path);
  }

  /// The Deputy's file: one notice per line, the latest per number (a
  /// resend is a revision), in the order first sent.
  List<InboxDocument> _deputy() {
    if (!deputyFile.existsSync()) return const [];
    final latest = <String, InboxDocument>{};
    for (final line in deputyFile.readAsLinesSync()) {
      if (line.trim().isEmpty) continue;
      try {
        final d = InboxDocument.fromJson((jsonDecode(line) as Map).cast<String, dynamic>());
        latest[d.number] = d;
      } catch (_) {}
    }
    return latest.values.toList();
  }
}

/// A notice file: front matter (`number`, `title`, `status`, `hot`) between
/// `---` lines, then the sheet — `## ` starts a section; each other
/// non-empty line is one line of it: `- [Lead](link) text` for an action
/// row (the lead set in the margin, the whole row a link), `[label](link)`
/// anywhere in a line for a link in the running text, a line all in
/// backticks for mono, all in `**` for a warning, `> ` for a quote. `\$steps` anywhere in the title or a line is
/// the number of link lines in the file, as a word, so a checklist counts
/// itself. Null when the front matter is missing or has no number. `dated`
/// is not set here; the caller knows when it was sent.
InboxDocument? parseNotice(String text, {required String from, required DocKind kind}) {
  final lines = const LineSplitter().convert(text);
  if (lines.isEmpty || lines.first.trim() != '---') return null;
  final end = lines.indexWhere((l) => l.trim() == '---', 1);
  if (end < 0) return null;
  final meta = <String, String>{};
  for (final l in lines.sublist(1, end)) {
    final cut = l.indexOf(':');
    if (cut > 0) meta[l.substring(0, cut).trim()] = l.substring(cut + 1).trim();
  }
  final number = meta['number'] ?? '';
  if (number.isEmpty) return null;
  final sections = <DocSection>[];
  var heading = '';
  var body = <DocLine>[];
  void close() {
    if (heading.isNotEmpty || body.isNotEmpty) sections.add(DocSection(heading, body));
    heading = '';
    body = [];
  }
  final link = RegExp(r'^-\s+\[([^\]]+)\]\(([^)]+)\)\s*(.*)$');
  final inline = RegExp(r'\[([^\]]+)\]\(([^)]+)\)');
  final body0 = lines.sublist(end + 1);
  final steps = _word(body0.where((l) => link.hasMatch(l.trim())).length);
  String fill(String t) => t.replaceAll(r'$steps', steps);
  for (final raw in body0) {
    final l = fill(raw.trim());
    if (l.isEmpty) continue;
    if (l.startsWith('## ')) {
      close();
      heading = l.substring(3).trim();
      continue;
    }
    final m = link.firstMatch(l);
    if (m != null) {
      body.add(DocLine(m.group(3)!, lead: m.group(1)!, link: m.group(2)!));
    } else if (l.length > 2 && l.startsWith('`') && l.endsWith('`')) {
      body.add(DocLine(l.substring(1, l.length - 1), mono: true));
    } else if (l.length > 4 && l.startsWith('**') && l.endsWith('**')) {
      body.add(DocLine(l.substring(2, l.length - 2), emphasis: true));
    } else if (l.startsWith('> ')) {
      body.add(DocLine(l.substring(2), quote: true));
    } else {
      body.add(DocLine(l, rich: inline.hasMatch(l)));
    }
  }
  close();
  return InboxDocument(
    kind: kind,
    number: number,
    title: fill(meta['title'] ?? ''),
    at: null,
    from: from,
    status: meta['status'] ?? '',
    header: const [],
    sections: sections,
    hot: meta['hot'] == 'true',
  );
}

/// A small count as a word, the way it reads in a sentence; digits past ten.
String _word(int n) => switch (n) {
  0 => 'no',
  1 => 'one',
  2 => 'two',
  3 => 'three',
  4 => 'four',
  5 => 'five',
  6 => 'six',
  7 => 'seven',
  8 => 'eight',
  9 => 'nine',
  10 => 'ten',
  _ => '$n',
};
