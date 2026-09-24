import 'dart:convert';
import 'dart:io';

/// One thing the Deputy did, or is doing, in the turn under way: a model
/// call, a tool call with its command, a compaction. Read off the session
/// journal as it is written, so the office shows the step in flight and
/// how long it has been there, not just "working".
class DeputyStep {
  const DeputyStep({
    required this.at,
    required this.kind,
    required this.text,
    this.done = true,
    this.seconds,
    this.ok = true,
    this.detail = '',
  });

  final DateTime at;

  /// `model` — a request to the model (thinking) · `memory` — the handoff
  /// that writes its working memory (a compaction) · `call` — a tool call ·
  /// `note` — the engine's own remark (a discarded call, a rebind).
  final String kind;
  final String text;

  /// False while the step is still in flight.
  final bool done;

  /// Elapsed once done; the screen counts from [at] while not.
  final double? seconds;
  final bool ok;

  /// A tool's first line of output, the reply's first line.
  final String detail;

  DeputyStep finish({double? seconds, bool ok = true, String detail = ''}) =>
      DeputyStep(at: at, kind: kind, text: text, done: true, seconds: seconds, ok: ok, detail: detail);

  Map<String, dynamic> toJson() => {
    'at': at.toUtc().toIso8601String(),
    'kind': kind,
    'text': text,
    'done': done,
    if (seconds != null) 'seconds': seconds,
    'ok': ok,
    if (detail.isNotEmpty) 'detail': detail,
  };

  factory DeputyStep.fromJson(Map<String, dynamic> j) => DeputyStep(
    at: DateTime.tryParse(j['at'] as String? ?? '')?.toLocal() ?? DateTime.now(),
    kind: j['kind'] as String? ?? 'note',
    text: j['text'] as String? ?? '',
    done: j['done'] != false,
    seconds: (j['seconds'] as num?)?.toDouble(),
    ok: j['ok'] != false,
    detail: j['detail'] as String? ?? '',
  );
}

/// The steps of the current turn — from the last time the person spoke
/// (`continued` / `interjected`, or the session's start) to the end of the
/// journal — and, for the record, the steps of any earlier turn.
class DeputyActivity {
  DeputyActivity(this.journal);
  final File journal;

  /// How much of the journal's tail to read. A `worker_handoff` line can
  /// run to a few hundred KB (it carries the archived window), so this is
  /// generous for one turn and cheap enough to re-read every second.
  static const tailBytes = 8 * 1024 * 1024;

  /// Steps of the turn under way (the last one on the journal).
  List<DeputyStep> current() {
    final turns = _turns(_tailLines());
    return turns.isEmpty ? const [] : turns.last;
  }

  /// Every turn's steps on the tail, oldest first.
  List<List<DeputyStep>> all() => _turns(_tailLines());

  List<String> _tailLines() {
    if (!journal.existsSync()) return const [];
    final length = journal.lengthSync();
    final start = length > tailBytes ? length - tailBytes : 0;
    final raf = journal.openSync();
    try {
      raf.setPositionSync(start);
      final bytes = raf.readSync(length - start);
      var text = utf8.decode(bytes, allowMalformed: true);
      if (start > 0) {
        // Drop the line the cut fell inside.
        final nl = text.indexOf('\n');
        text = nl < 0 ? '' : text.substring(nl + 1);
      }
      return text.split('\n');
    } finally {
      raf.closeSync();
    }
  }

  static const _wanted = {
    'model_request', 'model_response', 'command_tool', 'tool_outcome', 'rollover_forced', 'worker_handoff',
    'pending_discarded', 'rebound', 'continued', 'interjected', 'stopped',
  };

  /// Cheap type sniff before decoding: most lines are checkpoints.
  static final _type = RegExp(r'"event_type":\s*"([^"]*)"');
  static String? _typeOf(String line) => _type.firstMatch(line)?.group(1);

  List<List<DeputyStep>> _turns(List<String> lines) {
    final turns = <List<DeputyStep>>[[]];
    // Open steps by their key: a model request by purpose, a tool call by id.
    final open = <String, int>{};
    void add(DeputyStep s) => turns.last.add(s);
    void close(String key, DeputyStep Function(DeputyStep) f) {
      final i = open.remove(key);
      if (i == null || i >= turns.last.length) return;
      turns.last[i] = f(turns.last[i]);
    }

    for (final line in lines) {
      final type = _typeOf(line);
      if (type == null || !_wanted.contains(type)) continue;
      Map<String, dynamic> e;
      try {
        e = jsonDecode(line) as Map<String, dynamic>;
      } catch (_) {
        continue; // a line still being written
      }
      final at = DateTime.tryParse(e['created_at'] as String? ?? '')?.toLocal() ?? DateTime.now();
      final p = (e['payload'] as Map? ?? const {}).cast<String, dynamic>();
      switch (type) {
        case 'continued':
        case 'interjected':
          if (turns.last.isNotEmpty) turns.add([]);
          open.clear();
        case 'model_request':
          final purpose = p['purpose'] as String? ?? '';
          if (purpose.contains(':')) continue; // token estimates
          final memory = purpose == 'handoff';
          open['model:$purpose'] = turns.last.length;
          add(DeputyStep(
            at: at,
            kind: memory ? 'memory' : 'model',
            text: memory ? 'writing its working memory (compaction)' : 'thinking',
            done: false,
          ));
        case 'model_response':
          final purpose = p['purpose'] as String? ?? '';
          if (purpose.contains(':')) continue;
          final secs = (p['elapsed_seconds'] as num?)?.toDouble();
          final failed = p['error'] != null || (p['status'] as int? ?? 200) >= 400;
          final (content, calls) = _message(p['body']);
          close('model:$purpose', (s) => s.finish(
                seconds: secs,
                ok: !failed,
                detail: failed ? '${p['error'] ?? p['status']}' : (calls.isEmpty ? _firstLine(content) : ''),
              ));
          if (purpose == 'worker') {
            for (final (id, name, args) in calls) {
              open['call:$id'] = turns.last.length;
              add(DeputyStep(at: at, kind: 'call', text: _callLine(name, args), done: false));
            }
          }
        case 'command_tool':
          // The typed tool rendered to its command: show the command.
          final i = open['call:${p['call_id']}'];
          if (i != null && i < turns.last.length) {
            final s = turns.last[i];
            turns.last[i] = DeputyStep(at: s.at, kind: s.kind, text: '${p['name']}  ${p['command']}', done: false);
          }
        case 'tool_outcome':
          final secs = (p['elapsed_seconds'] as num?)?.toDouble();
          final status = p['status'] as String? ?? '';
          final code = p['exit_code'];
          final ok = status == 'ok' && (code == null || code == 0);
          final out = _firstLine((p['stdout'] as String?)?.trim().isNotEmpty == true
              ? p['stdout'] as String
              : (p['stderr'] as String? ?? p['error'] as String? ?? p['detail'] as String? ?? ''));
          close('call:${p['call_id']}', (s) => s.finish(seconds: secs, ok: ok, detail: ok ? out : '${status == 'ok' ? 'exit $code' : status}: $out'));
        case 'rollover_forced':
          add(DeputyStep(at: at, kind: 'note', text: 'compaction: its bindings changed (${p['reason']}), the window is rewritten first'));
        case 'worker_handoff':
          add(DeputyStep(at: at, kind: 'note', text: 'memory written; continuing in a fresh window'));
        case 'pending_discarded':
          add(DeputyStep(at: at, kind: 'note', text: 'a call the model never answered was discarded', ok: false));
        case 'rebound':
          final what = [
            if (p['worker'] == true) 'model',
            if (p['generation'] != null) 'generation',
            if (p['system_text'] == true) 'instructions',
            if (p['shell'] == true) 'sandbox',
          ];
          if (what.isNotEmpty) add(DeputyStep(at: at, kind: 'note', text: 'seat rebound: ${what.join(', ')} changed'));
        case 'stopped':
          add(DeputyStep(at: at, kind: 'note', text: 'stopped by the STOP file', ok: false));
      }
    }
    return turns.where((t) => t.isNotEmpty).toList();
  }

  /// The assistant message of a response body: its text and its tool
  /// calls as (id, name, arguments).
  static (String, List<(String, String, Map<String, dynamic>)>) _message(Object? body) {
    Map<String, dynamic>? b;
    try {
      b = body is String ? jsonDecode(body) as Map<String, dynamic>? : (body as Map?)?.cast<String, dynamic>();
    } catch (_) {
      return ('', const []);
    }
    final choices = b?['choices'] as List?;
    final m = (choices?.firstOrNull as Map?)?['message'] as Map?;
    if (m == null) return ('', const []);
    final calls = <(String, String, Map<String, dynamic>)>[];
    for (final c in (m['tool_calls'] as List? ?? const [])) {
      final f = (c as Map)['function'] as Map? ?? const {};
      Map<String, dynamic> args = const {};
      try {
        final a = f['arguments'];
        args = a is String ? (jsonDecode(a) as Map).cast<String, dynamic>() : (a as Map? ?? const {}).cast<String, dynamic>();
      } catch (_) {}
      calls.add(('${c['id']}', '${f['name']}', args));
    }
    return ('${m['content'] ?? ''}', calls);
  }

  static String _callLine(String name, Map<String, dynamic> args) {
    if (name == 'bash') return 'bash  ${args['command'] ?? ''}';
    if (name == 'read') return 'read  ${args['path'] ?? ''}';
    final short = args.entries.map((e) => '${e.key}=${_clip('${e.value}', 60)}').join(' ');
    return '$name  $short';
  }

  static String _firstLine(String s) {
    final t = s.trim();
    final nl = t.indexOf('\n');
    return _clip(nl < 0 ? t : t.substring(0, nl), 160);
  }

  static String _clip(String s, int n) => s.length <= n ? s : '${s.substring(0, n - 1)}…';
}
