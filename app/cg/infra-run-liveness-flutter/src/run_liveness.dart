/// Whether a long-running process is still there, judged from the record
/// it keeps on disk rather than by asking the operating system.
///
/// The writer stamps `heartbeat_at` every [LivenessPolicy.interval] while
/// it runs and `ended_at` once when it stops. A reader on any platform
/// then decides without `/proc`, `kill -0` or `OpenProcess`:
///
/// - **ended** — `ended_at` is set.
/// - **live** — the last heartbeat is within the tolerance.
/// - **stale** — heartbeats stopped without an `ended_at`: crashed or
///   killed.
/// - **unknown** — the record predates heartbeats and no fallback was
///   given.
///
/// Pure: the record is a map, the clock is an argument, and the only
/// side-effectful step (a pid probe) is an optional callback the caller
/// supplies.
enum RunLiveness { live, stale, ended, unknown }

/// How often the writer beats and how many missed beats mean it is gone.
class LivenessPolicy {
  const LivenessPolicy({
    this.interval = const Duration(seconds: 10),
    this.toleranceBeats = 3,
    this.legacyRecent = const Duration(minutes: 10),
  });

  /// The writer's heartbeat period.
  final Duration interval;

  /// Missed beats before a run is stale. `interval × toleranceBeats` is
  /// the grace window.
  final int toleranceBeats;

  /// For records without heartbeats: how recent a last write still counts
  /// as alive.
  final Duration legacyRecent;

  Duration get grace => interval * toleranceBeats;
}

/// Judge [record] at [now].
///
/// Field names are configurable for records with other conventions;
/// timestamps may be epoch seconds (int or double) or ISO-8601 strings.
/// For a record with no heartbeat field, [pidAlive] is asked about the
/// record's pid if both exist; failing that, [lastWrite] (the file's
/// mtime) within [LivenessPolicy.legacyRecent] counts as live.
RunLiveness judgeLiveness(
  Map<String, dynamic> record, {
  required DateTime now,
  LivenessPolicy policy = const LivenessPolicy(),
  bool Function(int pid)? pidAlive,
  DateTime? lastWrite,
  String heartbeatKey = 'heartbeat_at',
  String endedKey = 'ended_at',
  String pidKey = 'pid',
}) {
  if (record[endedKey] != null) return RunLiveness.ended;

  final beat = parseTimestamp(record[heartbeatKey]);
  if (beat != null) {
    return now.difference(beat) <= policy.grace ? RunLiveness.live : RunLiveness.stale;
  }

  final pid = record[pidKey];
  if (pidAlive != null && pid is int) {
    return pidAlive(pid) ? RunLiveness.live : RunLiveness.stale;
  }
  if (lastWrite != null) {
    return now.difference(lastWrite) <= policy.legacyRecent
        ? RunLiveness.live
        : RunLiveness.stale;
  }
  return RunLiveness.unknown;
}

/// Epoch seconds (int or double), or an ISO-8601 string, to a UTC
/// [DateTime]; anything else is null.
DateTime? parseTimestamp(Object? value) {
  if (value is num) {
    return DateTime.fromMillisecondsSinceEpoch((value * 1000).round(), isUtc: true);
  }
  if (value is String) return DateTime.tryParse(value)?.toUtc();
  return null;
}
