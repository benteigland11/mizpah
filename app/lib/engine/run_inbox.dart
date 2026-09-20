import 'dart:convert';
import 'dart:io';

import '../models/document.dart';

/// Reads a finished or running mizpah run off disk and turns it into the
/// day's paperwork. Arrival order is the cycle order (route → task → eval
/// → task → eval …); route.json stamps tasks, brief.json stamps change
/// requests, evals take the close time of the task they follow.
class RunInbox {
  RunInbox({required this.project, required this.session});

  /// The project root (holds `.terra/`).
  final Directory project;

  /// The session root (holds `loop.json`, `tasks/`, `report.md`).
  final Directory session;

  Map<String, dynamic>? _json(File f) {
    if (!f.existsSync()) return null;
    try {
      return jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  File _terra(String rel) => File('${project.path}/.terra/$rel');

  Map<String, dynamic>? _known(String id) => _json(_terra('map/knowns/$id.json'));
  Map<String, dynamic>? _unknown(String id) =>
      _json(_terra('map/unknowns/$id.json'));

  /// The paperwork, stored beside the run once built. The fingerprint is
  /// the state files' mtimes and sizes: a run that has not moved reads
  /// back in one small file; one that has is rebuilt and re-stored. The
  /// engine may write this same file itself; the app will not know.
  File get _store => File('${session.path}/inbox.json');

  Map<String, String> _crew = const {};
  String _from(String role, String key) {
    final m = _crew[key];
    return m == null || m.isEmpty ? role : '$role · $m';
  }

  /// Bump when the paperwork's shape or wording changes: every stored
  /// inbox.json then rebuilds on its next read instead of by hand.
  static const builderVersion = 4;

  String _fingerprint() {
    final parts = <String>['v$builderVersion'];
    final results = <File>[];
    final tasks = Directory('${session.path}/tasks');
    if (tasks.existsSync()) {
      for (final d in tasks.listSync()) {
        if (d is Directory) results.add(File('${d.path}/result.json'));
      }
    }
    for (final f in [
      File('${session.path}/controller.jsonl'),
      File('${session.path}/loop.json'),
      File('${session.path}/run.json'),
      _terra('route.json'),
      _terra('brief.json'),
      ...results,
    ]) {
      if (!f.existsSync()) {
        parts.add('-');
        continue;
      }
      final st = f.statSync();
      parts.add('${st.modified.millisecondsSinceEpoch}:${st.size}');
    }
    return parts.join('|');
  }

  /// Who is on the job, as run.json records it: `{controller: label,
  /// worker: label}`. Older runs have only report.md's "model X" for the
  /// worker.
  Map<String, String> crew() {
    final rj = _json(File('${session.path}/run.json'));
    final c = (rj?['crew'] as Map?)?.cast<String, dynamic>();
    String label(Map<String, dynamic>? role) {
      if (role == null) return '';
      // The model's name and nothing else: no provider prefix, no address.
      final model = role['model'] as String?;
      if (model != null && model.isNotEmpty) return model;
      final label = role['label'] as String? ?? '';
      if (label.startsWith('http')) return 'local model';
      return label.split('/').last;
    }
    if (c != null) {
      return {
        'controller': label((c['controller'] as Map?)?.cast<String, dynamic>()),
        'worker': label((c['worker'] as Map?)?.cast<String, dynamic>()),
      };
    }
    final rep = File('${session.path}/report.md');
    if (rep.existsSync()) {
      final m = RegExp(r'· model (\S+)').firstMatch(rep.readAsStringSync());
      if (m != null) {
        final raw = m.group(1)!;
        final w = raw.startsWith('http') ? 'local model' : raw.split('/').last;
        return {'controller': w, 'worker': w};
      }
    }
    return const {};
  }

  List<InboxDocument> read() {
    final fp = _fingerprint();
    final cached = _json(_store);
    if (cached != null && cached['fingerprint'] == fp) {
      return [
        for (final d in (cached['documents'] as List? ?? const []))
          InboxDocument.fromJson((d as Map).cast<String, dynamic>()),
      ];
    }
    final docs = build();
    try {
      _store.writeAsStringSync(
        const JsonEncoder.withIndent(' ').convert({
          'fingerprint': fp,
          'built_at': DateTime.now().toUtc().toIso8601String(),
          'documents': [for (final d in docs) d.toJson()],
        }),
      );
    } catch (_) {
      // A read-only run root still reads; it just rebuilds next time.
    }
    return docs;
  }

  /// Build the paperwork from the run's state files. The controller's
  /// journal and the route exist from the first turn; loop.json only once
  /// a cycle has closed, so nothing here depends on it except the ending.
  List<InboxDocument> build() {
    final route = _json(_terra('route.json'));
    final brief = _json(_terra('brief.json'));
    if (route == null || brief == null) return const [];
    final loop = _json(File('${session.path}/loop.json')) ?? const <String, dynamic>{};

    // Controller decisions in order: the opening route, then one eval per
    // closed work order.
    final journal = <Map<String, dynamic>>[];
    final jf = File('${session.path}/controller.jsonl');
    if (jf.existsSync()) {
      for (final line in jf.readAsLinesSync()) {
        if (line.trim().isEmpty) continue;
        try {
          final e = jsonDecode(line) as Map<String, dynamic>;
          if (e['mode'] == 'route' || e['mode'] == 'eval') journal.add(e);
        } catch (_) {}
      }
    }

    // Work orders: every route task the worker has opened a session on,
    // in the order they were started.
    final tasksDir = Directory('${session.path}/tasks');
    final opened = <String>{};
    if (tasksDir.existsSync()) {
      for (final d in tasksDir.listSync()) {
        if (d is Directory) opened.add(d.uri.pathSegments.where((x) => x.isNotEmpty).last);
      }
    }
    final tasks = [
      for (final t in (route['tasks'] as List? ?? const []))
        if (opened.contains((t as Map)['id'])) t.cast<String, dynamic>(),
    ]..sort((a, b) => (a['started_at'] as String? ?? '').compareTo(b['started_at'] as String? ?? ''));
    final closed = tasks.where((t) => t['status'] != 'in_progress').toList();

    _crew = crew();
    final docs = <InboxDocument>[];
    var nBriefing = 0;
    var ji = 0;
    if (ji < journal.length && journal[ji]['mode'] == 'route') {
      nBriefing++;
      docs.add(_briefing(journal[ji++], n: nBriefing,
          at: DateTime.tryParse(route['created_at'] as String? ?? ''), opening: true));
    }
    for (final t in closed) {
      final at = DateTime.tryParse(t['updated_at'] as String? ?? '');
      docs.add(_workOrder(t, at));
      if (ji < journal.length) {
        nBriefing++;
        docs.add(_briefing(journal[ji++], n: nBriefing, at: at));
      }
    }
    // Decisions with no closed task behind them (a route re-plan mid-cycle).
    while (ji < journal.length) {
      nBriefing++;
      docs.add(_briefing(journal[ji++], n: nBriefing, at: null));
    }
    for (final t in tasks.where((t) => t['status'] == 'in_progress')) {
      docs.add(_workOrder(t, DateTime.tryParse(t['started_at'] as String? ?? '')));
    }

    for (final p in (brief['proposals'] as List? ?? const [])) {
      docs.add(_changeRequest((p as Map).cast<String, dynamic>(), brief));
    }

    final outages = File('${session.path}/outages.jsonl');
    if (outages.existsSync()) {
      for (final line in outages.readAsLinesSync()) {
        if (line.trim().isEmpty) continue;
        final o = jsonDecode(line) as Map<String, dynamic>;
        final at = o['at'] is num
            ? DateTime.fromMillisecondsSinceEpoch(
                ((o['at'] as num) * 1000).round(),
              )
            : null;
        docs.add(
          InboxDocument(
            kind: DocKind.anomaly,
            number: 'outage ${o['outage']}',
            title: 'Model outage ${o['outage']}',
            at: at,
            from: 'Loop ops',
            status: 'OUTAGE',
            hot: true,
            header: const [],
            sections: [
              DocSection('Report', [DocLine(o['error'] as String? ?? '')]),
            ],
          ),
        );
      }
    }
    // When the run ended: run.json says, or the last time loop.json moved.
    final rj = _json(File('${session.path}/run.json'));
    DateTime? ended;
    final endedAt = rj?['ended_at'];
    if (endedAt is num) {
      ended = DateTime.fromMillisecondsSinceEpoch((endedAt * 1000).round());
    } else {
      final lf = File('${session.path}/loop.json');
      if (lf.existsSync()) ended = lf.statSync().modified;
    }
    if (loop['stop'] == 'controller_stalled') {
      docs.add(
        InboxDocument(
          kind: DocKind.anomaly,
          number: 'controller stalled',
          title: 'Controller stalled',
          at: ended,
          from: 'Loop ops',
          status: 'STALLED',
          hot: true,
          header: const [],
          sections: const [
            DocSection('Report', [
              DocLine(
                'The controller produced consecutive briefings applying '
                'nothing; the loop halted. See the last briefings\' '
                'Declined sections.',
              ),
            ]),
          ],
        ),
      );
    }

    final stop = loop['stop'] as String?;
    if (stop != null) {
      final report = File('${session.path}/report.md');
      final lines = report.existsSync()
          ? report.readAsLinesSync().where((l) => l.trim().isNotEmpty)
          : const <String>[];
      docs.add(
        InboxDocument(
          kind: DocKind.closing,
          number: stop.replaceAll('_', ' '),
          title: 'Run stopped: ${stop.replaceAll('_', ' ')}',
          at: ended,
          from: 'Loop ops',
          status: stop == 'nothing_owed' ? 'COMPLETE' : 'STOPPED',
          hot: stop != 'nothing_owed',
          header: [
            ('Work orders', '${loop['tasks_run'] ?? '—'}'),
            ('Hours', '${loop['hours'] ?? '—'}'),
            ('Open change requests', '${loop['open_proposals'] ?? 0}'),
          ],
          sections: [
            DocSection('Report', [
              for (final l in lines)
                if (!l.startsWith('# '))
                  DocLine(
                    l.replaceFirst(RegExp(r'^#+\s*'), '').replaceFirst(
                      RegExp(r'^-\s*'),
                      '',
                    ),
                    emphasis: l.startsWith('#'),
                    mono: l.startsWith('-') && l.contains('`'),
                  ),
            ]),
          ],
        ),
      );
    }
    return docs;
  }

  InboxDocument _briefing(
    Map<String, dynamic> e, {
    required int n,
    required DateTime? at,
    bool opening = false,
  }) {
    final applied = (e['applied'] as Map? ?? const {}).cast<String, dynamic>();
    final refused = (e['refused'] as List? ?? const []).cast<String>();
    final done = e['done'] == true;
    List<String> ids(String k) => (applied[k] as List? ?? const []).cast<String>();
    final acts = <DocLine>[
      if (ids('unknowns').isNotEmpty)
        DocLine(
          ids('unknowns').join(', '),
          lead: 'minted ${ids('unknowns').length} unknowns',
          mono: true,
        ),
      if (ids('tasks').isNotEmpty)
        DocLine(ids('tasks').join(', '), lead: 'routed work orders', mono: true),
      if ((applied['proposals'] as List? ?? const []).isNotEmpty)
        DocLine(
          '${(applied['proposals'] as List).length} change request(s) filed',
          lead: 'filed',
        ),
      if ((applied['rebucket'] as List? ?? const []).isNotEmpty)
        DocLine('${applied['rebucket']}', lead: 're-classed'),
      if ((applied['retype'] as List? ?? const []).isNotEmpty)
        DocLine((applied['retype'] as List).join(', '), lead: 'retyped', mono: true),
      if ((applied['unblock'] as List? ?? const []).isNotEmpty)
        DocLine((applied['unblock'] as List).join(', '), lead: 'released', mono: true),
    ];
    // What the worker said when it blocked: the statement this eval is
    // answering. Without it the briefing reads as if the block were ignored
    // (logo_mark8 #007 split a contrast unknown five ways on the worker's
    // word and showed only "minted 5 unknowns").
    final blocked = <DocLine>[
      for (final t in ((e['observation'] as Map?)?['tasks'] as List? ?? const []))
        if ((t as Map)['status'] == 'blocked' && ((t['blocked_reason'] as String?) ?? '').isNotEmpty)
          DocLine(_clip(t['blocked_reason'] as String, 400), lead: t['id'] as String, quote: true),
    ];
    // GO once the map answers the brief. Otherwise the work simply goes on
    // — IN WORK, a neutral stamp. Applying nothing is normal while routed
    // work orders are still open ("nothing new owed"); it is NO ACTION,
    // amber, only when nothing was applied AND nothing is routed to run —
    // which is how a stall starts.
    final obsTasks = (e['observation'] as Map?)?['tasks'] as List? ?? const [];
    final routedOpen = obsTasks.any((t) {
      final st = (t as Map)['status'];
      return st == 'ready' || st == 'in_progress';
    });
    final idle = !done && !opening && acts.isEmpty && !routedOpen;
    final status = opening
        ? 'OPENING'
        : done
        ? 'GO'
        : idle
        ? 'NO ACTION'
        : 'IN WORK';
    return InboxDocument(
      kind: DocKind.briefing,
      number: '#${n.toString().padLeft(3, '0')}',
      title: e['why'] as String? ?? '',
      at: at,
      from: _from('Project Eval', 'controller'),
      status: status,
      hot: false, // a watch item, not trouble; the stamp colour says so
      header: [
        (
          'Readiness',
          opening
              ? 'Opening — route the first work'
              : done
              ? 'GO — the map answers the brief'
              : idle
              ? 'In work — nothing applied and nothing routed'
              : acts.isEmpty
              ? 'In work — nothing new owed; routed work orders run on'
              : 'In work — the brief is not yet answered',
        ),
      ],
      sections: [
        DocSection('Situation', [DocLine(e['why'] as String? ?? '')]),
        if (blocked.isNotEmpty) DocSection('Worker reports', blocked, count: blocked.length),
        DocSection(
          'Actions taken',
          acts.isEmpty ? const [DocLine('None — nothing new owed.')] : acts,
        ),
        if (refused.isNotEmpty)
          DocSection('Declined', _grouped(refused), count: refused.length),
      ],
    );
  }

  /// Refusals share a handful of shapes; group by the text after the
  /// subject so 13 lines read as three reasons.
  List<DocLine> _grouped(List<String> refused) {
    final groups = <String, List<String>>{};
    for (final r in refused) {
      final m = RegExp(r'^(unknown|task) (\S+): (.*)$').firstMatch(r);
      if (m == null) {
        groups.putIfAbsent(r, () => []).add('');
        continue;
      }
      final reason = m.group(3)!;
      final key = reason.length > 90 ? reason.substring(0, 90) : reason;
      groups.putIfAbsent(key, () => []).add('${m.group(1)} ${m.group(2)}');
    }
    // Same shape as "Actions taken": the reason leads (with its count), the
    // subjects follow in mono — one decline made five times reads as one line.
    final out = <DocLine>[];
    groups.forEach((reason, subjects) {
      final full = refused.firstWhere((r) => r.contains(reason));
      final text = full.contains(': ') ? full.split(': ').skip(1).join(': ') : full;
      final named = subjects.where((s) => s.isNotEmpty).toList();
      out.add(
        named.isEmpty
            ? DocLine(text)
            : DocLine(
                named.join(', '),
                lead: named.length > 1 ? '$text ×${named.length}' : text,
                mono: true,
              ),
      );
    });
    return out;
  }

  /// The folder: every route task as a work order, whether or not a
  /// worker has picked it up. Unopened ones are issued but not started.
  List<InboxDocument> workOrders() {
    final route = _json(_terra('route.json'));
    if (route == null) return const [];
    _crew = crew();
    final tasks = [
      for (final t in (route['tasks'] as List? ?? const [])) (t as Map).cast<String, dynamic>(),
    ];
    return [
      for (final t in tasks)
        _workOrder(
          t,
          DateTime.tryParse((t['updated_at'] ?? t['created_at']) as String? ?? ''),
          opened: Directory('${session.path}/tasks/${t['id']}').existsSync(),
        ),
    ];
  }

  InboxDocument _workOrder(Map<String, dynamic> rt, DateTime? at, {bool opened = true}) {
    final id = rt['id'] as String;
    final res = _json(File('${session.path}/tasks/$id/result.json')) ?? const <String, dynamic>{};
    final status = rt['status'] as String? ?? 'ready';
    final inProgress = opened && (status == 'in_progress' || res.isEmpty);
    final verdict = !opened
        ? status
        : inProgress
        ? 'in_progress'
        : (res['verdict'] as String? ?? status);
    // The worker's verdicts: complete (gate green) · blocked_by_worker ·
    // stopped (from outside) · incomplete (finished with the gate red).
    final stamp = switch (verdict) {
      'complete' || 'done' => 'GATE GREEN',
      'blocked' || 'blocked_by_worker' => 'BLOCKED',
      'error' => 'ABORTED',
      'stopped' => 'STOPPED',
      'incomplete' => 'GATE RED',
      'in_progress' => 'IN PROGRESS',
      'ready' => 'QUEUED',
      'cancelled' => 'CANCELLED',
      _ => verdict.toUpperCase(),
    };
    final bucket = rt['bucket'] as String? ?? '';
    final cls = switch (bucket) {
      'low' => 'IMPLEMENT (low, 3 pts)',
      'medium' => 'VALIDATE (medium, 8 pts)',
      'high' => 'EXPLORE (high, 21 pts)',
      _ => bucket,
    };
    // Turns are charged at task end (result.json). While the task runs the
    // event log is the meter: one `worker_turn` event per turn taken.
    var turns = res['turns'];
    var turnsSoFar = false;
    if (turns == null && inProgress) {
      final events = File('${session.path}/tasks/$id/events/session.jsonl');
      if (events.existsSync()) {
        turns = _eventCounts(events)['worker_turn'] ?? 0;
        turnsSoFar = true;
      }
    }
    final criteria = <String>[
      for (final a in (rt['acceptance'] as List? ?? const [])) (a as String).split(':').last,
    ];
    if (criteria.isEmpty && rt['map_id'] is String) criteria.add(rt['map_id'] as String);

    final evidence = (rt['evidence'] as List? ?? const []);
    final closeOut = <DocLine>[];
    if (evidence.isNotEmpty) {
      final e = (evidence.last as Map).cast<String, dynamic>();
      for (final k in (e['knowns'] as List? ?? const []).cast<String>()) {
        final kn = _known(k);
        if (kn == null) {
          closeOut.add(DocLine('', lead: k, mono: true));
          continue;
        }
        final st = (kn['stats'] as Map? ?? const {});
        closeOut.add(DocLine(
          '= ${kn['value']} ${kn['unit'] ?? ''}  (${kn['type']}, n=${st['n']}, ${kn['confidence']})',
          lead: k, mono: true,
        ));
      }
      final runs = (e['runs'] as List? ?? const []).cast<String>();
      if (runs.isNotEmpty) closeOut.add(DocLine(runs.join(', '), lead: 'evidence runs', mono: true));
    } else if (inProgress) {
      closeOut.add(const DocLine('Open — the worker is on it.'));
    } else if (!opened) {
      closeOut.add(DocLine(
        status == 'blocked'
            ? 'Issued; blocked before any worker took it.'
            : status == 'cancelled'
            ? 'Cancelled before any worker took it.'
            : 'Issued; waiting for a worker.',
      ));
    } else {
      closeOut.add(const DocLine('No evidence filed.', emphasis: true));
    }
    final blocked = res['blocked_reason'] ?? rt['blocked_reason'];
    if (blocked != null) closeOut.add(DocLine('$blocked', lead: 'blocked', emphasis: true));
    if (verdict == 'incomplete') {
      final redRounds = (res['rounds'] as List? ?? const [])
          .where((r) => (r as Map)['gate'] == false)
          .length;
      closeOut.add(DocLine(
        'The worker finished but the gate refused its readings'
        '${redRounds > 0 ? ' $redRounds time(s)' : ''}; it did not say it was stuck. '
        'The refusals are under Anomalies; the next briefing routes what follows.',
        lead: 'gate red at close',
        emphasis: true,
      ));
    }

    final rounds = (res['rounds'] as List? ?? const []);
    final stamps = <DocLine>[];
    String? note;
    for (final r in rounds) {
      final rd = (r as Map).cast<String, dynamic>();
      final g = rd['gate'];
      final word = switch (g) {
        'effort' => 'EFFORT PAUSE',
        false => 'GATE RED',
        true => 'GATE GREEN',
        'playbook' => 'METHOD FILED',
        _ => '$g',
      };
      final problems = (rd['problems'] as List? ?? const []);
      stamps.add(DocLine(
        problems.isNotEmpty ? _clip(problems.first as String, 160) : '',
        lead: 'turn ${rd['turns']}  $word', emphasis: g == false,
      ));
      if (g != 'playbook' && (rd['final_text'] as String? ?? '').isNotEmpty) note = rd['final_text'] as String;
    }

    final pb = (res['playbook'] as Map? ?? const {}).cast<String, dynamic>();
    final wg = (res['widgets'] as Map? ?? const {}).cast<String, dynamic>();
    List<String> l(Map<String, dynamic> m, String k) => (m[k] as List? ?? const []).cast<String>();
    final method = <DocLine>[
      if (l(pb, 'installed').isNotEmpty) DocLine(l(pb, 'installed').join(', '), lead: 'procedure filed', mono: true),
      if (l(pb, 'ignored').isNotEmpty) DocLine(l(pb, 'ignored').join(', '), lead: 'procedure drafted, not accepted', mono: true),
      if (l(wg, 'checked_in').isNotEmpty) DocLine(l(wg, 'checked_in').join(', '), lead: 'widget checked in', mono: true),
    ];

    final anomalies = <DocLine>[];
    if ((res['overruns'] as int? ?? 0) > 0) anomalies.add(DocLine('${res['overruns']} effort overrun(s)'));
    if ((res['handoffs'] as int? ?? 0) > 0) anomalies.add(DocLine('${res['handoffs']} context handoff(s)'));
    final counts = (res['events'] as Map?)?.cast<String, dynamic>();
    if (counts != null) {
      // The engine tallied the event log at task end; nothing to scan.
      for (final k in const ['tool_rejected', 'tool_repeated_call', 'interjected']) {
        if ((counts[k] as int? ?? 0) > 0) anomalies.add(DocLine('${counts[k]} ${k.replaceAll('_', ' ')}'));
      }
    } else if (!inProgress) {
      final events = File('${session.path}/tasks/$id/events/session.jsonl');
      if (events.existsSync()) {
        final c = _eventCounts(events);
        for (final k in const ['tool_rejected', 'tool_repeated_call', 'interjected']) {
          if ((c[k] ?? 0) > 0) anomalies.add(DocLine('${c[k]} ${k.replaceAll('_', ' ')}'));
        }
      }
    }
    for (final p in (res['problems'] as List? ?? const [])) {
      anomalies.add(DocLine(_clip(p as String, 160)));
    }
    if (verdict == 'error') anomalies.add(const DocLine('Session ended in error.', emphasis: true));

    return InboxDocument(
      kind: DocKind.workOrder,
      number: id,
      title: rt['title'] as String? ?? id,
      at: at,
      from: _from('Worker', 'worker'),
      status: stamp,
      hot: opened ? (!inProgress && verdict != 'complete') : status == 'blocked',
      header: [
        ('Effort class', cls),
        if (!opened && (rt['priority'] as String? ?? '').isNotEmpty)
          ('Priority', rt['priority'] as String),
        (
          'Hours charged',
          turnsSoFar
              ? '$turns turns so far'
              : '${turns ?? '—'} turns of ${res['turn_budget'] ?? '—'} (estimate ${res['turn_estimate'] ?? '—'})',
        ),
        if ((rt['deps'] as List? ?? const []).isNotEmpty) ('Depends on', (rt['deps'] as List).join(', ')),
      ],
      sections: [
        DocSection('Acceptance criteria', [
          for (final u in criteria) DocLine(_unknown(u)?['claim'] as String? ?? '', lead: u),
        ]),
        DocSection('Close-out', closeOut),
        if (stamps.isNotEmpty) DocSection('Stamps', stamps),
        if (note != null) DocSection("Worker's note", [DocLine(note, quote: true)]),
        if (method.isNotEmpty) DocSection('Method', method),
        if (anomalies.isNotEmpty) DocSection('Anomalies', anomalies, count: anomalies.length),
      ],
    );
  }

  InboxDocument _changeRequest(Map<String, dynamic> p, Map<String, dynamic> brief) {
    final summary = p['summary'] as String? ?? '';
    final parts = summary.split(' — evidence: ');
    final patch = (p['patch'] as Map? ?? const {}).cast<String, dynamic>();
    final status = (p['status'] as String? ?? 'open').toUpperCase();
    final version = brief['version'] as int? ?? 1;
    return InboxDocument(
      kind: DocKind.changeRequest,
      number: p['id'] as String,
      title: parts.first,
      at: DateTime.tryParse(p['created_at'] as String? ?? ''),
      from: _from('Project Eval', 'controller'),
      status: status,
      awaitingSignature: status == 'OPEN',
      proposalId: p['id'] as String,
      patch: patch,
      header: [('Against', 'brief v$version')],
      sections: [
        DocSection('Finding', [DocLine(parts.first)]),
        if (parts.length > 1) DocSection('Evidence', [DocLine(parts[1])]),
        DocSection('Proposed amendment', [
          for (final e in patch.entries)
            DocLine('${e.value}', lead: e.key.replaceAll('_', ' ')),
        ]),
        if (status != 'OPEN')
          DocSection('Decision', [
            DocLine(status, emphasis: status == 'REJECTED'),
            if ((p['decision_reason'] as String? ?? '').isNotEmpty)
              DocLine(p['decision_reason'] as String, quote: true),
          ]),
      ],
    );
  }

  static String _clip(String s, int n) => s.length > n ? '${s.substring(0, n)}…' : s;

  /// Event logs run to hundreds of megabytes (every checkpoint carries the
  /// session). Scan for the type marker in chunks; never hold the file.
  static Map<String, int> _eventCounts(File f) {
    const marker = '"event_type": "';
    final counts = <String, int>{};
    final raf = f.openSync();
    try {
      var carry = '';
      while (true) {
        final bytes = raf.readSync(1 << 20);
        if (bytes.isEmpty) break;
        final text = carry + String.fromCharCodes(bytes);
        var from = 0;
        while (true) {
          final i = text.indexOf(marker, from);
          if (i < 0) break;
          final s = i + marker.length;
          final e = text.indexOf('"', s);
          if (e < 0) break;
          final kind = text.substring(s, e);
          counts[kind] = (counts[kind] ?? 0) + 1;
          from = e + 1;
        }
        carry = text.length > 64 ? text.substring(text.length - 64) : text;
      }
    } finally {
      raf.closeSync();
    }
    return counts;
  }
}
