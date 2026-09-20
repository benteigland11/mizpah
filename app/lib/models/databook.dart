/// The map pivoted by the brief: each need and deliverable with the
/// known that answers it, the unknown still open on it, or nothing yet.
/// Below each answer, the test report — probes, runs, agreement.
library;

class DataBookEntry {
  const DataBookEntry({
    required this.kind,
    required this.index,
    required this.text,
    required this.answers,
    required this.open,
  });

  /// need | deliverable
  final String kind;

  /// 1-based, as the brief numbers it.
  final int index;
  final String text;

  /// Knowns citing this entry.
  final List<KnownReport> answers;

  /// Unknowns citing this entry that have not graduated.
  final List<OpenUnknown> open;

  /// A boolean that read false answers the question and fails the need:
  /// the artifact is there and wrong, which is the case a person acts on.
  bool get failed => answers.any((k) => k.type == 'boolean' && k.value == 'false');

  String get state => failed
      ? 'false'
      : answers.isNotEmpty
      ? 'answered'
      : open.isNotEmpty
      ? 'open'
      : 'uncovered';
}

class KnownReport {
  const KnownReport({
    required this.id,
    required this.claim,
    required this.value,
    required this.unit,
    required this.type,
    required this.confidence,
    required this.n,
    required this.agreement,
    required this.methods,
    required this.probes,
    required this.runs,
    required this.adoptedFrom,
    required this.at,
  });
  final String id;
  final String claim;
  final String value;
  final String unit;

  /// number | boolean | label | formula
  final String type;
  final String confidence;
  final int n;
  final double? agreement;
  final int methods;
  final List<String> probes;
  final List<String> runs;

  /// The task map it was adopted from (`t_<task>`), if any.
  final String? adoptedFrom;
  final DateTime? at;
}

class OpenUnknown {
  const OpenUnknown({
    required this.id,
    required this.claim,
    required this.evidenceNeeded,
    required this.status,
  });
  final String id;
  final String claim;
  final String evidenceNeeded;
  final String status;
}

/// One map in the tree: global, or a work order's own (`t_<task>`).
class MapNode {
  const MapNode({
    required this.id,
    required this.kind,
    required this.parent,
    required this.purpose,
    required this.knowns,
    required this.runs,
    required this.openUnknowns,
  });
  final String id;

  /// global | session
  final String kind;
  final String? parent;
  final String purpose;
  final List<MapKnown> knowns;
  final int runs;
  final int openUnknowns;

  int get adopted => knowns.where((k) => k.adopted).length;
}

/// A known as it sits in one map, and whether the global map holds it.
class MapKnown {
  const MapKnown({required this.report, required this.adopted, this.from});
  final KnownReport report;

  /// In a task map: it was promoted to global. In global: always true.
  final bool adopted;

  /// In global: the task map it came up from, if adopted rather than born.
  final String? from;
}

class DataBook {
  const DataBook({
    required this.entries,
    required this.orphans,
    this.maps = const [],
  });
  final List<DataBookEntry> entries;

  /// The map tree, global first, then task maps in id order.
  final List<MapNode> maps;

  /// Knowns citing nothing in the brief: worth seeing, not worth a row.
  final List<KnownReport> orphans;

  static const empty = DataBook(entries: [], orphans: []);

  int get answered => entries.where((e) => e.state == 'answered').length;
  int get failed => entries.where((e) => e.state == 'false').length;
  int get open => entries.where((e) => e.state == 'open').length;
  int get uncovered => entries.where((e) => e.state == 'uncovered').length;

  /// The header line: what is met, what read false, what is still owed.
  String get verdict {
    final parts = ['$answered OF ${entries.length} MET'];
    if (failed > 0) parts.add('$failed FALSE');
    if (open > 0) parts.add('$open OPEN');
    if (uncovered > 0) parts.add('$uncovered UNCOVERED');
    return parts.join(' · ');
  }
}
