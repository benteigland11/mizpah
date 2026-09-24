/// The points ledger, as Terra keeps it (engine/terra/src/terra/route.py):
/// a budget on the brief; sectors that reserve a slice of it; a free pool
/// for unsectored work; a baseline (`plan_points`) the working points may
/// drift from; and every task as a line item drawing on one of those.
library;

class BudgetLine {
  const BudgetLine({
    required this.id,
    required this.title,
    required this.status,
    required this.bucket,
    required this.points,
    required this.planPoints,
    required this.sector,
    required this.phase,
    required this.priority,
  });
  final String id;
  final String title;

  /// ready | in_progress | blocked | done | cancelled
  final String status;
  final String bucket;

  /// Working points — what the task costs now.
  final int points;

  /// Baseline points — what it was planned at. Null before a baseline.
  final int? planPoints;
  final String? sector;
  final String phase;
  final String priority;

  bool get open => status != 'done' && status != 'cancelled';
  bool get drifted => planPoints != null && planPoints != points;
}

/// A sector: a named reserve of the budget. Its cap is `reserved`; what
/// its tasks plan against that is `planned`.
class SectorLedger {
  const SectorLedger({
    required this.id,
    required this.title,
    required this.reserved,
    required this.lines,
  });
  final String id;
  final String title;

  /// Null for the free pool.
  final int? reserved;
  final List<BudgetLine> lines;

  int get planned => lines
      .where((l) => l.status != 'cancelled')
      .fold(0, (a, l) => a + (l.planPoints ?? l.points));
  int get spent => lines.where((l) => l.status == 'done').fold(0, (a, l) => a + l.points);
  int get committed => lines.where((l) => l.open).fold(0, (a, l) => a + l.points);
  int get cancelled =>
      lines.where((l) => l.status == 'cancelled').fold(0, (a, l) => a + l.points);

  /// Reserve not yet planned against; null for the free pool.
  int? get headroom => reserved == null ? null : reserved! - planned;
}

class BudgetLedger {
  const BudgetLedger({
    required this.budget,
    required this.notes,
    required this.locked,
    required this.sectors,
    required this.freePool,
  });

  /// Null when the brief sets none.
  final int? budget;
  final String notes;
  final bool locked;
  final List<SectorLedger> sectors;

  /// Unsectored work.
  final SectorLedger freePool;

  static const empty = BudgetLedger(
    budget: null,
    notes: '',
    locked: false,
    sectors: [],
    freePool: SectorLedger(id: '', title: '', reserved: null, lines: []),
  );

  List<BudgetLine> get lines => [
    for (final s in sectors) ...s.lines,
    ...freePool.lines,
  ];

  /// Σ sector reserves.
  int get reserved => sectors.fold(0, (a, s) => a + (s.reserved ?? 0));

  /// Budget − reserves: what unsectored work may draw on.
  int? get free => budget == null ? null : budget! - reserved;
  int get spent => lines.where((l) => l.status == 'done').fold(0, (a, l) => a + l.points);
  int get committed => lines.where((l) => l.open).fold(0, (a, l) => a + l.points);
  int get cancelled =>
      lines.where((l) => l.status == 'cancelled').fold(0, (a, l) => a + l.points);
  int get planned => lines
      .where((l) => l.status != 'cancelled')
      .fold(0, (a, l) => a + (l.planPoints ?? l.points));

  /// Reserve nobody has planned against yet, across sectors.
  int get idleReserve => sectors.fold(0, (a, s) => a + (s.headroom ?? 0).clamp(0, 1 << 30));

  /// Budget − spent − committed: what is left to route.
  int? get available => budget == null ? null : budget! - spent - committed;
  bool get over => budget != null && spent + committed > budget!;

  /// Lines whose working points moved off the baseline.
  List<BudgetLine> get drift => lines.where((l) => l.drifted).toList();

  bool get isEmpty => budget == null && lines.isEmpty;
}
