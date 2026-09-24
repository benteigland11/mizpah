import '../models/budget.dart';

/// Builds the ledger from a brief and a route, both as their JSON sits on
/// disk. Pure: the fake engine and the run reader both feed it.
BudgetLedger buildLedger(Map<String, dynamic> brief, Map<String, dynamic> route) {
  final tasks = [
    for (final t in (route['tasks'] as List? ?? const [])) (t as Map).cast<String, dynamic>(),
  ];
  final sectors = [
    for (final s in (route['sectors'] as List? ?? const [])) (s as Map).cast<String, dynamic>(),
  ];
  int? intOf(Object? v) => v is int ? v : (v is num ? v.toInt() : null);

  final lines = [
    for (final t in tasks)
      BudgetLine(
        id: t['id'] as String? ?? '',
        title: t['title'] as String? ?? '',
        status: t['status'] as String? ?? 'ready',
        bucket: t['bucket'] as String? ?? '',
        points: intOf(t['points']) ?? 0,
        planPoints: intOf(t['plan_points']),
        sector: t['sector_id'] as String?,
        phase: t['phase'] as String? ?? '',
        priority: t['priority'] as String? ?? '',
      ),
  ];
  final bySector = <String, List<BudgetLine>>{};
  final unsectored = <BudgetLine>[];
  for (final l in lines) {
    if (l.sector == null) {
      unsectored.add(l);
    } else {
      bySector.putIfAbsent(l.sector!, () => []).add(l);
    }
  }
  return BudgetLedger(
    budget: intOf(brief['budget_points']),
    notes: brief['budget_notes'] as String? ?? '',
    locked: route['plan_locked'] == true,
    sectors: [
      for (final s in sectors)
        SectorLedger(
          id: s['id'] as String? ?? '',
          title: s['title'] as String? ?? s['id'] as String? ?? '',
          reserved: intOf(s['reserved_points']),
          lines: bySector.remove(s['id']) ?? const [],
        ),
      // A task naming a sector the route no longer lists still owes points.
      for (final e in bySector.entries)
        SectorLedger(id: e.key, title: '${e.key} (undeclared)', reserved: null, lines: e.value),
    ],
    freePool: SectorLedger(id: '', title: 'Free pool', reserved: null, lines: unsectored),
  );
}
