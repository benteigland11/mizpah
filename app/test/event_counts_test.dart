import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/run_inbox.dart';

void main() {
  test('counts are exact across appends, split markers and a restart', () {
    final dir = Directory.systemTemp.createTempSync('ev');
    addTearDown(() => dir.deleteSync(recursive: true));
    final f = File('${dir.path}/session.jsonl');
    String line(String kind) => '{"event_type": "$kind", "payload": {"x": "${'y' * 40}"}}\n';
    f.writeAsStringSync(line('worker_turn') * 3 + line('tool_call'));
    expect(RunInbox.eventCounts(f), {'worker_turn': 3, 'tool_call': 1});
    // Append in pieces, including half a marker, and count again each time.
    final more = line('worker_turn');
    f.writeAsStringSync(more.substring(0, 9), mode: FileMode.append);
    expect(RunInbox.eventCounts(f), {'worker_turn': 3, 'tool_call': 1});
    f.writeAsStringSync(more.substring(9), mode: FileMode.append);
    expect(RunInbox.eventCounts(f), {'worker_turn': 4, 'tool_call': 1});
    // Enough to cross a chunk boundary, then compare with a from-scratch count.
    f.writeAsStringSync(line('checkpoint') * 30000, mode: FileMode.append);
    final incremental = RunInbox.eventCounts(f);
    File('${f.path}.counts.json').deleteSync();
    expect(RunInbox.eventCounts(f), incremental);
    expect(incremental['checkpoint'], 30000);
  });
}
