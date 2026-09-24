import 'dart:convert';
import 'dart:io';

import '../models/loop.dart';
import '../cg/syntax_highlighted_code/syntax_highlighted_code.dart' show languageForPath;
import 'state_dir.dart';

/// A seat on the floor: one session a person can look over the shoulder
/// of — the controller, or a worker on one work order.
class FloorSession {
  const FloorSession({
    required this.id,
    required this.title,
    required this.status,
    required this.turns,
    required this.startedAt,
    this.phase = '',
    this.lastActiveAt,
  });

  /// When this seat last did anything: a worker's last log or report write, a
  /// decision's own time (the live one is now). The tab lists by this — the
  /// agent that most recently worked comes first.
  final DateTime? lastActiveAt;

  /// Where a live worker is, read from the tail of its log: `claimed done ·
  /// reviewer looking`, `sent back by the reviewer`, `gate red: repair round`, `gate green: record the
  /// method`, `library merge`… Empty when the session has ended or has not
  /// reached a phase. The route's `done` used to stamp the seat GATE GREEN
  /// for a hundred turns of review, red round and write-up.
  final String phase;

  /// `controller`, or the task id.
  final String id;
  final String title;

  /// in_progress | done | blocked | … for a worker; `controller` for it.
  final String status;

  /// Turns taken so far (null when unknown).
  final int? turns;
  final DateTime? startedAt;

  bool get isController => id.startsWith('controller');

  /// The loop itself between seats (`host.live.json`): closing a work order,
  /// committing, re-taking stale readings, settling a block.
  bool get isHost => id == 'host';

  /// Which decision this seat is (1-based), for a controller seat.
  int? get decision => isController ? int.tryParse(id.split(':').last) : null;
}

/// Progress of a model reply: bytes so far and when the last of them came.
class Wire {
  const Wire({required this.bytes, required this.at, this.lastEvent = ''});
  final int bytes;
  final DateTime at;

  /// The last SSE event name seen (`response.reasoning_summary_text.delta`,
  /// `response.output_text.delta`, …): what the model is doing on the wire.
  final String lastEvent;

  /// thinking | writing | replying: the reply's channel, from its last event.
  String get doing {
    final e = lastEvent;
    if (e.contains('reasoning')) return 'thinking';
    if (e.contains('output_text') || e.contains('function_call') || e.contains('output_item') || e.contains('content_part')) return 'writing';
    return 'replying';
  }

  static Wire? read(File f) {
    try {
      if (!f.existsSync()) return null;
      final j = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
      return Wire(
        bytes: (j['bytes'] as num?)?.toInt() ?? 0,
        at: DateTime.fromMillisecondsSinceEpoch(((j['at'] as num) * 1000).round(), isUtc: true),
        lastEvent: (j['last_event'] as String?) ?? '',
      );
    } catch (_) {
      return null;
    }
  }
}

/// A slice of a worker's chat and the byte range it came from, so the
/// caller can ask for the slice before it.
class TraceWindow {
  const TraceWindow({required this.turns, required this.start, required this.end, required this.fileLength, this.unchanged = false, this.wire});

  /// The file has not grown past what the caller already holds: no turns
  /// were read. Only answered when the caller said what it holds.
  final bool unchanged;
  final List<Turn> turns;

  /// The reply on the wire, from the session's `stream.json`: when its last
  /// byte arrived. Read on every tick, whether or not the log grew — the
  /// log does not move while a call is out, and this is what moves.
  final Wire? wire;

  /// Offset of the first complete line read; page back by asking for
  /// `end: start`.
  final int start;
  final int end;
  final int fileLength;

  bool get atBeginning => start == 0;
}

/// Reads what the workers and the controller are doing, step by step,
/// from the files the loop writes — tailing, never loading. The event log
/// is dominated by `checkpoint` and `model_*` lines that carry the whole
/// session; those are skipped by prefix without parsing. What remains is
/// the worker's own words per turn, each tool call paired with its
/// outcome, rejections, repeats, interjections and handoffs.
class RunFloor {
  RunFloor({required this.project, required this.session});
  final Directory project;
  final Directory session;

  /// The worker's window, from the harness config `run.json` names: how big
  /// the prompt may grow (`session_policy.context_capacity`) and where the
  /// loop rolls it over (`rollover_threshold`). Read once; the config does
  /// not move under a running session.
  late final Map<String, dynamic> _sessionPolicy = () {
    final run = _json(File('${session.path}/run.json'));
    final config = _json(File(run?['config'] as String? ?? ''));
    return (config?['session_policy'] as Map?)?.cast<String, dynamic>() ?? const <String, dynamic>{};
  }();
  int? get contextCapacity => (_sessionPolicy['context_capacity'] as num?)?.toInt();
  int? get rolloverTokens => (_sessionPolicy['rollover_threshold'] as num?)?.toInt();

  /// How much of the log's tail to read per refresh. A `worker_turn` line
  /// can be a few hundred KB (it carries the applied prompt), so this is
  /// generous enough for a screenful of recent turns and cheap enough to
  /// re-read every couple of seconds.
  static const tailBytes = 24 * 1024 * 1024;

  Map<String, dynamic>? _json(File f) {
    if (!f.existsSync()) return null;
    try {
      return jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  /// The seats, newest first: every controller decision (each one its own
  /// small loop of draft → guards → redraft) and every work order a worker
  /// has opened. Decisions are dated the way the desk dates briefings: the
  /// opening one at the route's creation, each later one at the close of
  /// the work order it followed.
  List<FloorSession> sessions() {
    final route = _json(File('${stateDir(project.path)}/route.json'));
    final byId = <String, Map<String, dynamic>>{
      for (final t in (route?['tasks'] as List? ?? const []))
        (t as Map)['id'] as String: t.cast<String, dynamic>(),
    };
    final seats = <FloorSession>[];

    // Controller decisions.
    final closed = byId.values.where((t) => t['status'] != 'in_progress' && t['started_at'] != null).toList()
      ..sort((a, b) => (a['updated_at'] as String? ?? '').compareTo(b['updated_at'] as String? ?? ''));
    final journal = _journal();
    var evalIx = 0;
    for (final (i, e) in journal.indexed) {
      final mode = e['mode'] as String? ?? 'eval';
      DateTime? at;
      final stamped = e['at'];
      if (stamped is num) {
        at = DateTime.fromMillisecondsSinceEpoch((stamped * 1000).round(), isUtc: true);
        if (mode != 'route') evalIx++;
      } else if (mode == 'route') {
        at = DateTime.tryParse(route?['created_at'] as String? ?? '');
      } else {
        at = evalIx < closed.length ? DateTime.tryParse(closed[evalIx]['updated_at'] as String? ?? '') : null;
        evalIx++;
      }
      final attempts = (e['attempts'] as List? ?? const []).length;
      final why = (e['why'] as String? ?? '').trim();
      seats.add(FloorSession(
        id: 'controller:${i + 1}',
        title: why.isEmpty ? mode : why,
        status: 'controller',
        turns: attempts,
        startedAt: at,
        lastActiveAt: at,
      ));
    }
    // The decision being made: the engine keeps `controller.live.json` while a
    // step runs (its reads so far, when it went to the model) and drops it when
    // the record lands on the journal. A first step on a local model is ten
    // minutes with nothing else on disk; the page was empty until then.
    final live = _live();
    if (live != null) {
      final started = live['started_at'];
      seats.add(FloorSession(
        id: 'controller:${journal.length + 1}',
        title: (live['user'] as String? ?? '').trim().isEmpty ? 'deciding' : (live['user'] as String).trim(),
        status: 'controller',
        turns: (live['tools'] as List? ?? const []).length,
        startedAt: started is num ? DateTime.fromMillisecondsSinceEpoch((started * 1000).round(), isUtc: true) : null,
        phase: _livePhase(live),
        lastActiveAt: DateTime.now().toUtc(),
      ));
    }

    // The loop's own work between seats: minutes of library ticks, commits and
    // re-taken readings that used to leave every seat silent.
    final host = _hostLive();
    if (host != null) {
      final steps = (host['steps'] as List? ?? const []);
      final started = host['started_at'];
      seats.add(FloorSession(
        id: 'host',
        title: steps.isEmpty ? 'between seats' : ((steps.last as Map)['text'] as String? ?? 'between seats'),
        status: 'host',
        turns: steps.length,
        startedAt: started is num ? DateTime.fromMillisecondsSinceEpoch((started * 1000).round(), isUtc: true) : null,
        phase: 'host work',
        lastActiveAt: DateTime.now().toUtc(),
      ));
    }

    // Worker sessions.
    final tasks = Directory('${session.path}/tasks');
    if (!tasks.existsSync()) return seats..sort(_newestFirst);
    // One loop runs one task at a time: of the live sessions, the one whose
    // log moved last is the one being worked; the others wait their turn
    // (a resumed loop puts an unreported task first and the one it was on
    // waits behind it — it looked frozen at "completion accepted").
    final dirs = [for (final d in tasks.listSync()) if (d is Directory) d];
    Directory? current;
    var newest = 0;
    for (final d in dirs) {
      if (File('${d.path}/result.json').existsSync()) continue;
      final log = File('${d.path}/events/session.jsonl');
      if (!log.existsSync()) continue;
      final at = log.lastModifiedSync().millisecondsSinceEpoch;
      if (at > newest) {
        newest = at;
        current = d;
      }
    }
    for (final d in dirs) {
      final id = d.uri.pathSegments.where((s) => s.isNotEmpty).last;
      final rt = byId[id] ?? const <String, dynamic>{};
      final res = _json(File('${d.path}/result.json'));
      // Live until the worker's report exists: the route's `done` is the
      // task on the route, not the end of the session (review, a red
      // round and the write-up all come after it).
      final live = res == null;
      final waiting = live && current != null && current.path != d.path;
      seats.add(FloorSession(
        id: id,
        title: rt['title'] as String? ?? id,
        status: live ? 'in_progress' : (res['verdict'] as String? ?? 'done'),
        phase: !live
            ? ''
            : waiting
            ? 'waiting · the loop is on ${current.uri.pathSegments.where((s) => s.isNotEmpty).last}'
            : _phaseOf(File('${d.path}/events/session.jsonl'), routeDone: rt['status'] == 'done'),
        turns: res?['turns'] as int?,
        startedAt: DateTime.tryParse(rt['started_at'] as String? ?? '') ??
            DateTime.tryParse(rt['created_at'] as String? ?? ''),
        lastActiveAt: _lastWrite([File('${d.path}/result.json'), File('${d.path}/events/session.jsonl')]),
      ));
    }
    return seats..sort(_newestFirst);
  }

  /// The phase a live worker is in, from the last host continuation or
  /// interjection with a label, and whether the reviewer sent a completion
  /// back; before any, `claimed done` once the route has the task. Then what
  /// is happening this minute, when it is not plain turning (the stamp says
  /// that): compacting, reflecting, the reviewer looking. Empty when there
  /// is nothing to add.
  ///
  /// The route's `done` is the worker's claim, and it stays `done` after the
  /// reviewer sends the claim back: a worker forty minutes into the rework
  /// read `ROUTE DONE · WORKER TURNING`, in green.
  static String _phaseOf(File log, {required bool routeDone}) {
    if (!log.existsSync()) return routeDone ? 'claimed done' : '';
    final length = log.lengthSync();
    final start = length > 512 * 1024 ? length - 512 * 1024 : 0;
    final (lines, firstLine) = _linesBetween(log, start, length);
    String phase = '';
    var sentBack = false;
    var decided = false;       // a completion review or a host phase seen: the tail says where the worker is
    var reviewerOut = false;   // a reviewer call in flight: request logged, no response yet
    var reflecting = false;    // the window filled: the worker records its method before it rolls
    var compacting = false;    // the handoff call is out: the window is being rolled
    for (final line in lines) {
      final type = _typeOf(line);
      if (type == 'reflect_started') {
        reflecting = true;
        continue;
      }
      if (type == 'worker_handoff') {
        reflecting = compacting = false;
        continue;
      }
      if (type != 'continued' && type != 'interjected' && type != 'controller_review' &&
          type != 'model_request' && type != 'model_response') {
        continue;
      }
      if (type == 'model_request' && _fieldOf(line, 'purpose') == 'handoff') {
        compacting = true;
        continue;
      }
      Map<String, dynamic> e;
      try {
        e = jsonDecode(line) as Map<String, dynamic>;
      } catch (_) {
        continue;
      }
      final p = (e['payload'] as Map? ?? const {}).cast<String, dynamic>();
      switch (type) {
        case 'continued' || 'interjected':
          final label = _phaseLabel(p);
          if (label.isNotEmpty) {
            phase = label;
            decided = true;
            sentBack = false;
          }
        case 'controller_review':
          // Held until the next completion review or host phase: the rework
          // that follows is the worker turning, and it is still sent back.
          final back = _sentBack(p);
          if (back != null) {
            sentBack = back;
            decided = true;
          }
          reviewerOut = false;
        case 'model_request':
          if (p['purpose'] == 'controller') reviewerOut = true;
        case 'model_response':
          if (p['purpose'] == 'controller') reviewerOut = false;
      }
    }
    // What is happening this minute comes first, then the host phase, then the route.
    // A long rework pushes the review that sent it back out of the tail
    // (a 30 MB log, the review 25 MB up): walk back for the latest review
    // or host phase. Only once the route has the task — before that there
    // is no claim to have been sent back.
    if (routeDone && !decided) {
      var end = firstLine;
      const chunk = 4 * 1024 * 1024;
      while (end > 0 && !decided && length - end < 96 * 1024 * 1024) {
        final (older, at) = _linesBetween(log, end > chunk ? end - chunk : 0, end);
        for (final line in older.reversed) {
          final type = _typeOf(line);
          if (type != 'continued' && type != 'interjected' && type != 'controller_review') continue;
          Map<String, dynamic> p;
          try {
            p = ((jsonDecode(line) as Map)['payload'] as Map? ?? const {}).cast<String, dynamic>();
          } catch (_) {
            continue;
          }
          final label = _phaseLabel(p);
          final back = type == 'controller_review' ? _sentBack(p) : null;
          if (type != 'controller_review' && label.isNotEmpty) {
            phase = label;
            decided = true;
          } else if (back != null) {
            sentBack = back;
            decided = true;
          }
          if (decided) break;
        }
        if (at >= end) break;
        end = at;
      }
    }
    final now = compacting
        ? 'compacting'
        : reflecting
        ? 'reflecting before compaction'
        : reviewerOut ? 'reviewing' : '';
    final where = sentBack ? 'sent back by the reviewer' : phase.isNotEmpty ? phase : routeDone ? 'claimed done' : '';
    return [display(where), now].where((s) => s.isNotEmpty).join(' · ');
  }

  /// The phase a host continuation opens, or '' — `estimate spent` is a
  /// note to the worker in the phase it is in, not a phase of its own.
  static String _phaseLabel(Map<String, dynamic> p) {
    final label = _label(p);
    return label.startsWith('estimate spent') ? '' : label;
  }

  /// A host continuation's label, when the tab draws it as a rule. Labels
  /// no run sends any more (`gate green: write up the method`, before the
  /// write-up phase went; `continued: <task>`, an earlier naming) and ones
  /// no run has ever sent (`reply too big`, `resumed: done tool added`)
  /// read as an unlabelled continuation: a plain row, no rule.
  static String _label(Map<String, dynamic> p) {
    final label = (p['label'] as String? ?? '').trim();
    // Taken off the tab (trace-rules review, 2026-09-23): a library merge or
    // repair and a resume with walks open are the worker working, not a phase
    // the person needs a line for.
    const dropped = {
      'gate green: write up the method', 'reply too big', 'resumed: done tool added',
      'library merge', 'library repair: validator refused', 'resumed: walks left open',
    };
    return dropped.contains(label) || label.startsWith('continued: ') ? '' : label;
  }

  /// How the tab draws an engine phase label: the words the person reads,
  /// its colour and where the words sit on the line. Labels not listed are
  /// drawn as the engine wrote them. Agreed on the trace-rules page.
  static const _rules = <String, (String, String, String)>{
    // Every red is one line; the reason is the line under it (see _whyRed).
    'gate red: repair round': ('gate red', 'crimson', 'center'),
    'gate red: tick the walks you opened': ('gate red', 'crimson', 'center'),
    'gate green: record the method': ('gate green: record success in reflection', 'pass', 'left'),
    'gate green: the library asks': ('gate green: no recordable library objects - requesting...', 'pass', 'left'),
    'reopened': ('resumed', 'system', 'center'),
    // Why a session runs again when nothing was said to the worker (a `marked` event): a plain restart, a
    // controller's rebucket (the buckets under the line) or its release of a block.
    'resumed': ('resumed', 'system', 'center'),
    'rebucketed': ('resumed', 'system', 'center'),
    'unblocked': ('unblocked', 'pass', 'center'),
    'the person': ('requestor message', 'system', 'center'),
  };

  /// The words the tab uses for an engine phase label (the seat's phase too).
  static String display(String label) => _rules[label]?.$1 ?? label;

  /// A phase rule for an engine label, with the host's words under it.
  static Turn _phaseRule(int n, DateTime at, String label, String text) {
    final r = _rules[label];
    final title = r?.$1 ?? label;
    return Turn(n: n, kind: 'phase', at: at, title: title, body: _phaseBody(title, text), tone: r?.$2 ?? '', anchor: r?.$3 ?? 'center');
  }

  /// A gate-red line leads with why, for the person: what kinds of thing are
  /// missing and how many, then the host's list as the worker got it.
  static String _phaseBody(String title, String text) {
    if (title != 'gate red' || text.isEmpty || text.endsWith('from the host')) return text;
    final why = _whyRed(text);
    return why.isEmpty ? text : '$why\n\n$text';
  }

  static String _whyRed(String text) {
    // (singular, plural) per kind of problem, in the order a person fixes them.
    const kinds = [
      ('task not closed on the route', 'task not closed on the route'),
      ('reading the host could not reproduce', 'readings the host could not reproduce'),
      ('problem on the map', 'problems on the map'),
      ('widget that does not validate', 'widgets that do not validate'),
      ('procedure checklist left open', 'procedure checklists left open'),
      // The host's honesty checks: their lines start with an unknown's id.
      ('reading true over an empty input', 'readings true over an empty input'),
      ('artifact that disagrees with the map', 'artifacts that disagree with the map'),
      ('set of readings that agree to every digit', 'sets of readings that agree to every digit'),
      ('probe that never reads its declared input', 'probes that never read their declared inputs'),
      ('other problem', 'other problems'),
    ];
    final counts = List.filled(kinds.length, 0);
    for (final raw in text.split('\n')) {
      final l = raw.trim();
      if (!l.startsWith('- ')) continue;
      final item = l.substring(2);
      counts[item.startsWith('route task')
          ? 0
          : item.startsWith('re-measure')
          ? 1
          : item.startsWith('[')
          ? 2
          : item.startsWith('widget ')
          ? 3
          : item.startsWith('checklist ')
          ? 4
          : item.contains(' reads true (')
          ? 5
          : item.startsWith('artifact known ')
          ? 6
          : item.contains(' all read exactly ')
          ? 7
          : item.contains('_probe declares ')
          ? 8
          : 9]++;
    }
    final parts = [
      for (final (i, n) in counts.indexed)
        if (n > 0) '$n ${n == 1 ? kinds[i].$1 : kinds[i].$2}',
    ];
    return parts.isEmpty ? '' : 'Red because: ${parts.join(' · ')}.';
  }

  /// The closing rule from the worker's report: how it ended, and what it
  /// leaves (turns, knowns, runs, what the library took). Null while live.
  ({String title, String body, String tone, DateTime at})? _closing(String taskId) {
    final f = File('${session.path}/tasks/$taskId/result.json');
    final r = _json(f);
    if (r == null) return null;
    final verdict = r['verdict'] as String? ?? '';
    final (title, tone) = switch (verdict) {
      // Every ending the controller reviews says so; a stop ends the run and a driver error skips the
      // controller (the loop moves to the next work order), so those two say what happens instead.
      'complete' || 'done' => ('closed — complete · sent to the controller', 'pass'),
      'incomplete' => ('closed — gate red · sent to the controller', 'crimson'),
      'blocked' || 'blocked_by_worker' => ('closed — blocked · sent to the controller', 'crimson'),
      'stopped' => ('closed — stopped · the run stops here', 'muted'),
      'error' => ('closed — driver error · the loop moves to the next work order', 'crimson'),
      _ => ('closed — $verdict', 'muted'),
    };
    List<String> names(Object? m, List<String> keys) => [
      if (m is Map)
        for (final k in keys) ...((m[k] as List?) ?? const []).map((e) => '$e'),
    ];
    final procedures = names(r['playbook'], const ['created', 'improved', 'installed']).toSet();
    final widgets = names(r['widgets'], const ['checked_in']).toSet();
    final parts = [
      if (r['turns'] != null) '${r['turns']} turns',
      for (final (k, one) in const [('knowns', 'known'), ('runs', 'run')])
        switch ((r[k] as List? ?? const []).length) { 1 => '1 $one', final n => '$n $k' },
    ];
    // Why it ended leads, in the words the trace notes asked for; the tally follows.
    final endedBy = r['ended_by'] as String?;
    final stopNote = (r['stop_note'] as String? ?? '').trim();
    final why = switch (verdict) {
      'blocked' || 'blocked_by_worker' when r['blocked_reason'] is String => 'Worker called block: ${r['blocked_reason']}',
      'incomplete' when endedBy == 'turn_cap' =>
        'Ended by the turn cap: ${r['turns'] ?? '?'} of ${r['turn_cap'] ?? '?'} turns, gate still red.',
      'incomplete' when endedBy == 'gate_rounds' =>
        'Ended after ${r['gate_rounds'] ?? 3} red gate rounds in a row with no fewer problems.',
      'stopped' => 'System stop initiated${stopNote.isEmpty || stopNote == 'STOP' ? '' : ': $stopNote'}.',
      'error' when r['error'] is String => 'System error: ${r['error']}',
      _ => '',
    };
    final body = [
      if (why.isNotEmpty) why,
      parts.join(' · '),
      if (procedures.isNotEmpty) 'Procedures filed: ${procedures.join(', ')}',
      if (widgets.isNotEmpty) 'Widgets filed: ${widgets.join(', ')}',
    ].join('\n');
    return (title: title, body: body, tone: tone, at: f.lastModifiedSync().toUtc());
  }

  /// A completion review's verdict: sent back (a correction) or accepted;
  /// null for a periodic review, which judges no claim.
  static bool? _sentBack(Map<String, dynamic> p) {
    if (p['boundary'] != 'completion') return null;
    final d = (p['decision'] as Map? ?? const {}).cast<String, dynamic>();
    final c = (d['correction'] as String? ?? '').trim();
    return c.isNotEmpty && c != 'None';
  }

  /// The latest modification among files that exist, in UTC; null when none do.
  static DateTime? _lastWrite(List<File> files) {
    DateTime? out;
    for (final f in files) {
      if (!f.existsSync()) continue;
      final m = f.lastModifiedSync().toUtc();
      if (out == null || m.isAfter(out)) out = m;
    }
    return out;
  }

  static int _newestFirst(FloorSession a, FloorSession b) {
    // Most recently active first; the start time only where nothing newer is known.
    final at = a.lastActiveAt ?? a.startedAt, bt = b.lastActiveAt ?? b.startedAt;
    if (at == null || bt == null) return at == null ? -1 : 1; // undated (the newest decision) on top
    final c = bt.compareTo(at);
    // A decision and the work order it closed share a time: decision above.
    return c != 0 ? c : (a.isController ? -1 : (b.isController ? 1 : 0));
  }

  List<Map<String, dynamic>> _journal() {
    final f = File('${session.path}/controller.jsonl');
    if (!f.existsSync()) return const [];
    final out = <Map<String, dynamic>>[];
    for (final line in f.readAsLinesSync()) {
      if (line.trim().isEmpty) continue;
      try {
        out.add(jsonDecode(line) as Map<String, dynamic>);
      } catch (_) {}
    }
    return out;
  }

  Map<String, dynamic>? _live() => _json(File('${session.path}/controller.live.json'));
  Map<String, dynamic>? _hostLive() => _json(File('${session.path}/host.live.json'));

  /// The host's steps as a trace: each one a line, the last one live.
  List<Turn> hostTurns() {
    final live = _hostLive();
    if (live == null) return const [];
    final steps = [for (final s in (live['steps'] as List? ?? const [])) (s as Map).cast<String, dynamic>()];
    DateTime at(Map<String, dynamic> s) => DateTime.fromMillisecondsSinceEpoch((((s['at'] as num?) ?? 0) * 1000).round(), isUtc: true);
    return [
      for (final (i, s) in steps.indexed)
        i == steps.length - 1
            ? Turn(n: 0, kind: 'live', at: at(s), title: 'host', body: s['text'] as String? ?? '')
            : Turn(n: 0, kind: 'note', at: at(s), title: s['text'] as String? ?? ''),
    ];
  }

  /// `reading · 3 reads` or `calling the model · 3 reads so far`, from the live file.
  static String _livePhase(Map<String, dynamic> live) {
    final reads = (live['tools'] as List? ?? const []).length;
    final n = reads == 0 ? '' : ' · $reads read${reads == 1 ? '' : 's'}';
    return (live['phase'] == 'model' ? 'calling the model' : 'reading') + n;
  }

  /// A controller decision as the exchange it was, in the worker's format:
  /// for each attempt, the message it was sent, its thinking when returned,
  /// its reply verbatim, and what came back — the guards' refusals, or the
  /// look it asked for. Then the line that stood.
  List<Turn> controllerTurns({int? decision}) {
    final out = <Turn>[];
    var n = 0;
    final journal = _journal();
    final live = _live();
    if (live != null && decision == journal.length + 1) {
      // The step in progress: what it was sent, the reads it has made, and a clock on the call that is out.
      final user = (live['user'] as String? ?? '').trim();
      out.add(Turn(n: 1, kind: 'prompt', at: DateTime.now(), title: user.isEmpty ? 'observation' : user, body: user));
      for (final t in (live['tools'] as List? ?? const [])) {
        final call = (t as Map).cast<String, dynamic>();
        final args = call['args'];
        final refused = call['refused'] == true;
        out.add(Turn(
          n: 1,
          kind: 'tool',
          at: DateTime.now(),
          title: '${call['name']}  ${args is Map ? args.values.map((v) => '$v').join(' ') : '$args'}',
          body: refused ? 'refused' : '${call['chars'] ?? '?'} chars read',
          ok: !refused,
        ));
      }
      final at = live['at'];
      out.add(Turn(
        n: 1,
        kind: 'live',
        at: at is num ? DateTime.fromMillisecondsSinceEpoch((at * 1000).round(), isUtc: true) : DateTime.now(),
        title: 'controller',
        body: _livePhase(live),
      ));
      return out;
    }
    for (final e in journal) {
      n++;
      if (decision != null && n != decision) continue;
      final mode = e['mode'] as String? ?? 'eval';
      final attempts = (e['attempts'] as List? ?? const []);
      for (final (i, a) in attempts.indexed) {
        final at = (a as Map).cast<String, dynamic>();
        final turn = i + 1;
        // In: the observation as rendered for the model.
        final user = at['user'] as String?;
        out.add(Turn(
          n: turn,
          kind: 'prompt',
          at: DateTime.now(),
          title: user != null
              ? '$mode  ${_firstLine(user)}'
              : '$mode  observation — ${at['observation_chars'] ?? '?'} chars (not journaled by this engine)',
          body: user ?? '',
        ));
        final thought = (at['reasoning'] as String? ?? '').trim();
        if (thought.isNotEmpty) out.add(Turn(n: turn, kind: 'thought', at: DateTime.now(), title: _firstLine(thought), body: thought));
        // The reads it made before deciding, one row each, the way a worker's tool calls read: verb and
        // arguments, red when the verb was refused or errored.
        for (final t in (at['tools'] as List? ?? const [])) {
          final call = (t as Map).cast<String, dynamic>();
          final args = call['args'];
          final argText = args is Map
              ? args.values.map((v) => '$v').join(' ')
              : '$args';
          final refused = call['refused'] == true;
          out.add(Turn(
            n: turn,
            kind: 'tool',
            at: DateTime.now(),
            title: '${call['name']}  $argText',
            body: refused ? 'refused' : '${call['chars'] ?? '?'} chars read',
            ok: !refused,
          ));
        }
        if (at['error'] != null) {
          out.add(Turn(n: turn, kind: 'tool', at: DateTime.now(), title: 'reply was not a decision', body: '${at['error']}', ok: false));
          continue;
        }
        // Out: the reply, verbatim (pretty-printed when it is the JSON it was asked for).
        final raw = at['raw'] as String? ?? '';
        final d = _decisionOf(raw);
        out.add(Turn(
          n: turn,
          kind: 'note',
          at: DateTime.now(),
          title: (d?['why'] as String?)?.trim().isNotEmpty == true ? d!['why'] as String : _firstLine(raw),
          body: d == null ? raw : const JsonEncoder.withIndent('  ').convert(d),
        ));
        // Back: what the guards returned.
        final refused = [
          ...(at['refusals'] as List? ?? const []).cast<String>(),
          ...(at['refused'] as List? ?? const []).cast<String>(),
        ];
        if (refused.isNotEmpty) {
          out.add(Turn(
            n: turn,
            kind: 'tool',
            at: DateTime.now(),
            title: 'guards  refused ${refused.length}',
            body: refused.map((r) => '· $r').join('\n'),
            ok: false,
          ));
        } else {
          final acc = (at['accepted'] as Map?)?.cast<String, dynamic>();
          final counts = acc == null
              ? ''
              : [
                  for (final k in const ['unknowns', 'tasks', 'proposals', 'rebucket', 'unblock', 'retype'])
                    if ((acc[k] as List? ?? const []).isNotEmpty) '${(acc[k] as List).length} $k',
                ].join(', ');
          out.add(Turn(n: turn, kind: 'tool', at: DateTime.now(), title: 'guards  accepted${counts.isEmpty ? '' : ' — $counts'}'));
          // The step's reflection: the controller's notes for its next step, as a handoff row — the same
          // thing a worker writes at its window's end.
          final memory = (acc?['memory'] as String? ?? '').trim();
          if (memory.isNotEmpty) {
            out.add(Turn(n: turn, kind: 'handoff', at: DateTime.now(), title: 'Writing to memory.md', body: memory, tone: 'pass', anchor: 'left'));
          }
        }
      }
      final applied = (e['applied'] as Map? ?? const {}).cast<String, dynamic>();
      List<String> ids(String k) => (applied[k] as List? ?? const []).cast<String>();
      final stood = <String>[
        if (ids('unknowns').isNotEmpty) 'unknowns: ${ids('unknowns').join(', ')}',
        if (ids('tasks').isNotEmpty) 'work orders: ${ids('tasks').join(', ')}',
        if ((applied['proposals'] as List? ?? const []).isNotEmpty) 'change requests: ${(applied['proposals'] as List).length}',
        if (e['done'] == true) 'brief answered — done',
      ];
      out.add(Turn(
        n: attempts.length,
        kind: 'checkin',
        at: DateTime.now(),
        title: stood.isEmpty ? 'applied nothing' : 'applied — ${stood.join(' · ')}',
      ));
    }
    return out;
  }

  static Map<String, dynamic>? _decisionOf(String raw) {
    final s = raw.indexOf('{'), e = raw.lastIndexOf('}');
    if (s < 0 || e <= s) return null;
    try {
      final j = jsonDecode(raw.substring(s, e + 1));
      return j is Map ? j.cast<String, dynamic>() : null;
    } catch (_) {
      return null;
    }
  }

  /// A window of a worker's chat, read from a byte range of its event log.
  ///
  /// The log is append-only, so a window is stable once read: the caller
  /// keeps older windows and re-reads only the last one. Every
  /// `worker_turn` event is one exchange — the worker's words, its tool
  /// calls with arguments, and each call's result — so nothing is orphaned
  /// at a window edge except the one line the edge cuts, which the
  /// neighbouring window owns. [end] defaults to the file's length.
  /// [from] pins the window's start instead (a live tail anchored where it
  /// was first read, so it grows with the file rather than sliding).
  ///
  /// [rulesOnly] reads the whole range for the rule rows alone (handoffs,
  /// phase lines, markers), streamed a chunk at a time and skipping the
  /// turns' bodies — a 200 MB log in a fraction of a second — so the trace's
  /// rule menu can list the whole session. Each rule is the same row, same
  /// key, the full read makes.
  TraceWindow workerWindow(String taskId, {int? end, int? from, int bytes = tailBytes, bool rulesOnly = false}) {
    final f = File('${session.path}/tasks/$taskId/events/session.jsonl');
    if (!f.existsSync()) return const TraceWindow(turns: [], start: 0, end: 0, fileLength: 0);
    final length = f.lengthSync();
    final to = (end ?? length).clamp(0, length);
    final start = from != null ? from.clamp(0, to) : (to > bytes ? to - bytes : 0);
    final (lines, firstLineAt) = rulesOnly ? (_lineStream(f, start, to), start) : _linesBetween(f, start, to);
    final out = <Turn>[];
    // A reviewer call in flight: its request is on the log, its response is
    // not yet. The reviewer thinks for 20–80 s a call and the trace showed
    // nothing for that stretch — it read as a stall after the worker's claim.
    DateTime? reviewerSince;
    DateTime? workerCallSince;   // a worker model call out with no response yet
    // The window filled and the harness asked the worker to record its
    // method before it rolls: from `reflect_started` to the handoff. Its
    // `done` ends the reflection, not the task.
    var reflecting = false;
    DateTime? compactingSince;   // the handoff call out: the window is being rolled
    int? handoffRule;            // the handoff rule drawn at the request, waiting for the handoff's text
    var window = 0;              // the worker's window, from its turns
    // The handoff rule goes up when the prompt for it is sent, not when the
    // handoff comes back (seconds to minutes later); its text joins it then.
    void handoffSent(DateTime at) {
      compactingSince = at;
      if (handoffRule != null) return;   // one rule per rollover, however many tries
      out.add(Turn(n: _lastTurn(out), kind: 'handoff', at: at, title: 'handoff → window ${window + 1}', tone: 'system'));
      handoffRule = out.length - 1;
    }
    int? hostLine;   // a phase/marker line still waiting for the words the host said
    // The host phase the rows are in, from the labelled continuations seen
    // so far: it decides what "the reviewer accepted" leads to.
    var currentPhase = '';
    for (final line in lines) {
      final type = _typeOf(line);
      if (type == null || !_chat.contains(type)) continue;
      // A model request or response is read for its purpose alone (and, for a
      // request, the host's words a phase line is still waiting for); its body
      // is tens of kilobytes and the trace decoded every one of them, on every
      // tick, for one field that sits near the front of the line.
      if ((type == 'model_request' || type == 'model_response') && (rulesOnly || !(type == 'model_request' && hostLine != null))) {
        final purpose = _fieldOf(line, 'purpose');
        final at = _createdAt(line);
        if (type == 'model_request' && purpose == 'controller') reviewerSince = at;
        if (type == 'model_response' && purpose == 'controller') reviewerSince = null;
        // A worker call in flight, and a call that failed: the wire is part of
        // the trace. A stalled proxy held a turn for fifteen minutes and the
        // trace showed a worker "turning" the whole time.
        if (type == 'model_request' && purpose == 'worker') workerCallSince = at;
        if (type == 'model_request' && purpose == 'handoff') handoffSent(at);
        if (type == 'model_response' && purpose == 'worker') {
          workerCallSince = null;
          final error = _fieldOf(line, 'error');
          if (error != null && error.isNotEmpty && error != 'null') {
            final secs = _fieldOf(line, 'elapsed_seconds') ?? _fieldOf(line, 'seconds');
            out.add(Turn(n: _lastTurn(out), kind: 'outage', at: at,
                title: 'model call failed after ${secs == null ? '?' : double.tryParse(secs)?.round() ?? secs} s — retried',
                body: error, ok: false));
          }
        }
        continue;
      }
      // Rules only: a turn is read for its number and window (the last fields
      // on its line), unless it closed the task on the route — the one rule a
      // turn carries.
      if (rulesOnly && type == 'worker_turn' && !line.contains('terra route complete') && !line.contains('terra_route_complete')) {
        final m = _turnTail.firstMatch(line.length > 240 ? line.substring(line.length - 240) : line);
        if (m != null) {
          out.add(Turn(n: int.parse(m[1]!), kind: '_turn', at: _createdAt(line), title: ''));
          window = int.parse(m[2]!);
        }
        continue;
      }
      Map<String, dynamic> e;
      try {
        e = jsonDecode(line) as Map<String, dynamic>;
      } catch (_) {
        continue; // a line still being written
      }
      final at = DateTime.tryParse(e['created_at'] as String? ?? '') ?? DateTime.now();
      final p = (e['payload'] as Map? ?? const {}).cast<String, dynamic>();
      switch (type) {
        case 'model_request':
          if (p['purpose'] == 'handoff') handoffSent(at);
          if (p['purpose'] == 'controller') {
            reviewerSince = at;
          } else if (hostLine != null && p['purpose'] == 'worker') {
            // Only the worker's own next request carries the host's words: the
            // handoff request's last message is the compaction prompt, and a
            // gate-red line once read as "Write the working memory…".
            // The host's words reach the worker as the last user message of its
            // next request: a journal from before the text rode in the event
            // still has them there.
            final said = _lastUserMessage(p);
            if (said.isNotEmpty) {
              final t = out[hostLine];
              out[hostLine] = Turn(n: t.n, kind: t.kind, at: t.at, title: t.title, body: _phaseBody(t.title, said), ok: t.ok, tone: t.tone, anchor: t.anchor);
              hostLine = null;   // a token estimate logs no body; keep waiting for the request that does
            }
          }
        case 'model_response':
          if (p['purpose'] == 'controller') reviewerSince = null;
        case 'worker_turn':
          window = (p['window'] as num?)?.toInt() ?? window;
          final green = currentPhase.startsWith('gate green') || currentPhase.startsWith('library');
          out.addAll(_exchange(p, at,
              doneAs: reflecting
                  ? 'reflection over → compacting'
                  : green
                  ? 'task finished'
                  : 'worker claims the task is finished'));
        // Two bars for a window rolling: this one when the reflection opens,
        // the handoff when the new window starts. Between them, the live
        // "compacting" row while the handoff is written.
        case 'reflect_started':
          reflecting = true;
          final tokens = (p['prompt_tokens'] as num?)?.toInt();
          final budget = (p['turns'] as num?)?.toInt();
          out.add(Turn(
            n: _lastTurn(out),
            kind: 'phase',
            at: at,
            title: 'reflection: record the method',
            tone: 'hold',
            anchor: 'left',
            body: 'The window is full'
                '${tokens == null ? '' : ' (${(tokens / 1000).round()}K tokens)'}: the worker records its method'
                '${budget == null || budget < 0 ? '' : ' in up to $budget turns'}, then the window is compacted.',
          ));
        case 'tool_rejected':
          out.add(Turn(
            n: _lastTurn(out),
            kind: 'tool',
            at: at,
            title: '${p['name']} refused — ${p['characters']} chars over the ${p['bound']} bound',
            body: p['repeated'] == true ? 'repeated refusal' : '',
            ok: false,
          ));
        // A host continuation or interjection names the phase it opens
        // (`label`): gate red, the write-up after green, a merge. Drawn as a
        // phase line, and everything after it is tinted until the next one —
        // "gate green" used to read as the end while the worker went on
        // reflecting for another twenty turns.
        // "estimate spent" opens nothing: it is a one-line note to the worker
        // (past the bucket's estimate; carry on, change approach, or block) and
        // the work goes on in the phase it was in — a marker, no tint of its own.
        case 'interjected':
          final label = _label(p);
          if (label.startsWith('estimate spent')) {
            out.add(Turn(n: _lastTurn(out), kind: 'milestone', at: at, title: label, body: _hostText(p), tone: 'rose', anchor: 'left'));
            if (_hostText(p).endsWith('from the host')) hostLine = out.length - 1;
            break;
          }
          if (label.isNotEmpty) currentPhase = label;
          out.add(label.isEmpty
              ? Turn(n: _lastTurn(out), kind: 'checkin', at: at, title: 'controller interjected — ${p['characters']} chars')
              : _phaseRule(_lastTurn(out), at, label, _hostText(p)));
          if (label.isNotEmpty && _hostText(p).endsWith('from the host')) hostLine = out.length - 1;
        case 'marked':
          // A line for the watcher only: the worker was told nothing, so the phase it is in does not change.
          final label = (p['label'] as String? ?? '').trim();
          if (label.isNotEmpty) out.add(_phaseRule(_lastTurn(out), at, label, (p['text'] as String? ?? '').trim()));
        case 'continued':
          final label = _label(p);
          if (label.isNotEmpty) currentPhase = label;
          out.add(label.isEmpty
              ? Turn(n: _lastTurn(out), kind: 'checkin', at: at, title: 'controller: continue — ${p['characters']} chars')
              : _phaseRule(_lastTurn(out), at, label, _hostText(p)));
          if (label.isNotEmpty && _hostText(p).endsWith('from the host')) hostLine = out.length - 1;
        case 'worker_handoff':
          reflecting = false;
          compactingSince = null;
          final written = Turn(
            n: handoffRule == null ? _lastTurn(out) : out[handoffRule!].n,
            kind: 'handoff',
            at: handoffRule == null ? at : out[handoffRule!].at,
            title: 'handoff → window ${p['window_index']}',
            body: (p['handoff'] as String? ?? '').trim(),
            tone: 'system',
          );
          if (handoffRule == null) {
            out.add(written);   // its request was before this window of the log
          } else {
            out[handoffRule!] = written;
          }
          handoffRule = null;
          window = (p['window_index'] as num?)?.toInt() ?? window + 1;
        // The reviewer (the check-in seat) at a boundary: what it decided
        // about the worker's probes. A completion claim it sends back is
        // why a task the route already closed keeps working — "successful"
        // on the route is not the end of the session; the reviewer's
        // acceptance is.
        case 'controller_review':
          out.add(_review(p, at, _lastTurn(out), phase: currentPhase));
        case 'controller_decision_rejected':
          out.add(Turn(
            n: _lastTurn(out),
            kind: 'review',
            at: at,
            title: 'reviewer replied nothing usable (${p['error']}) — held',
            body: (p['content'] as String? ?? '').trim(),
          ));
      }
    }
    if (rulesOnly) {
      return TraceWindow(
        turns: [for (final t in out) if (t.kind == 'handoff' || t.kind == 'phase' || t.kind == 'milestone') t],
        start: firstLineAt, end: to, fileLength: length);
    }
    // The ending: the worker's report is written and the work order goes
    // back to the controller. Every finished trace has a last line.
    final closed = to == length ? _closing(taskId) : null;
    if (closed != null) {
      out.add(Turn(n: _lastTurn(out), kind: 'milestone', at: closed.at, title: closed.title, body: closed.body, tone: closed.tone));
      return TraceWindow(turns: out, start: firstLineAt, end: to, fileLength: length);
    }
    final compacting = compactingSince;   // assigned in a closure: no promotion
    if (compacting != null && to == length) {
      // The handoff is being written: the old window's last model call. It
      // takes seconds to minutes and nothing else moves meanwhile.
      out.add(Turn(n: _lastTurn(out), kind: 'live', at: compacting, title: 'compacting'));
    }
    if (workerCallSince != null && to == length && reviewerSince == null) {
      // A worker call out with no reply on the log: one live row, dated at
      // the call. The screen keeps the clock and reads the wire (bytes
      // arriving, or not) from the window it polls; nothing worded here
      // would stay true for two seconds.
      out.add(Turn(n: _lastTurn(out), kind: 'live', at: workerCallSince, title: 'worker'));
    }
    if (reviewerSince != null && to == length) {
      // The log does not move while the call is out, so no live count here:
      // the row's time is when the reviewer started looking.
      out.add(Turn(n: _lastTurn(out), kind: 'live', at: reviewerSince, title: 'reviewer'));
    }
    return TraceWindow(turns: out, start: firstLineAt, end: to, fileLength: length);
  }

  static Turn _review(Map<String, dynamic> p, DateTime at, int n, {String phase = ''}) {
    final d = (p['decision'] as Map? ?? const {}).cast<String, dynamic>();
    final boundary = p['boundary'] as String? ?? 'periodic';
    final correction = (d['correction'] as String? ?? '').trim();
    final op = d['operation'] as String? ?? (correction.isEmpty ? 'withdraw' : correction == 'None' ? 'hold' : 'replace');
    final atClaim = boundary == 'completion';
    // What a claim that stands leads to depends on the phase: in the work it
    // is the host's gate over the map; in the write-up it is the harvest into
    // the library; in a library round it is the check-in of what was fixed.
    final lower = phase.toLowerCase();
    final next = lower.startsWith('gate green')
        ? 'the write-up stands — harvesting into the library'
        : lower.startsWith('library')
        ? 'the fix stands — checking it in'
        : 'the claim stands — the host gate checks the map';
    final spent = p['termination'] == 'completion_corrections_spent';
    final String title;
    final bool ok;
    switch (op) {
      case 'replace':
        title = atClaim
            ? 'reviewer · sent the claim back — $correction'
            : 'reviewer · correction — $correction';
        ok = false;
      case 'clear' || 'withdraw':
        title = atClaim ? 'reviewer · withdrew its correction; $next' : 'reviewer · correction withdrawn';
        ok = true;
      default:
        title = atClaim
            ? (spent
                ? 'reviewer · still doubted the probe but its send-backs are spent; $next (the doubt goes to the controller)'
                : p['termination'] == 'malformed_decisions_held'
                ? 'reviewer · gave no usable decision; $next'
                : 'reviewer · no objection; $next')
            : 'reviewer · looked, nothing to correct';
        ok = true;
    }
    final body = [
      if ((d['evidence'] as String? ?? '').trim().isNotEmpty) 'evidence: ${d['evidence']}',
      if ((d['warrant'] as String? ?? '').trim().isNotEmpty) 'warrant: ${d['warrant']}',
      'boundary: $boundary · model calls: ${p['model_calls'] ?? '-'} · ${p['termination'] ?? ''}',
    ].join('\n');
    return Turn(n: n, kind: 'review', at: at, title: title, body: body, ok: ok);
  }

  static const _chat = {'marked', 'reflect_started', 'worker_turn', 'tool_rejected', 'interjected', 'continued', 'worker_handoff', 'controller_review', 'controller_decision_rejected', 'model_request', 'model_response'};

  /// What the host said when it continued or interjected: the text itself
  /// when the journal carries it (engine c1947d3+), else how much of it there
  /// was. A gate-red line is the reason the gate went red.
  static String _lastUserMessage(Map<String, dynamic> p) {
    final body = p['body'];
    final messages = body is Map ? body['messages'] : null;
    if (messages is! List) return '';
    for (final m in messages.reversed) {
      if (m is Map && m['role'] == 'user' && m['content'] is String) return (m['content'] as String).trim();
    }
    return '';
  }

  static String _hostText(Map<String, dynamic> p) {
    final text = (p['text'] as String? ?? '').trim();
    return text.isNotEmpty ? text : '${p['characters']} chars from the host';
  }

  static int _lastTurn(List<Turn> out) => out.isEmpty ? 0 : out.last.n;

  /// One worker_turn as chat: the worker's words, then each tool call with
  /// its result folded under it.
  static List<Turn> _exchange(Map<String, dynamic> p, DateTime at, {String doneAs = ''}) {
    final n = (p['turn'] as num?)?.toInt() ?? 0;
    final r = (p['response'] as Map? ?? const {}).cast<String, dynamic>();
    final out = <Turn>[];
    // The model's thinking, when the provider returned it (a reasoning
    // summary, or a local model's thinking block).
    final thought = (r['reasoning_content'] as String? ?? '').trim();
    if (thought.isNotEmpty) out.add(Turn(n: n, kind: 'thought', at: at, title: _firstLine(thought), body: thought));
    final content = (r['content'] as String? ?? '').trim();
    if (content.isNotEmpty) out.add(Turn(n: n, kind: 'note', at: at, title: _firstLine(content), body: content));
    final results = <String, Map<String, dynamic>>{
      for (final tr in (p['tool_results'] as List? ?? const []))
        if ((tr as Map)['call_id'] is String)
          tr['call_id'] as String: ((tr['result'] as Map?) ?? const {}).cast<String, dynamic>(),
    };
    for (final tc in (r['tool_calls'] as List? ?? const [])) {
      final call = (tc as Map).cast<String, dynamic>();
      final fn = ((call['function'] as Map?) ?? call).cast<String, dynamic>();
      final name = fn['name'] as String? ?? 'tool';
      final args = _args(fn['arguments']);
      final res = results[call['id']];
      final ok = res == null || (res['status'] == null || res['status'] == 'ok') &&
          (res['exit_code'] == null || res['exit_code'] == 0 || res['exit_code'] == '0') && res['timed_out'] != true;
      final detail = res == null ? '' : _result(res);
      final isEdit = name == 'edit' && args['old_text'] is String && args['new_text'] is String;
      final isWrite = name == 'write' && args['content'] is String;
      // `done` ends a round, and which round is the whole of its meaning:
      // the reflection before compaction, a claim the task is finished, or
      // the finished task's last round. Its summary follows the label.
      final summary = args['summary'] is String ? (args['summary'] as String).trim() : '';
      final path = args['path'] ?? args['file_path'];
      final fileLang = path is String ? languageForPath(path) : null;
      out.add(Turn(
        n: n,
        kind: 'tool',
        at: at,
        title: name == 'done' && doneAs.isNotEmpty
            ? 'done · $doneAs${summary.isEmpty ? '' : ' — ${_firstLine(summary)}'}'
            : '$name  ${_firstLine(_headline(name, args))}',
        body: isEdit
            ? (detail.isEmpty ? '' : '→ $detail')
            : [isWrite ? args['content'] as String : _pretty(args), if (detail.isNotEmpty) '\n→ $detail'].join('\n'),
        ok: ok,
        before: isEdit ? args['old_text'] as String : null,
        after: isEdit ? args['new_text'] as String : (isWrite ? args['content'] as String : null),
        lang: name == 'bash' ? 'bash' : (isWrite ? fileLang : null),
        resultLang: name == 'read' ? fileLang : null,
      ));
      // The route closed the task here. The session goes on: the reviewer
      // must accept the completion claim, and the write-up after green is
      // still to come — so this is a marker, not the end of the trace.
      final stdout = '${res?['stdout'] ?? ''}';
      final completed = ok &&
          ((name == 'terra_route_complete') ||
              (name == 'bash' && RegExp(r'terra\s+route\s+complete\b').hasMatch('${args['command'] ?? ''}'))) &&
          stdout.contains('"status": "success"');
      if (completed) {
        out.add(Turn(
          n: n,
          kind: 'milestone',
          at: at,
          title: 'worker labels task done - reviewer confirming',
          tone: 'hold',
          anchor: 'left',
        ));
      }
    }
    return out;
  }

  static Map<String, dynamic> _args(Object? raw) {
    if (raw is Map) return raw.cast<String, dynamic>();
    if (raw is String) {
      try {
        final j = jsonDecode(raw);
        if (j is Map) return j.cast<String, dynamic>();
      } catch (_) {}
      return {'_': raw};
    }
    return const {};
  }

  /// The one argument that says what the call was: the command, the path,
  /// the query — whichever the tool has.
  static String _headline(String name, Map<String, dynamic> a) {
    for (final k in const ['command', 'path', 'file_path', 'query', 'title', 'text', 'name', 'id', '_']) {
      final v = a[k];
      if (v is String && v.trim().isNotEmpty) return v.trim();
    }
    return a.isEmpty ? '' : a.keys.join(', ');
  }

  static String _pretty(Map<String, dynamic> a) {
    if (a.length == 1 && a.values.first is String) return a.values.first as String;
    try {
      return const JsonEncoder.withIndent('  ').convert(a);
    } catch (_) {
      return '$a';
    }
  }

  static String _result(Map<String, dynamic> res) {
    final parts = <String>[];
    for (final k in const ['stdout', 'content', 'output', 'detail', 'error']) {
      final v = res[k];
      if (v is String && v.trim().isNotEmpty) parts.add(v.trimRight());
    }
    final err = res['stderr'];
    if (err is String && err.trim().isNotEmpty) parts.add('stderr:\n${err.trimRight()}');
    final code = res['exit_code'];
    if (code != null && code != 0 && code != '0') parts.add('exit $code');
    if (res['timed_out'] == true) parts.add('timed out');
    return parts.join('\n');
  }

  /// Complete lines in [from, to): the first partial line (when [from] is
  /// mid-line) is dropped and the offset of the first complete one returned.
  static final _turnTail = RegExp(r'"turn": (\d+), "window": (\d+)\}');

  /// The complete lines in [from, to), read a chunk at a time so a whole log
  /// is never one string in memory. As [_linesBetween]: a line cut by [from]
  /// is dropped; a last line with no newline yet is given (it will not decode).
  static Iterable<String> _lineStream(File f, int from, int to) sync* {
    const chunk = 8 * 1024 * 1024;
    final raf = f.openSync();
    try {
      raf.setPositionSync(from);
      var pos = from;
      var skip = from > 0;
      var carry = <int>[];
      while (pos < to) {
        final bytes = raf.readSync((to - pos).clamp(0, chunk));
        if (bytes.isEmpty) break;
        pos += bytes.length;
        var at = 0;
        while (true) {
          final nl = bytes.indexOf(10, at);
          if (nl < 0) break;
          final piece = carry.isEmpty ? bytes.sublist(at, nl) : (carry..addAll(bytes.sublist(at, nl)));
          carry = <int>[];
          at = nl + 1;
          if (skip) {
            skip = false;
            continue;
          }
          if (piece.isNotEmpty) yield utf8.decode(piece, allowMalformed: true);
        }
        carry.addAll(bytes.sublist(at));
      }
      if (!skip && carry.isNotEmpty) yield utf8.decode(carry, allowMalformed: true);
    } finally {
      raf.closeSync();
    }
  }

  static (List<String>, int) _linesBetween(File f, int from, int to) {
    final raf = f.openSync();
    try {
      raf.setPositionSync(from);
      final bytes = raf.readSync(to - from);
      final text = utf8.decode(bytes, allowMalformed: true);
      var firstAt = from;
      var body = text;
      if (from > 0) {
        final nl = text.indexOf('\n');
        if (nl < 0) return (const [], to);
        // byte length of the dropped fragment, not char length
        firstAt = from + utf8.encode(text.substring(0, nl + 1)).length;
        body = text.substring(nl + 1);
      }
      return (body.split('\n').where((l) => l.isNotEmpty).toList(), firstAt);
    } finally {
      raf.closeSync();
    }
  }

  /// The most recent window: what the live view tails.
  List<Turn> workerTurns(String taskId) => workerWindow(taskId).turns;

  /// A short string field near the front of a journal line, without decoding
  /// the line: `"purpose": "controller"`. Null when it is not in the first 400
  /// characters (the payload's big fields come after).
  /// The reply's progress on the wire, when the session has written it for
  /// the call now out (`stream.json` is overwritten per chunk; one older than
  /// the call belongs to a previous call).

  static String? _fieldOf(String line, String name) {
    // The value may be a string or a number: `"error": "Timeout…"` or `"elapsed_seconds": 941.5`.
    var marker = '"$name": "';
    var i = line.indexOf(marker);
    if (i < 0) {
      final bare = '"$name": ';
      final j = line.indexOf(bare);
      if (j >= 0 && j <= 400) {
        final s = j + bare.length;
        var e = s;
        while (e < line.length && '0123456789.-'.contains(line[e])) {
          e++;
        }
        return e > s ? line.substring(s, e) : null;
      }
    }
    if (i < 0 || i > 400) {
      // A request that carries its body has `purpose` after it, near the end
      // of the line (path, purpose, request_id, transport follow the body).
      final tail = line.length > 800 ? line.length - 800 : 0;
      i = line.lastIndexOf(marker);
      if (i < 0 || i < tail) return null;
    }
    final s = i + marker.length;
    final e = line.indexOf('"', s);
    return e < 0 ? null : line.substring(s, e);
  }

  static DateTime _createdAt(String line) =>
      DateTime.tryParse(_fieldOf(line, 'created_at') ?? '') ?? DateTime.now();

  /// The event type without parsing the line: it sits in a fixed spot.
  static String? _typeOf(String line) {
    const marker = '"event_type": "';
    final i = line.indexOf(marker);
    if (i < 0 || i > 200) return null;
    final s = i + marker.length;
    final e = line.indexOf('"', s);
    return e < 0 ? null : line.substring(s, e);
  }

  static String _firstLine(String s) {
    final i = s.indexOf('\n');
    final line = i < 0 ? s : s.substring(0, i);
    return line.length > 160 ? '${line.substring(0, 160)}…' : line;
  }
}
