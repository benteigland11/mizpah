import 'package:flutter_test/flutter_test.dart';

import 'package:run_liveness/run_liveness.dart';

void main() {
  final now = DateTime.utc(2030, 1, 1, 12, 0, 0);
  double epoch(DateTime t) => t.millisecondsSinceEpoch / 1000;

  test('ended_at set means ended, whatever the heartbeat says', () {
    final r = {'heartbeat_at': epoch(now), 'ended_at': epoch(now), 'pid': 42};
    expect(judgeLiveness(r, now: now), RunLiveness.ended);
  });

  test('a recent heartbeat is live, a lapsed one is stale', () {
    final fresh = {'heartbeat_at': epoch(now.subtract(const Duration(seconds: 25)))};
    final lapsed = {'heartbeat_at': epoch(now.subtract(const Duration(seconds: 31)))};
    expect(judgeLiveness(fresh, now: now), RunLiveness.live);
    expect(judgeLiveness(lapsed, now: now), RunLiveness.stale);
  });

  test('the grace window follows the policy', () {
    final r = {'heartbeat_at': epoch(now.subtract(const Duration(seconds: 50)))};
    const loose = LivenessPolicy(interval: Duration(seconds: 30), toleranceBeats: 2);
    expect(judgeLiveness(r, now: now, policy: loose), RunLiveness.live);
    expect(loose.grace, const Duration(seconds: 60));
  });

  test('ISO-8601 heartbeats are accepted', () {
    final r = {'heartbeat_at': now.subtract(const Duration(seconds: 5)).toIso8601String()};
    expect(judgeLiveness(r, now: now), RunLiveness.live);
  });

  test('legacy record: the pid probe decides when given', () {
    final r = {'pid': 7, 'ended_at': null};
    expect(judgeLiveness(r, now: now, pidAlive: (p) => p == 7), RunLiveness.live);
    expect(judgeLiveness(r, now: now, pidAlive: (_) => false), RunLiveness.stale);
  });

  test('legacy record: last write within the window is live', () {
    final r = <String, dynamic>{};
    expect(
      judgeLiveness(r, now: now, lastWrite: now.subtract(const Duration(minutes: 2))),
      RunLiveness.live,
    );
    expect(
      judgeLiveness(r, now: now, lastWrite: now.subtract(const Duration(minutes: 20))),
      RunLiveness.stale,
    );
  });

  test('nothing to judge by is unknown, not a guess', () {
    expect(judgeLiveness(const {}, now: now), RunLiveness.unknown);
    expect(judgeLiveness({'pid': 'not-a-number'}, now: now, pidAlive: (_) => true), RunLiveness.unknown);
  });

  test('field names are configurable', () {
    final r = {'beat': epoch(now), 'stopped': null};
    expect(
      judgeLiveness(r, now: now, heartbeatKey: 'beat', endedKey: 'stopped'),
      RunLiveness.live,
    );
  });

  test('parseTimestamp handles the three shapes', () {
    expect(parseTimestamp(0), DateTime.utc(1970));
    expect(parseTimestamp(1.5)!.millisecondsSinceEpoch, 1500);
    expect(parseTimestamp('2030-01-01T12:00:00Z'), now);
    expect(parseTimestamp('nonsense'), isNull);
    expect(parseTimestamp(null), isNull);
    expect(parseTimestamp(true), isNull);
  });
}
