import 'dart:convert';
import 'dart:io';

import '../models/procedure.dart';

/// Reads the playbook's global store (one JSON per procedure) and, from the
/// runs it is told about, who minted each procedure and who has used it.
class ProcedureStore {
  ProcedureStore(this.dir);
  final Directory dir;

  static const ledgerName = '.library.json';

  /// The ledger beside the procedures, as the playbook wrote it.
  Map<String, dynamic> ledger() {
    try {
      return jsonDecode(File('${dir.path}/$ledgerName').readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {
      return const {};
    }
  }

  LibraryPolicy policy() {
    final l = ledger();
    final p = (l['policy'] as Map?)?.cast<String, dynamic>() ?? const {};
    return LibraryPolicy(
      enabled: p['enabled'] == true,
      grace: (p['grace'] as num?)?.toInt() ?? 50,
      clock: (l['clock'] as num?)?.toInt() ?? 0,
      weights: ((p['weights'] as Map?)?.cast<String, dynamic>() ?? const {}).map((k, v) => MapEntry(k, (v as num).toInt())),
    );
  }

  /// One procedure's standing, computed as the ledger would (grace left
  /// from the latest touch of each kind).
  static LibraryStanding standingOf(Map<String, dynamic> ledger, String id) {
    final items = (ledger['items'] as Map?)?.cast<String, dynamic>() ?? const {};
    final e = (items[id] as Map?)?.cast<String, dynamic>();
    final p = (ledger['policy'] as Map?)?.cast<String, dynamic>() ?? const {};
    final protectedPrefixes = ((p['protected'] as List?) ?? const []).cast<String>();
    final protected = protectedPrefixes.any(id.startsWith);
    if (e == null) return LibraryStanding(protected: protected);
    final touched = ((e['touched'] as Map?)?.cast<String, dynamic>() ?? const {}).map((k, v) => MapEntry(k, (v as num).toInt()));
    final retired = e['status'] == 'retired';
    final pinned = e['pinned'] == true;
    final enabled = p['enabled'] == true;
    int? graceLeft;
    if (enabled && !pinned && !protected && !retired) {
      final grace = (p['grace'] as num?)?.toInt() ?? 50;
      final weights = ((p['weights'] as Map?)?.cast<String, dynamic>() ?? const {});
      final clock = (ledger['clock'] as num?)?.toInt() ?? 0;
      var expiry = -1;
      for (final t in touched.entries) {
        final w = (weights[t.key] as num?)?.toInt() ?? 0;
        if (t.value + w * grace > expiry) expiry = t.value + w * grace;
      }
      graceLeft = expiry < 0 ? 0 : (expiry - clock).clamp(0, 1 << 30);
    }
    final r = (e['retired'] as Map?)?.cast<String, dynamic>();
    return LibraryStanding(
      retired: retired,
      pinned: pinned,
      protected: protected,
      graceLeft: graceLeft,
      retiredAt: (r?['at'] as num?)?.toInt(),
      retiredReason: r?['reason'] as String?,
      touched: touched,
    );
  }

  List<Procedure> read({Map<String, ProcedureOrigin> mintedBy = const {}, Map<String, List<ProcedureOrigin>> usedBy = const {}}) {
    if (!dir.existsSync()) return const [];
    final out = <Procedure>[];
    final book = ledger();
    for (final f in dir.listSync()) {
      if (f is! File || !f.path.endsWith('.json') || f.uri.pathSegments.last.startsWith('.')) continue;
      Map<String, dynamic> j;
      try {
        j = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
      } catch (_) {
        continue;
      }
      final id = j['id'] as String? ?? f.uri.pathSegments.last.replaceFirst('.json', '');
      out.add(Procedure(
        id: id,
        title: j['title'] as String? ?? id,
        description: j['description'] as String? ?? '',
        tags: (j['tags'] as List? ?? const []).cast<String>(),
        steps: [
          for (final s in (j['steps'] as List? ?? const []))
            ProcedureStep(
              id: (s as Map)['id'] as String? ?? '',
              title: s['title'] as String? ?? '',
              do_: s['do'] as String? ?? '',
              procedure: s['procedure'] as String?,
            ),
        ],
        updatedAt: f.statSync().modified,
        mintedBy: mintedBy[id],
        usedBy: usedBy[id] ?? const [],
        standing: standingOf(book, id),
      ));
    }
    out.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return out;
  }

  /// Provenance from a run's task results: `playbook.installed` names what
  /// a work order filed; a checklist path in its problems names what it
  /// opened. Returns (mintedBy, usedBy) keyed by procedure id.
  static (Map<String, ProcedureOrigin>, Map<String, List<ProcedureOrigin>>) provenance(
    Iterable<({String taskId, String taskTitle, String session})> runs,
  ) {
    final minted = <String, ProcedureOrigin>{};
    final used = <String, List<ProcedureOrigin>>{};
    final opened = RegExp(r'\.playbook/open/([a-z0-9][a-z0-9\-]*)--');
    for (final r in runs) {
      final tasks = Directory('${r.session}/tasks');
      if (!tasks.existsSync()) continue;
      for (final d in tasks.listSync()) {
        if (d is! Directory) continue;
        final rf = File('${d.path}/result.json');
        if (!rf.existsSync()) continue;
        Map<String, dynamic> res;
        try {
          res = jsonDecode(rf.readAsStringSync()) as Map<String, dynamic>;
        } catch (_) {
          continue;
        }
        final wo = d.uri.pathSegments.where((s) => s.isNotEmpty).last;
        final origin = ProcedureOrigin(taskId: r.taskId, taskTitle: r.taskTitle, workOrder: wo, at: rf.statSync().modified);
        final pb = (res['playbook'] as Map? ?? const {});
        for (final id in (pb['installed'] as List? ?? const []).cast<String>()) {
          final was = minted[id];
          if (was == null || (origin.at != null && was.at != null && origin.at!.isBefore(was.at!))) minted[id] = origin;
        }
        final seen = <String>{};
        for (final round in (res['rounds'] as List? ?? const [])) {
          for (final p in ((round as Map)['problems'] as List? ?? const [])) {
            for (final m in opened.allMatches('$p')) {
              final id = m.group(1)!;
              if (seen.add(id)) used.putIfAbsent(id, () => []).add(origin);
            }
          }
        }
        for (final id in (pb['ignored'] as List? ?? const []).cast<String>()) {
          if (seen.add(id)) used.putIfAbsent(id, () => []).add(origin);
        }
      }
    }
    return (minted, used);
  }
}
