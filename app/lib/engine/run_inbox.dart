import 'dart:convert';
import 'dart:io';

import '../models/document.dart';
import 'run_databook.dart';
import 'state_dir.dart';

/// Reads a finished or running mizpah run off disk and turns it into the
/// day's paperwork. Arrival order is the cycle order (route → task → eval
/// → task → eval …); route.json stamps tasks, brief.json stamps change
/// requests, evals take the close time of the task they follow.
class RunInbox {
  RunInbox({required this.project, required this.session});

  /// Whether this session's loop is running now: run.json holds no stop or
  /// end and its pid is alive. The authority for "still being worked".
  bool get _loopLive {
    final rj = _json(File('${session.path}/run.json'));
    return rj != null && rj['stop'] == null && rj['ended_at'] == null &&
        rj['pid'] is int && Directory('/proc/${rj['pid']}').existsSync();
  }

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

  File _terra(String rel) => File('${stateDir(project.path)}/$rel');

  Map<String, dynamic>? _known(String id) => _json(File('${mapRoot(project.path)}/knowns/$id.json'));
  Map<String, dynamic>? _unknown(String id) => _json(File('${mapRoot(project.path)}/unknowns/$id.json'));

  /// The paperwork, stored beside the run once built. The fingerprint is
  /// the state files' mtimes and sizes: a run that has not moved reads
  /// back in one small file; one that has is rebuilt and re-stored. The
  /// engine may write this same file itself; the app will not know.
  File get _store => File('${session.path}/inbox.json');

  Map<String, String> _crew = const {};
  /// Files the brief's deliverables name, as the engine's ledger reads them:
  /// backticked names or path-like tokens with an extension.
  List<List<String>> _deliverableRows(Map<String, dynamic> brief) {
    final rows = <List<String>>[];
    final seen = <String>{};
    final builders = <String, String>{};
    for (final f in Directory('${mapRoot(project.path)}/unknowns').existsSync()
        ? Directory('${mapRoot(project.path)}/unknowns').listSync()
        : const <FileSystemEntity>[]) {
      final u = f is File && f.path.endsWith('.json') ? _json(f) : null;
      final notes = u?['notes'] as String? ?? '';
      final m = RegExp(r'creates ([^;]+)').firstMatch(notes);
      if (m != null) builders[m.group(1)!.trim().toLowerCase()] = u!['id'] as String;
    }
    for (final text in (brief['deliverables'] as List? ?? const [])) {
      for (final m in RegExp(r'`([^`]+)`|([\w./-]+\.[A-Za-z0-9]{1,5})').allMatches('$text')) {
        final name = (m.group(1) ?? m.group(2) ?? '').trim();
        if (name.isEmpty || name.contains(' ') || !name.split('/').last.contains('.') || !seen.add(name)) continue;
        final path = name.contains('<') ? null : File('${project.path}/$name');
        final exists = path != null && path.existsSync();
        final builder = builders.entries.where((e) => e.key == name.toLowerCase() || e.key.endsWith('/${name.toLowerCase()}')).map((e) => e.value).firstOrNull;
        rows.add([
          name,
          exists ? _bytes(path.lengthSync()) : (name.contains('<') ? 'per candidate' : 'missing'),
          exists ? _when(path.lastModifiedSync()) : '-',
          builder ?? '-',
        ]);
      }
    }
    return rows;
  }

  static String _when(DateTime d) {
    final l = d.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${l.year}-${two(l.month)}-${two(l.day)} ${two(l.hour)}:${two(l.minute)}';
  }

  static String _bytes(int n) => n >= 1048576 ? '${(n / 1048576).toStringAsFixed(1)} MB' : n >= 1024 ? '${(n / 1024).toStringAsFixed(1)} KB' : '$n B';

  /// One row per need: the known answering it (value, confidence, runs), or
  /// what is still open. Read through the data book's pivot.
  List<List<String>> _requirementRows(Map<String, dynamic> brief) {
    final book = RunDataBook(project).read();
    final rows = <List<String>>[];
    for (final e in book.entries.where((e) => e.kind == 'need')) {
      if (e.answers.isEmpty) {
        rows.add(['${e.index}', e.open.isEmpty ? '-' : e.open.map((u) => u.id).join(', '), e.open.isEmpty ? 'uncovered' : 'open', '', '']);
        continue;
      }
      for (final (i, k) in e.answers.indexed) {
        rows.add([i == 0 ? '${e.index}' : '', k.id, k.unit.isEmpty ? k.value : '${k.value} ${k.unit}', k.confidence, '${k.n}']);
      }
    }
    return rows;
  }

  List<List<String>> _decidedRows(Map<String, dynamic> brief) => [
    for (final p in (brief['proposals'] as List? ?? const []))
      if ((p as Map)['status'] == 'accepted' || p['status'] == 'rejected')
        ['${p['id']}', '${p['status']}', '${p['decision_reason'] ?? ''}'],
  ];

  /// What a work order left in the library, one item per line.
  static String _library(Map t) {
    final pb = (t['playbook'] as Map?) ?? const {};
    final w = (t['widgets'] as Map?) ?? const {};
    final created = pb['created'] as List? ?? (pb['created'] == null ? (pb['installed'] as List? ?? const []) : const []);
    // What compounds: + new to the library, ↑ an existing one improved.
    return [
      for (final p in created) '+ procedure  $p',
      for (final p in (pb['improved'] as List? ?? const []))
        '↑ procedure  $p${(pb['merged'] as List? ?? const []).contains(p) ? ' (merged onto another task\'s improvement)' : ''}',
      for (final p in (pb['rejected'] as List? ?? const [])) '✗ procedure  $p',
      for (final c in (pb['conflicts'] as List? ?? const []))
        if (c is Map) '⚠ procedure  ${c['id']}  not landed: ${(c['conflicts'] as List? ?? const []).join('; ')}',
      for (final x in (w['checked_in'] as List? ?? const []))
        '+ widget  $x${(w['merged'] as List? ?? const []).contains(x) ? ' (merged onto another task\'s improvement)' : ''}',
      for (final c in (w['conflicts'] as List? ?? const []))
        if (c is Map) '⚠ widget  ${c['id']}  not landed: same lines changed by both (${(c['clashes'] as List? ?? const []).join(', ')})',
      for (final x in (w['rejected'] as List? ?? const [])) '✗ widget  $x (rejected)',
    ].join('\n');
  }

  /// "2 d 3 hr", "1 hr 12 min", "6 min": empty fields left out, not zeroed.
  static String _duration(double? hours) {
    if (hours == null) return '-';
    final minutes = (hours * 60).round();
    final d = minutes ~/ 1440, h = (minutes % 1440) ~/ 60, m = minutes % 60;
    final parts = [if (d > 0) '$d d', if (h > 0) '$h hr', if (m > 0 || (d == 0 && h == 0)) '$m min'];
    return parts.join(' ');
  }

  static String _k(Object? n) {
    final v = (n as num?)?.toInt() ?? 0;
    if (v >= 1000000) return '${(v / 1000000).toStringAsFixed(1)}M';
    if (v >= 1000) return '${(v / 1000).toStringAsFixed(v >= 100000 ? 0 : 1)}K';
    return '$v';
  }

  static String _shortPath(String path) {
    final home = Platform.environment['HOME'];
    return home != null && home.isNotEmpty && path.startsWith(home) ? '~${path.substring(home.length)}' : path;
  }

  String _from(String role, String key) {
    final m = _crew[key];
    return m == null || m.isEmpty ? role : '$role · $m';
  }

  /// Bump when the paperwork's shape or wording changes: every stored
  /// inbox.json then rebuilds on its next read instead of by hand.
  // Bump when the documents change shape: every cached inbox rebuilds.
  static const builderVersion = 22;

  /// What the desk is built from, as one string: a change here means the
  /// paperwork must be rebuilt; the same string means the cache holds.
  /// Cheap: file stats only.
  String fingerprint() {
    final parts = <String>['v$builderVersion'];
    final results = <File>[];
    final tasks = Directory('${session.path}/tasks');
    if (tasks.existsSync()) {
      for (final d in tasks.listSync()) {
        if (d is! Directory) continue;
        final rf = File('${d.path}/result.json');
        results.add(rf);
        // A running task: its journal moves every turn, and so does the
        // work order's turns and tokens; an outage lands beside it.
        if (!rf.existsSync()) {
          results.add(File('${d.path}/events/session.jsonl'));
          results.add(File('${d.path}/outages.jsonl'));
        }
      }
    }
    for (final f in [
      File('${session.path}/controller.jsonl'),
      File('${session.path}/loop.json'),
      File('${session.path}/run.json'),
      File('${session.path}/outages.jsonl'),
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
    final fp = fingerprint();
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
    var loop = _json(File('${session.path}/loop.json')) ?? const <String, dynamic>{};

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
    // Which briefing first routed each work order: a later "already exists"
    // decline points back at it instead of reading as an unexplained refusal.
    _routedIn = {};
    for (var i = 0; i < journal.length; i++) {
      for (final id in ((journal[i]['applied'] as Map?)?['tasks'] as List? ?? const [])) {
        _routedIn.putIfAbsent('$id', () => i + 1);
      }
    }
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
      final n = _routedIn['${t['id']}'];
      docs.add(_workOrder(t, at, routedBy: n == null ? '' : '#${n.toString().padLeft(3, '0')}'));
      if (ji < journal.length) {
        nBriefing++;
        docs.add(_briefing(journal[ji++], n: nBriefing, at: at, after: '${t['id']}'));
      }
    }
    // Decisions with no closed task behind them (a route re-plan mid-cycle).
    while (ji < journal.length) {
      nBriefing++;
      docs.add(_briefing(journal[ji++], n: nBriefing, at: null));
    }
    for (final t in tasks.where((t) => t['status'] == 'in_progress')) {
      final n = _routedIn['${t['id']}'];
      docs.add(_workOrder(t, DateTime.tryParse(t['started_at'] as String? ?? ''),
          routedBy: n == null ? '' : '#${n.toString().padLeft(3, '0')}'));
    }

    // The same ask filed four times is one change request: the open ones
    // that Terra marked `same_as` each other fold under the earliest open
    // one, which lists them; deciding it decides them all (Terra folds).
    final proposals = [for (final p in (brief['proposals'] as List? ?? const [])) (p as Map).cast<String, dynamic>()];
    final byId = {for (final p in proposals) p['id'] as String: p};
    String head(Map<String, dynamic> p) {
      var h = p;
      while (h['status'] == 'open' && byId[h['same_as']]?['status'] == 'open') {
        h = byId[h['same_as'] as String]!;
      }
      return h['id'] as String;
    }
    final heads = {for (final p in proposals) if (p['status'] == 'open') p['id'] as String: head(p)};
    for (final p in proposals) {
      final id = p['id'] as String;
      if (p['status'] == 'open' && heads[id] != id) continue;
      docs.add(_changeRequest(p, brief, alsoAsked: [
        for (final q in proposals) if (q['status'] == 'open' && q['id'] != id && heads[q['id']] == id) q,
      ], askedBefore: p['status'] == 'open' ? byId[p['same_as']] : null));
    }
    // What the person said to this run: SENT until the controller has read
    // it at a briefing, then READ. The briefing that follows is the answer.
    final notes = File('${session.path}/operator.jsonl');
    if (notes.existsSync()) {
      var n = 0;
      for (final line in notes.readAsLinesSync()) {
        if (line.trim().isEmpty) continue;
        Map<String, dynamic> j;
        try {
          j = (jsonDecode(line) as Map).cast<String, dynamic>();
        } catch (_) {
          continue;
        }
        n++;
        final at = (j['at'] as num?)?.toDouble();
        final resumed = j['resume'] == true;
        final to = j['to'] as String?;
        final toWorker = to != null && to.startsWith('worker:');
        docs.add(InboxDocument(
          kind: DocKind.memo,
          number: '#${n.toString().padLeft(3, '0')}',
          title: '',
          at: at == null ? null : DateTime.fromMillisecondsSinceEpoch((at * 1000).round(), isUtc: true),
          from: 'You',
          status: j['read'] == true ? 'READ' : 'SENT',
          header: [
            toWorker ? ('To', 'Worker on ${to.substring(7)}') : const ('To', 'Project Eval'),
            if (toWorker) const ('Delivered', 'at the worker\'s next boundary'),
            if (resumed) const ('Action', 'Resumed the run on this session with this note'),
          ],
          sections: [DocSection('Note', [DocLine(j['text'] as String? ?? '')])],
        ));
      }
    }

    // Whether the run is over, for the outage stamps below: run.json's
    // ended_at, or a stop in loop.json that no live process supersedes.
    final rj = _json(File('${session.path}/run.json'));
    final resumed = rj != null && rj['stop'] == null && rj['ended_at'] == null &&
        (rj['pid'] is int && Directory('/proc/${rj['pid']}').existsSync());
    final runOver = rj?['ended_at'] is num || (loop['stop'] != null && !resumed);

    // The engine writes outages per task (tasks/<id>/outages.jsonl); a
    // session-level file is read too. Only the session file was read before,
    // so no real outage ever became a document.
    final outageFiles = <File>[
      File('${session.path}/outages.jsonl'),
      for (final d in (Directory('${session.path}/tasks').existsSync()
          ? Directory('${session.path}/tasks').listSync()
          : const <FileSystemEntity>[]))
        if (d is Directory) File('${d.path}/outages.jsonl'),
    ];
    for (final outages in outageFiles) {
      if (!outages.existsSync()) continue;
      for (final line in outages.readAsLinesSync()) {
        if (line.trim().isEmpty) continue;
        final o = jsonDecode(line) as Map<String, dynamic>;
        final at = o['at'] is num
            ? DateTime.fromMillisecondsSinceEpoch(
                ((o['at'] as num) * 1000).round(),
              )
            : null;
        // Who hit it and which endpoint did not answer. Records from before
        // the engine wrote a role are the worker's: only the worker wrote any.
        final role = o['role'] as String? ?? 'worker';
        // Records from before the engine named the endpoint: the crew's
        // model for that role is the best the run can say.
        final endpoint = o['endpoint'] as String? ?? _crew[role] ?? '';
        final nth = o['outage'] as int?;
        final who = role == 'controller' ? 'controller' : 'worker';
        // What happened, in words, from the engine's classification; the
        // exception text is kept as the last line for the record.
        final what = o['what'] as String?;
        final kind = o['kind'] as String? ?? 'transport';
        final task = o['task'] as String?;
        final turn = o['turn'] as int?;
        final action = o['action'] as String?;
        // Has it passed? The worker's outage records the turns completed
        // when it hit; a turn completed since, or the task ending, means the
        // model answered again. The controller's briefings carry no clock,
        // so a route task created after the outage is its next answer. A
        // run that ended is over either way. The stamp keeps its word (it
        // happened) and goes grey (it is over); the report stays as written.
        final passed = _outagePassed(role, at, task, turn, runOver: runOver, route: route);
        final status = switch (kind) {
          'reply_too_big' => 'REPLY TOO BIG',
          'no_reply' => 'NO REPLY',
          'server_down' => 'SERVER DOWN',
          'rate_limited' => 'RATE LIMITED',
          _ => 'OUTAGE',
        };
        docs.add(
          InboxDocument(
            kind: DocKind.anomaly,
            number: '',
            title: '',
            at: at,
            from: _from(role == 'controller' ? 'Project Eval' : 'Worker', role),
            status: status,
            hot: !passed,
            past: passed,
            header: [
              if (endpoint.isNotEmpty) ('Endpoint', endpoint),
              if (task != null) ('While on', turn == null ? task : '$task, turn $turn'),
              if (nth != null) ('Outage', '$nth of 5 before the ${role == 'controller' ? 'step' : 'task'} fails'),
              if (passed) ('Passed', runOver ? 'the run has ended' : 'the $who\'s model answered again'),
            ],
            sections: [
              DocSection('Report', [
                DocLine(what == null
                    ? 'The $who\'s model${endpoint.isEmpty ? '' : ', $endpoint,'} did not answer.'
                    : 'The $who\'s model${endpoint.isEmpty ? '' : ', $endpoint'}: $what.'),
                if (action != null) DocLine('What the loop did: $action.'),
                DocLine(o['error'] as String? ?? ''),
              ]),
            ],
          ),
        );
      }
    }
    // When the run ended: run.json says, or the last time loop.json moved.
    DateTime? ended;
    final endedAt = rj?['ended_at'];
    if (endedAt is num) {
      ended = DateTime.fromMillisecondsSinceEpoch((endedAt * 1000).round());
    } else {
      final lf = File('${session.path}/loop.json');
      if (lf.existsSync()) ended = lf.statSync().modified;
    }
    // A run relaunched on its own root (a resume) is live again while
    // loop.json still carries the stop the killed run wrote; that stop is
    // history, not the state. run.json is the authority on liveness: a null
    // stop there with a live process means no stopped notice.
    if (resumed) {
      loop = Map.of(loop)..remove('stop');
    }
    if (loop['stop'] == 'controller_stalled') {
      docs.add(
        InboxDocument(
          kind: DocKind.anomaly,
          number: '',
          title: '',
          at: ended,
          from: _from('Project Eval', 'controller'),
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
      final complete = (stop == 'completed' || stop == 'nothing_owed');
      final usage = (loop['usage'] as List? ?? const []).cast<Map>();
      // A stop on proposals is read against the proposals as they are now:
      // still open, you owe a decision; accepted since, the brief moved and
      // the task wants running again; all rejected, the task has nothing
      // left it may do and needs guidance from you, not a signature.
      final proposals = (brief['proposals'] as List? ?? const []).cast<Map>();
      final openCRs = proposals.where((p) => p['status'] == 'open').length;
      final acceptedCRs = proposals.where((p) => p['status'] == 'accepted').length;
      final needsGuidance = stop == 'proposals_pending' && openCRs == 0 && acceptedCRs == 0 && proposals.isNotEmpty;
      final reason = switch (stop) {
        'completed' || 'nothing_owed' => 'Brief requirements have been met',
        'proposals_pending' when openCRs > 0 => 'Waiting on you: a change request needs a decision',
        'proposals_pending' when acceptedCRs > 0 =>
          'The brief changed after this stop: a change request was accepted. Run the task again to continue',
        'proposals_pending' =>
          'Needs guidance: the brief asks for something this task cannot produce, and the amendments it '
              'proposed were rejected. Change the brief or the inputs, then run it again',
        'blocked' => 'Blocked: a work order could not proceed',
        'deadline' => 'Out of time: the deadline passed',
        'controller_stalled' => 'Stalled: the controller made no progress',
        'stopped_by_operator' => 'Held: stopped from the dial',
        'driver_failing' => 'Stopped: the engine hit repeated errors',
        'interrupted' => 'Interrupted: the loop was killed',
        _ => 'Stopped: ${stop.replaceAll('_', ' ')}',
      };
      docs.add(
        InboxDocument(
          kind: complete ? DocKind.completion : DocKind.closing,
          number: '',
          title: '',
          // The reason in plain words: the stop code is the engine's.
          header: [
            ('Work orders', '${loop['tasks_run'] ?? '-'}'),
            ('Time', _duration((loop['hours'] as num?)?.toDouble())),
            // A completed task has no open change request by definition:
            // an open one is what keeps a task from completing.
            if (openCRs > 0) ('Open change requests', '$openCRs'),
          ],
          at: ended,
          from: '${brief['title'] ?? project.path.split('/').last} · ${_shortPath(project.path)}',
          status: complete
              ? 'COMPLETE'
              : needsGuidance
              ? 'NEEDS GUIDANCE'
              : stop == 'proposals_pending' && openCRs > 0
              ? 'FOR SIGNATURE'
              : 'STOPPED',
          hot: !complete,
          sections: [
            if (usage.isNotEmpty)
              DocSection('Models', [
                for (final u in usage)
                  DocLine(
                    '${_k(u['prompt'])} in · ${_k(u['completion'])} out · ${u['calls']} calls'
                    '${u['cache_share'] != null ? ' · cache ${((u['cache_share'] as num) * 100).round()}%' : ''}',
                    lead: '${u['role']}  ${u['model']}',
                    mono: true,
                  ),
              ]),
            // A stopped task says why, right after the cost: the reason in plain
            // words, then what is blocked or waiting on a person.
            if (!complete)
              DocSection('Why it stopped', [
                DocLine(reason, emphasis: true),
                for (final b in (loop['blocked'] as List? ?? const []))
                  DocLine('${(b as Map)['reason'] ?? ''}', lead: '${b['id']}', quote: true),
                for (final p in (brief['proposals'] as List? ?? const []))
                  if ((p as Map)['status'] == 'open')
                    DocLine('${p['summary']}'.split(' — evidence:').first,
                        lead: '${p['id']}  waiting on your decision', link: 'changeRequest:${p['id']}')
                  // Decided after the loop stopped: the notice is a snapshot; say
                  // what happened since, and that the task wants running again.
                  else if (stop == 'proposals_pending' && (p['status'] == 'accepted' || p['status'] == 'rejected'))
                    DocLine(
                      '${p['decision_reason'] ?? ''}'.isEmpty
                          ? 'Decided since.'
                          : '${p['decision_reason']}',
                      lead: '${p['id']}  ${p['status']} since',
                      link: 'changeRequest:${p['id']}',
                    ),
              ]),
            // What was delivered: every file the brief's deliverables name,
            // on disk or not, with who built it. The reason the task existed.
            DocSection('Deliverables', const [], columns: const ['File', 'Size', 'Modified', 'Built by'],
                rows: _deliverableRows(brief), flexColumn: 0),
            // The evidence: one row per need, the reading that answers it.
            DocSection('Requirements met', const [], columns: const ['Need', 'Reading', 'Value', 'Confidence', 'n'],
                rows: _requirementRows(brief), flexColumn: 1),
            // On a completed task, the change requests decided along the way
            // (the brief it met may not be the brief it started with).
            if (complete && _decidedRows(brief).isNotEmpty)
              DocSection('Change requests decided', const [], columns: const ['CR', 'Decision', 'Reason'],
                  rows: _decidedRows(brief), flexColumn: 2),
            // Each work order: how it ended, what it cost, what it left in the
            // library — one table, one row per order and per artifact.
            DocSection('Work orders', const [], columns: const ['Work order', 'Outcome', 'Turns', 'Minted'], rows: [
              for (final c in (loop['cycles'] as List? ?? const []))
                for (final t in ((c as Map)['tasks'] as List? ?? const []))
                  [
                    '${(t as Map)['task']}',
                    '${t['verdict']}',
                    '${t['turns'] ?? '-'}',
                    _library(t),
                  ],
            ], footnote: '+ added to the library · ↑ an existing one improved · ✗ rejected by the validator'),
          ],
        ),
      );
    }
    return _inArrivalOrder(docs);
  }

  /// The desk is chronological. Briefings and work orders come out of the
  /// journal in cycle order; change requests, memos and outages are gathered
  /// afterwards from their own files, so they must be merged in by time. A
  /// document with no stamp of its own keeps its place after the last
  /// stamped one (the cycle order is the only order it has). Stable sort.
  static List<InboxDocument> _inArrivalOrder(List<InboxDocument> docs) {
    final keyed = <(DateTime, int, InboxDocument)>[];
    DateTime last = DateTime.fromMillisecondsSinceEpoch(0);
    for (var i = 0; i < docs.length; i++) {
      final at = docs[i].at;
      if (at != null && at.isAfter(last)) last = at;
      keyed.add((at ?? last, i, docs[i]));
    }
    keyed.sort((a, b) {
      final c = a.$1.compareTo(b.$1);
      return c != 0 ? c : a.$2.compareTo(b.$2);
    });
    return [for (final k in keyed) k.$3];
  }

  InboxDocument _briefing(
    Map<String, dynamic> e, {
    required int n,
    required DateTime? at,
    bool opening = false,
    String after = '',
  }) {
    final applied = (e['applied'] as Map? ?? const {}).cast<String, dynamic>();
    // Coverage as the controller saw it: needs with a resolved unknown citing
    // them, over all needs. The number a person reads first.
    final obs = (e['observation'] as Map?)?.cast<String, dynamic>() ?? const {};
    final needsN = ((obs['brief'] as Map?)?['needs'] as List? ?? const []).length;
    final metNeeds = <String>{};
    for (final u in (obs['unknowns'] as List? ?? const [])) {
      final um = (u as Map);
      if (um['status'] != 'resolved') continue;
      final m = RegExp(r'cites need:(\d+)').firstMatch('${um['notes'] ?? ''}');
      if (m != null) metNeeds.add(m.group(1)!);
    }
    final coverage = needsN == 0 ? '' : '${metNeeds.length} of $needsN requirements met';
    final refused = (e['refused'] as List? ?? const []).cast<String>();
    final done = e['done'] == true;
    List<String> ids(String k) => (applied[k] as List? ?? const []).cast<String>();
    // Everything the controller asked for, applied or not: a declined item
    // is still an action it took, shown struck with its verdict beside it.
    List<String> declinedOf(String kind) => [
      for (final r in refused)
        if (RegExp('^$kind (\\S+): ').firstMatch(r) case final m?) m.group(1)!,
    ];
    DocLine? attempted(String kind, String verb, String noun) {
      final ok = ids(kind == 'unknown' ? 'unknowns' : 'tasks');
      final no = declinedOf(kind).where((id) => !ok.contains(id)).toList();
      if (ok.isEmpty && no.isEmpty) return null;
      return DocLine(
        [...ok, for (final id in no) '$id (declined)'].join(', '),
        lead: '$verb ${ok.length + no.length} $noun${no.isEmpty ? '' : ' · ${no.length} declined'}',
        mono: true,
      );
    }
    final routedIds = ids('tasks');
    final acts = <DocLine>[
      ?attempted('unknown', 'minted', 'unknowns'),
      // Each routed work order is its own line, linked, when there are few;
      // a long list stays one line.
      if (routedIds.isNotEmpty && routedIds.length <= 4)
        for (final t in routedIds) DocLine(t, lead: 'routed work order', mono: true, link: 'workOrder:$t')
      else
        ?attempted('task', 'routed', 'work orders'),
      if ((applied['proposals'] as List? ?? const []).isNotEmpty)
        DocLine(
          '${(applied['proposals'] as List).length} change request(s) filed for your decision',
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
      // The document's title is its kind and number; what it says is the body.
      title: '',
      at: at,
      from: _from('Project Eval', 'controller'),
      status: status,
      hot: false, // a watch item, not trouble; the stamp colour says so
      header: [
        if (coverage.isNotEmpty) ('Coverage', coverage),
        if (after.isNotEmpty) ('After', 'Work order $after closed'),
        (
          'Readiness',
          opening
              ? 'Opening: route the first work'
              : done
              ? 'GO: brief requirements have been met'
              : idle
              ? 'In work: nothing applied and nothing routed'
              : acts.isEmpty
              ? 'In work: nothing new owed, routed work orders run on'
              : 'In work: the brief is not yet answered',
        ),
      ],
      sections: [
        DocSection('Situation', [DocLine(e['why'] as String? ?? '')]),
        if (blocked.isNotEmpty) DocSection('Worker reports', blocked, count: blocked.length),
        DocSection(
          'Actions taken',
          acts.isEmpty ? const [DocLine('None: nothing new owed.')] : acts,
        ),
        if (refused.isNotEmpty)
          DocSection('Declined', _grouped(refused), count: refused.length),
        // Restatements: things the route already held, listed again. Not
        // declines — nothing was asked wrongly — so they sit apart, quiet.
        if ((e['noted'] as List? ?? const []).isNotEmpty)
          DocSection('Already on the route', _grouped((e['noted'] as List).cast<String>()),
              count: (e['noted'] as List).length),
        // The handoff: the eval closes and the loop takes the next ready work
        // order — the first of these, the rest in order behind it.
        if (!done && (e['up_next'] as List? ?? const []).isNotEmpty)
          DocSection('Up next', [
            for (final (i, t) in (e['up_next'] as List).cast<String>().indexed)
              DocLine(t, lead: i == 0 ? 'next' : 'then', mono: true, link: 'workOrder:$t'),
          ]),
      ],
    );
  }

  /// Refusals share a handful of shapes; group by the text after the
  /// subject so 13 lines read as three reasons.
  Map<String, int> _routedIn = const {};

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
      // "already exists": the controller re-listed work the route already
      // held; say which briefing routed it.
      String withOrigin(String subject) {
        final id = subject.split(' ').last;
        final n = _routedIn[id];
        return n != null && text.startsWith('already exists') ? '$subject (#${n.toString().padLeft(3, '0')})' : subject;
      }
      out.add(
        named.isEmpty
            ? DocLine(text)
            : DocLine(
                named.map(withOrigin).join(', '),
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

  InboxDocument _workOrder(Map<String, dynamic> rt, DateTime? at, {bool opened = true, String routedBy = ''}) {
    final id = rt['id'] as String;
    final res = _json(File('${session.path}/tasks/$id/result.json')) ?? const <String, dynamic>{};
    final status = rt['status'] as String? ?? 'ready';
    // The route is the authority for the state; result.json is the worker's
    // report. A task the route closed without a report (the loop was killed
    // after the gate went green, before the worker wrote up) is closed — it
    // used to show IN PROGRESS forever, a second one beside the real one.
    // While the loop is live, a task without a report is still being worked
    // whatever the route says: the route's `done` comes at `route complete`,
    // and the review, a red round and the write-up follow it in the same
    // session. The agents tab says WORKING for that stretch; this says the
    // same, as ROUTE DONE, so the two never disagree.
    final live = res.isEmpty && opened && _loopLive;
    final inProgress = opened && (status == 'in_progress' || (res.isEmpty && status == 'ready'));
    final verdict = !opened
        ? status
        : inProgress
        ? 'in_progress'
        : live && status == 'done'
        ? 'route_done'
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
      'route_done' => 'ROUTE DONE · WRITING UP',
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
    if (turns == null && (inProgress || res.isEmpty)) {
      // No report yet, or none ever (closed by the route without one): count
      // the turns the log holds rather than show nothing.
      final events = File('${session.path}/tasks/$id/events/session.jsonl');
      if (events.existsSync()) {
        turns = eventCounts(events)['worker_turn'] ?? 0;
        turnsSoFar = true;
      }
    }
    final criteria = <String>[
      for (final a in (rt['acceptance'] as List? ?? const [])) (a as String).split(':').last,
    ];
    if (criteria.isEmpty && rt['map_id'] is String) criteria.add(rt['map_id'] as String);

    // Probes: one numbered entry per known the work order delivered — what
    // it read, then the claim it answers. Run ids stay in the data book.
    final evidence = (rt['evidence'] as List? ?? const []);
    final closeOut = <DocLine>[];
    if (evidence.isNotEmpty) {
      final e = (evidence.last as Map).cast<String, dynamic>();
      var n = 0;
      for (final k in (e['knowns'] as List? ?? const []).cast<String>()) {
        n++;
        final num = n.toString().padLeft(2, '0');
        final kn = _known(k);
        if (kn == null) {
          closeOut.add(DocLine('$k  ·  not on the map', lead: num, mono: true));
          continue;
        }
        final st = (kn['stats'] as Map? ?? const {});
        final unit = '${kn['unit'] ?? ''}'.trim();
        final value = unit.isEmpty ? '${kn['value']}' : '${kn['value']} $unit';
        closeOut.add(DocLine(
          '$k  =  $value  ·  n=${st['n'] ?? '?'}  ·  ${kn['confidence'] ?? ''}',
          lead: num,
          mono: true,
        ));
        final claim = (kn['claim'] as String? ?? '').trim();
        if (claim.isNotEmpty) closeOut.add(DocLine(claim, quote: true));
      }
    } else if (inProgress) {
      closeOut.add(const DocLine('Open: the worker is on it.'));
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
    final created = pb['created'] == null ? l(pb, 'installed') : l(pb, 'created');
    final method = <DocLine>[
      for (final p in created) DocLine(p, lead: '+ procedure', mono: true),
      for (final p in l(pb, 'improved')) DocLine(p, lead: '↑ procedure', mono: true),
      for (final w in l(wg, 'checked_in')) DocLine(w, lead: '+ widget', mono: true),
      for (final w in l(wg, 'rejected')) DocLine(w, lead: '✗ widget', mono: true, emphasis: true),
      if (l(pb, 'ignored').isNotEmpty) DocLine(l(pb, 'ignored').join(', '), lead: 'procedure drafted, not accepted', mono: true),
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
        final c = eventCounts(events);
        for (final k in const ['tool_rejected', 'tool_repeated_call', 'interjected']) {
          if ((c[k] ?? 0) > 0) anomalies.add(DocLine('${c[k]} ${k.replaceAll('_', ' ')}'));
        }
      }
    }
    for (final p in (res['problems'] as List? ?? const [])) {
      anomalies.add(DocLine(_clip(p as String, 160)));
    }
    if (verdict == 'error') anomalies.add(const DocLine('Session ended in error.', emphasis: true));

    final turnsLine = turnsSoFar ? '$turns so far' : '${turns ?? '-'}';
    // Tokens: from the result once the task ends; scanned off the journal while it runs.
    var usage = (res['usage'] as Map?)?.cast<String, dynamic>();
    if (usage == null) {
      final events = File('${session.path}/tasks/$id/events/session.jsonl');
      if (events.existsSync()) usage = _journalUsage(events);
    }
    final tokensLine = usage == null || (usage['calls'] ?? 0) == 0
        ? ''
        : '${_k(usage['prompt'])} in · ${_k(usage['completion'])} out · ${usage['calls']} calls'
            '${usage['cache_share'] != null ? ' · cache ${((usage['cache_share'] as num) * 100).round()}%' : ''}';
    return InboxDocument(
      kind: DocKind.workOrder,
      number: id,
      title: rt['title'] as String? ?? id,
      at: at,
      from: _from('Worker', 'worker'),
      status: stamp,
      hot: opened ? (!inProgress && verdict != 'complete' && verdict != 'route_done') : status == 'blocked',
      header: [
        ('Effort class', cls),
        if (!opened && (rt['priority'] as String? ?? '').isNotEmpty)
          ('Priority', rt['priority'] as String),
        ('Turns', turnsLine),
        if (tokensLine.isNotEmpty) ('Tokens', tokensLine),
        if (routedBy.isNotEmpty) ('Routed by', 'Daily briefing $routedBy'),
        if ((rt['deps'] as List? ?? const []).isNotEmpty) ('Depends on', (rt['deps'] as List).join(', ')),
      ],
      sections: [
        // The worker's own words first: what it found, or why it stopped.
        if (note != null) DocSection("Worker's statement", [DocLine(note, quote: true)]),
        // What the order had to establish, in English: one sentence per
        // unknown, the claim as the controller wrote it.
        DocSection('Objective', [
          for (final u in criteria)
            DocLine(_unknown(u)?['claim'] as String? ?? u, lead: u),
        ]),
        // What it established: each probe's reading and the claim it answers.
        if (closeOut.isNotEmpty) DocSection('Probes', closeOut),
        if (stamps.isNotEmpty) DocSection('Course of work', stamps),
        if (method.isNotEmpty)
          DocSection('Minted', method, footnote: '+ added to the library · ↑ an existing one improved'),
        if (anomalies.isNotEmpty) DocSection('Anomalies', anomalies, count: anomalies.length),
      ],
    );
  }

  /// [alsoAsked]: the open proposals that ask the same thing, folded under
  /// this one. [askedBefore]: a decided proposal this one asks again.
  InboxDocument _changeRequest(Map<String, dynamic> p, Map<String, dynamic> brief,
      {List<Map<String, dynamic>> alsoAsked = const [], Map<String, dynamic>? askedBefore}) {
    final summary = p['summary'] as String? ?? '';
    final parts = summary.split(' — evidence: ');
    final patch = (p['patch'] as Map? ?? const {}).cast<String, dynamic>();
    final status = (p['status'] as String? ?? 'open').toUpperCase();
    final version = brief['version'] as int? ?? 1;
    // Decided along with another: on record, inert.
    final folded = p['decided_with'] as String?;
    return InboxDocument(
      kind: DocKind.changeRequest,
      number: p['id'] as String,
      title: '',
      at: DateTime.tryParse(p['created_at'] as String? ?? ''),
      from: _from('Project Eval', 'controller'),
      status: status,
      past: folded != null,
      awaitingSignature: status == 'OPEN',
      proposalId: p['id'] as String,
      patch: patch,
      decidedAt: DateTime.tryParse((p['decided_at'] ?? p['accepted_at'] ?? p['rejected_at'] ?? p['updated_at'] ?? '') as String),
      signedBy: p['signed_by'] as String? ?? '',
      signature: (p['signature'] as String?)?.isNotEmpty == true ? _terra(p['signature'] as String).path : null,
      header: [
        ('Against', 'brief v$version'),
        if (alsoAsked.isNotEmpty) ('Asked', '${alsoAsked.length + 1} times; one decision covers all'),
        if (folded != null) ('Decided with', folded),
        if (askedBefore != null && askedBefore['status'] != 'open')
          ('Asked before', '${askedBefore['id']}, ${askedBefore['status']}'),
      ],
      sections: [
        DocSection('Finding', [DocLine(parts.first)]),
        if (parts.length > 1) DocSection('Evidence', [DocLine(parts[1])]),
        if (alsoAsked.isNotEmpty)
          DocSection('Asked again as', [
            for (final q in alsoAsked)
              DocLine((q['summary'] as String? ?? '').split(' — evidence: ').first, lead: q['id'] as String),
          ], footnote: 'The same amendment; the reasons differ. Your decision on this request decides these too.'),
        DocSection('Proposed amendment', [
          for (final e in patch.entries)
            if (e.key == 'budget_delta')
              // The budget line is the one that was missed when it read
              // "note: 3": say the number before and after, in emphasis.
              DocLine(_budgetLine(e.value, patch), lead: 'budget', emphasis: true)
            else if (e.key == 'budget_points')
              DocLine('set to ${e.value} points (was ${patch['was_budget_points'] ?? '—'}; older form, a number written over the budget)',
                  lead: 'budget', emphasis: true)
            else if (e.key != 'was_budget_points' && e.key != 'budget_before')
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

  static String _budgetLine(Object? delta, Map<String, dynamic> patch) {
    final d = (delta as num?)?.toInt() ?? 0;
    final base = (patch['budget_before'] ?? patch['was_budget_points']) as num?;
    final sign = d >= 0 ? '+' : '';
    if (base == null) return '$sign$d points';
    final applied = patch.containsKey('budget_before');
    return '$sign$d points: ${base.toInt()} → ${base.toInt() + d}${applied ? '' : ' as it stands now; added to the budget when accepted'}';
  }

  /// Event logs run to hundreds of megabytes (every checkpoint carries the
  /// session). Scan for the type marker in chunks; never hold the file.
  /// Tokens a session journal consumed, from its model responses.
  static Map<String, dynamic> _journalUsage(File f) {
    var calls = 0, prompt = 0, completion = 0, cached = 0;
    for (final line in f.readAsLinesSync()) {
      if (!line.contains('"model_response"')) continue;
      try {
        final e = jsonDecode(line) as Map<String, dynamic>;
        if (e['event_type'] != 'model_response') continue;
        final p = (e['payload'] as Map?)?.cast<String, dynamic>() ?? const {};
        if (p['status'] != 200) continue;
        var body = p['body'];
        if (body is String) body = jsonDecode(body);
        final u = ((body as Map?)?['usage'] as Map?)?.cast<String, dynamic>();
        if (u == null) continue;
        calls++;
        prompt += (u['prompt_tokens'] as num? ?? 0).toInt();
        completion += (u['completion_tokens'] as num? ?? 0).toInt();
        cached += (((u['prompt_tokens_details'] as Map?)?['cached_tokens']) as num? ?? 0).toInt();
      } catch (_) {}
    }
    return {'calls': calls, 'prompt': prompt, 'completion': completion, 'cached': cached, 'cache_share': prompt == 0 ? null : cached / prompt};
  }

  /// Whether an outage is behind the run: the model that did not answer
  /// has answered since, or the run is over.
  bool _outagePassed(String role, DateTime? at, String? task, int? turn,
      {required bool runOver, required Map<String, dynamic> route}) {
    if (runOver) return true;
    if (role == 'worker') {
      if (task == null) return false;
      if (File('${session.path}/tasks/$task/result.json').existsSync()) return true;
      if (turn == null) return false;
      final events = File('${session.path}/tasks/$task/events/session.jsonl');
      if (!events.existsSync()) return false;
      return (eventCounts(events)['worker_turn'] ?? 0) > turn;
    }
    if (at == null) return false;
    for (final t in (route['tasks'] as List? ?? const [])) {
      final created = DateTime.tryParse(((t as Map)['created_at'] as String?) ?? '');
      if (created != null && created.isAfter(at)) return true;
    }
    return false;
  }

  /// Event kinds in a worker's log, counted. The log is append-only and
  /// runs to hundreds of megabytes, so the count is kept beside it with
  /// the offset it was taken to, and only what was appended since is read.
  static Map<String, int> eventCounts(File f) {
    const marker = '"event_type": "';
    final side = File('${f.path}.counts.json');
    var counts = <String, int>{};
    var done = 0;
    final length = f.lengthSync();
    try {
      final j = jsonDecode(side.readAsStringSync()) as Map<String, dynamic>;
      final at = (j['done'] as num?)?.toInt() ?? 0;
      if (at <= length) {
        done = at;
        counts = ((j['counts'] as Map?)?.cast<String, dynamic>() ?? const {}).map((k, v) => MapEntry(k, (v as num).toInt()));
      }
    } catch (_) {
      // No side file, or an unreadable one: count from the start.
    }
    if (done == length) return counts;
    final raf = f.openSync();
    try {
      raf.setPositionSync(done);
      var carry = '';
      var base = done; // file offset of carry[0]
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
          counts[text.substring(s, e)] = (counts[text.substring(s, e)] ?? 0) + 1;
          from = e + 1;
          done = base + from; // everything before here is counted
        }
        // Carry only what was not counted (a marker may straddle the chunk
        // edge); carrying counted text would count it twice.
        final cut = from > text.length - 64 ? from : text.length - 64;
        base += cut;
        carry = text.substring(cut);
      }
    } finally {
      raf.closeSync();
    }
    try {
      side.writeAsStringSync(jsonEncode({'done': done, 'counts': counts}));
    } catch (_) {}
    return counts;
  }

}
