/// What the tasks folder weighs, split into what must stay (briefs, maps,
/// runs, paperwork) and what is only the workers' raw transcripts — the
/// event logs, which run to hundreds of megabytes per work order.
class StorageReport {
  const StorageReport({
    required this.total,
    required this.transcripts,
    required this.transcriptFiles,
    required this.liveTranscripts,
    required this.measuredAt,
  });
  final int total;
  final int transcripts;
  final int transcriptFiles;

  /// Transcript bytes under a loop that is still running: not clearable.
  final int liveTranscripts;
  final DateTime measuredAt;

  int get kept => total - transcripts;
  int get clearable => transcripts - liveTranscripts;

  Map<String, dynamic> toJson() => {
    'total': total,
    'transcripts': transcripts,
    'transcript_files': transcriptFiles,
    'live_transcripts': liveTranscripts,
    'measured_at': measuredAt.toUtc().toIso8601String(),
  };

  factory StorageReport.fromJson(Map<String, dynamic> j) => StorageReport(
    total: (j['total'] as num?)?.toInt() ?? 0,
    transcripts: (j['transcripts'] as num?)?.toInt() ?? 0,
    transcriptFiles: (j['transcript_files'] as num?)?.toInt() ?? 0,
    liveTranscripts: (j['live_transcripts'] as num?)?.toInt() ?? 0,
    measuredAt: DateTime.tryParse(j['measured_at'] as String? ?? '') ?? DateTime.now(),
  );
}

String humanBytes(int bytes) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  var v = bytes.toDouble();
  var i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return i == 0 ? '$bytes B' : '${v.toStringAsFixed(v >= 10 ? 0 : 1)} ${units[i]}';
}
