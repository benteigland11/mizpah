import 'package:flutter_test/flutter_test.dart';

import 'package:run_liveness/run_liveness.dart';

/// A console reading a worker's `run.json` decides what to show next to
/// its name. The writer beats every ten seconds; three missed beats and it
/// is presumed gone. Records from before heartbeats existed fall back to a
/// pid probe the console supplies, or the file's mtime.
void main() {
  test('example: four records on a status board', () {
    final now = DateTime.utc(2030, 5, 4, 9, 30);
    double ago(Duration d) => now.subtract(d).millisecondsSinceEpoch / 1000;

    final board = {
      'beating': {'pid': 101, 'heartbeat_at': ago(const Duration(seconds: 4)), 'ended_at': null},
      'silent': {'pid': 102, 'heartbeat_at': ago(const Duration(minutes: 3)), 'ended_at': null},
      'finished': {'pid': 103, 'heartbeat_at': ago(const Duration(hours: 1)), 'ended_at': ago(const Duration(minutes: 50))},
      'legacy': {'pid': 104, 'ended_at': null},
    };
    // The console's own probe for legacy records — here, a fake table.
    bool probe(int pid) => pid == 104;

    final shown = {
      for (final e in board.entries)
        e.key: judgeLiveness(e.value, now: now, pidAlive: probe),
    };
    expect(shown, {
      'beating': RunLiveness.live,
      'silent': RunLiveness.stale,
      'finished': RunLiveness.ended,
      'legacy': RunLiveness.live,
    });
  });
}
