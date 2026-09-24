import 'dart:convert';
import 'dart:io';

import '../models/databook.dart';
import 'state_dir.dart';

/// Reads a project's map and pivots it by its brief. A known or unknown
/// cites the brief in `notes` ("cites need:10", "cites deliverable:1 |
/// need:5"); the first cite is primary and the row it lands on.
class RunDataBook {
  RunDataBook(this.project);
  final Directory project;

  File _terra(String rel) => File('${stateDir(project.path)}/$rel');

  List<Map<String, dynamic>> _all(String dir, {String? map}) {
    final base = map == null || map == 'global'
        ? mapRoot(project.path)
        : '${stateDir(project.path)}/map/sessions/$map';
    final d = Directory('$base/$dir');
    if (!d.existsSync()) return const [];
    final out = <Map<String, dynamic>>[];
    for (final f in d.listSync()) {
      if (f is! File || !f.path.endsWith('.json')) continue;
      try {
        out.add(jsonDecode(f.readAsStringSync()) as Map<String, dynamic>);
      } catch (_) {}
    }
    return out;
  }

  static final _cite = RegExp(r'(need|deliverable):(\d+)');

  /// Every (kind, index) a note cites; the first is primary.
  static List<(String, int)> cites(String? notes) => [
    for (final m in _cite.allMatches(notes ?? ''))
      (m.group(1)!, int.parse(m.group(2)!)),
  ];

  DataBook read() {
    Map<String, dynamic>? brief;
    try {
      brief = jsonDecode(_terra('brief.json').readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {
      return DataBook.empty;
    }
    final needs = (brief['needs'] as List? ?? const []).cast<String>();
    final deliverables = (brief['deliverables'] as List? ?? const []).cast<String>();

    final answers = <(String, int), List<KnownReport>>{};
    final open = <(String, int), List<OpenUnknown>>{};
    final orphans = <KnownReport>[];

    for (final k in _all('knowns')) {
      if (k['status'] == 'superseded') continue;
      final r = _known(k);
      final c = cites(k['notes'] as String?);
      if (c.isEmpty) {
        orphans.add(r);
      } else {
        answers.putIfAbsent(c.first, () => []).add(r);
      }
    }
    for (final u in _all('unknowns')) {
      if (u['resolved_by'] != null) continue;
      final c = cites(u['notes'] as String?);
      if (c.isEmpty) continue;
      open.putIfAbsent(c.first, () => []).add(
        OpenUnknown(
          id: u['id'] as String? ?? '',
          claim: u['claim'] as String? ?? '',
          evidenceNeeded: u['evidence_needed'] as String? ?? '',
          status: u['status'] as String? ?? 'open',
        ),
      );
    }

    DataBookEntry row(String kind, int i, String text) => DataBookEntry(
      kind: kind,
      index: i,
      text: text,
      answers: answers[(kind, i)] ?? const [],
      open: open[(kind, i)] ?? const [],
    );
    return DataBook(
      entries: [
        for (var i = 0; i < needs.length; i++) row('need', i + 1, needs[i]),
        for (var i = 0; i < deliverables.length; i++)
          row('deliverable', i + 1, deliverables[i]),
      ],
      orphans: orphans,
      maps: _tree(),
    );
  }

  int _count(String base, String dir) {
    final d = Directory('$base/$dir');
    if (!d.existsSync()) return 0;
    return d.listSync().where((e) => e is Directory || e.path.endsWith('.json')).length;
  }

  Map<String, dynamic>? _meta(String base) {
    try {
      return jsonDecode(File('$base/map.json').readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  /// Global first, then every task map under `sessions/`. A task-map known
  /// is adopted when global holds a known of the same id whose
  /// `adopted_from.map` names this map.
  List<MapNode> _tree() {
    final base = '${stateDir(project.path)}/map';
    final globalKnowns = <String, Map<String, dynamic>>{
      for (final k in _all('knowns')) k['id'] as String: k,
    };
    final nodes = <MapNode>[];
    final gm = _meta(base);
    nodes.add(
      MapNode(
        id: 'global',
        kind: 'global',
        parent: null,
        purpose: gm?['purpose'] as String? ?? '',
        knowns: [
          for (final k in globalKnowns.values)
            MapKnown(
              report: _known(k),
              adopted: true,
              from: ((k['adopted_from'] as Map?) ?? const {})['map'] as String?,
            ),
        ],
        runs: _count(base, 'runs'),
        openUnknowns: _all('unknowns').where((u) => u['resolved_by'] == null).length,
      ),
    );
    final sessions = Directory('$base/sessions');
    if (!sessions.existsSync()) return nodes;
    final dirs = sessions.listSync().whereType<Directory>().toList()
      ..sort((a, b) => a.path.compareTo(b.path));
    for (final d in dirs) {
      final id = d.uri.pathSegments.where((s) => s.isNotEmpty).last;
      final m = _meta(d.path);
      nodes.add(
        MapNode(
          id: id,
          kind: m?['kind'] as String? ?? 'session',
          parent: m?['parent'] as String? ?? 'global',
          purpose: m?['purpose'] as String? ?? '',
          knowns: [
            for (final k in _all('knowns', map: id))
              MapKnown(
                report: _known(k),
                adopted: (((globalKnowns[k['id']]?['adopted_from'] as Map?) ??
                        const {})['map'] as String?) ==
                    id,
              ),
          ],
          runs: _count(d.path, 'runs'),
          openUnknowns: _all('unknowns', map: id)
              .where((u) => u['resolved_by'] == null)
              .length,
        ),
      );
    }
    return nodes;
  }

  KnownReport _known(Map<String, dynamic> k) {
    final st = (k['stats'] as Map? ?? const {}).cast<String, dynamic>();
    final corr = (st['corroboration'] as Map? ?? const {});
    final v = k['value'];
    String value;
    if (v is bool) {
      value = v ? 'true' : 'false';
    } else if (v is num && k['type'] == 'boolean') {
      value = v != 0 ? 'true' : 'false';
    } else if (v is double) {
      value = v == v.roundToDouble() && v.abs() < 1e15
          ? v.toStringAsFixed(0)
          : v.toStringAsPrecision(6).replaceFirst(RegExp(r'\.?0+$'), '');
    } else {
      value = '$v';
    }
    return KnownReport(
      id: k['id'] as String? ?? '',
      claim: k['claim'] as String? ?? '',
      value: value,
      unit: k['unit'] as String? ?? '',
      type: k['type'] as String? ?? '',
      confidence: k['confidence'] as String? ?? '',
      n: (st['n'] as num?)?.toInt() ?? 0,
      agreement: (st['agreement'] as num?)?.toDouble(),
      methods: (corr['methods'] as num?)?.toInt() ?? 0,
      probes: (k['probe_ids'] as List? ?? const []).cast<String>(),
      runs: (k['run_ids'] as List? ?? const []).cast<String>(),
      adoptedFrom: ((k['adopted_from'] as Map?) ?? const {})['map'] as String?,
      at: DateTime.tryParse(k['updated_at'] as String? ?? ''),
    );
  }
}
