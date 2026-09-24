import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/deputy_activity.dart';

String _ev(String type, Map<String, dynamic> payload, String at) =>
    jsonEncode({'created_at': at, 'event_id': 'e', 'event_type': type, 'payload': payload, 'session_id': 'session'});

String _response(String purpose, {String content = '', List<Map<String, dynamic>> calls = const [], double secs = 1.0}) =>
    jsonEncode({
      'purpose': purpose,
      'status': 200,
      'error': null,
      'elapsed_seconds': secs,
      'body': jsonEncode({
        'choices': [
          {'message': {'role': 'assistant', 'content': content, 'tool_calls': calls}},
        ],
      }),
    });

void main() {
  test('a turn is read off the journal step by step, live and finished', () async {
    final dir = await Directory.systemTemp.createTemp('deputy');
    final f = File('${dir.path}/session.jsonl');
    final lines = [
      _ev('checkpoint', {'revision': 1}, '2026-09-21T02:14:00Z'),
      _ev('continued', {'characters': 35, 'window': 0}, '2026-09-21T02:14:09Z'),
      _ev('model_request', {'purpose': 'worker:estimate'}, '2026-09-21T02:14:09Z'),
      _ev('rollover_forced', {'reason': 'rebind', 'name': 'worker', 'count': 0}, '2026-09-21T02:14:09Z'),
      _ev('model_request', {'purpose': 'handoff'}, '2026-09-21T02:14:12Z'),
      jsonEncode({'created_at': '2026-09-21T02:17:05Z', 'event_type': 'model_response', 'payload': jsonDecode(_response('handoff', content: 'memory', secs: 172.7))}),
      _ev('worker_handoff', {'handoff': 'memory'}, '2026-09-21T02:17:05Z'),
      _ev('model_request', {'purpose': 'worker'}, '2026-09-21T02:17:05Z'),
      jsonEncode({
        'created_at': '2026-09-21T02:17:10Z',
        'event_type': 'model_response',
        'payload': jsonDecode(_response('worker', secs: 5.1, calls: [
          {'id': 'c1', 'type': 'function', 'function': {'name': 'draft_show', 'arguments': '{"slug": "counting-a-bar"}'}},
          {'id': 'c2', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"command": "cd /work/counting-a-bar && terra brief show"}'}},
        ])),
      }),
      _ev('command_tool', {'call_id': 'c1', 'name': 'draft_show', 'command': 'python -m mizpah.draft show counting-a-bar'}, '2026-09-21T02:17:10Z'),
      _ev('tool_outcome', {'call_id': 'c1', 'status': 'ok', 'exit_code': 0, 'elapsed_seconds': 5.0, 'stdout': '{"status": "ok"}\n'}, '2026-09-21T02:17:15Z'),
    ];
    await f.writeAsString('${lines.join('\n')}\n');
    final live = DeputyActivity(f).current();
    expect(live.map((s) => s.kind).toList(), ['note', 'memory', 'note', 'model', 'call', 'call']);
    expect(live[1].text, contains('compaction'));
    expect(live[1].seconds, 172.7);
    expect(live[3].done, isTrue);
    expect(live[4].text, 'draft_show  python -m mizpah.draft show counting-a-bar');
    expect(live[4].done, isTrue);
    expect(live[4].detail, '{"status": "ok"}');
    expect(live[5].text, 'bash  cd /work/counting-a-bar && terra brief show');
    expect(live[5].done, isFalse, reason: 'no outcome yet: in flight');
    // The second call fails; the model answers; the turn closes.
    await f.writeAsString([
      _ev('tool_outcome', {'call_id': 'c2', 'status': 'ok', 'exit_code': 1, 'elapsed_seconds': 4.9, 'stdout': '', 'stderr': 'TERRA ERROR: no brief'}, '2026-09-21T02:17:20Z'),
      _ev('model_request', {'purpose': 'worker'}, '2026-09-21T02:17:20Z'),
      jsonEncode({'created_at': '2026-09-21T02:17:25Z', 'event_type': 'model_response', 'payload': jsonDecode(_response('worker', content: 'Pulled up.\nMore.', secs: 5.0))}),
      '',
    ].join('\n'), mode: FileMode.append);
    final done = DeputyActivity(f).current();
    expect(done.length, 7);
    expect(done[5].ok, isFalse);
    expect(done[5].detail, 'exit 1: TERRA ERROR: no brief');
    expect(done[6].detail, 'Pulled up.');
    expect(done.every((s) => s.done), isTrue);
    // The next turn starts a new list; the earlier one stays readable.
    await f.writeAsString('${_ev('continued', {'characters': 3, 'window': 1}, '2026-09-21T02:20:00Z')}\n${_ev('model_request', {'purpose': 'worker'}, '2026-09-21T02:20:00Z')}\n', mode: FileMode.append);
    final turns = DeputyActivity(f).all();
    expect(turns.length, 2);
    expect(turns.last.single.done, isFalse);
    expect(DeputyActivity(f).current().single.text, 'thinking');
    // Wire round trip.
    final j = done[5].toJson();
    final back = DeputyStep.fromJson(jsonDecode(jsonEncode(j)) as Map<String, dynamic>);
    expect(back.text, done[5].text);
    expect(back.ok, isFalse);
    expect(back.seconds, 4.9);
    await dir.delete(recursive: true);
  });
}
