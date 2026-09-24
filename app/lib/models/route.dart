/// Mirrors `terra route status` (engine/terra/src/terra/route.py) and
/// `terra route log`. Read-mostly; the few writes the app makes go through
/// the engine seam as verbs.
library;

class RouteTask {
  const RouteTask({
    required this.id,
    required this.title,
    required this.status,
    required this.phase,
    required this.enablerId,
    required this.unknownId,
    required this.bucket,
    required this.points,
    required this.priority,
    required this.deps,
    required this.skill,
    required this.role,
    required this.pickable,
    required this.ownerAgent,
    required this.blockedReason,
    required this.acceptance,
    required this.evidenceRuns,
    required this.evidenceKnowns,
    required this.updatedAt,
  });

  final String id;
  final String title;

  /// ready | in_progress | blocked | done | cancelled
  final String status;
  final String phase;
  final String? enablerId;

  /// The unknown this task resolves (Terra's `map_id`).
  final String? unknownId;
  final String? bucket;
  final int? points;

  /// p0 spine · p1 this phase · p2 backlog · p3 deferred
  final String priority;
  final List<String> deps;
  final String skill;
  final String role;
  final bool pickable;
  final String? ownerAgent;
  final String? blockedReason;
  final List<String> acceptance;
  final List<String> evidenceRuns;
  final List<String> evidenceKnowns;
  final String updatedAt;

  bool get open => status != 'done' && status != 'cancelled';

  factory RouteTask.fromJson(Map<String, dynamic> j) {
    final runs = <String>[];
    final knowns = <String>[];
    for (final e in (j['evidence'] as List? ?? const [])) {
      if (e is Map) {
        runs.addAll((e['runs'] as List? ?? const []).map((x) => '$x'));
        knowns.addAll((e['knowns'] as List? ?? const []).map((x) => '$x'));
      }
    }
    return RouteTask(
      id: j['id'] as String? ?? '',
      title: j['title'] as String? ?? '',
      status: j['status'] as String? ?? 'ready',
      phase: j['phase'] as String? ?? '',
      enablerId: j['enabler_id'] as String?,
      unknownId: j['map_id'] as String?,
      bucket: j['bucket'] as String?,
      points: j['points'] as int?,
      priority: j['priority'] as String? ?? 'p2',
      deps: (j['deps'] as List? ?? const []).map((x) => '$x').toList(),
      skill: j['skill'] as String? ?? 'any',
      role: j['role'] as String? ?? 'any',
      pickable: j['pickable'] as bool? ?? false,
      ownerAgent: j['owner_agent'] as String?,
      blockedReason: j['blocked_reason'] as String?,
      acceptance: (j['acceptance'] as List? ?? const [])
          .map((x) => '$x')
          .toList(),
      evidenceRuns: runs,
      evidenceKnowns: knowns,
      updatedAt: j['updated_at'] as String? ?? '',
    );
  }
}

/// One declared phase's row from `route status`.
class PhaseRow {
  const PhaseRow({
    required this.id,
    required this.title,
    required this.closed,
    required this.open,
    required this.done,
    required this.blocked,
    required this.inProgress,
    required this.unreachable,
    required this.exitReady,
    required this.pointsOpen,
  });
  final String id;
  final String title;
  final bool closed;
  final int open;
  final int done;
  final int blocked;
  final int inProgress;
  final int unreachable;

  /// ≥1 task and every task done/cancelled.
  final bool exitReady;
  final int pointsOpen;

  int get total => open + done;

  factory PhaseRow.fromJson(Map<String, dynamic> j) => PhaseRow(
    id: j['id'] as String? ?? '',
    title: j['title'] as String? ?? '',
    closed: j['closed'] as bool? ?? false,
    open: j['open'] as int? ?? 0,
    done: j['done'] as int? ?? 0,
    blocked: j['blocked'] as int? ?? 0,
    inProgress: j['in_progress'] as int? ?? 0,
    unreachable: j['unreachable'] as int? ?? 0,
    exitReady: j['exit_ready'] as bool? ?? false,
    pointsOpen: j['points_open'] as int? ?? 0,
  );
}

/// The budget rollup from `route status`.
class RouteBudget {
  const RouteBudget({
    required this.budgetPoints,
    required this.pointsPlan,
    required this.pointsActual,
    required this.pointsDone,
    required this.overBudget,
  });
  final int? budgetPoints;
  final int pointsPlan;
  final int pointsActual;
  final int pointsDone;
  final bool overBudget;

  /// Points on open tasks: committed but not yet done.
  int get pointsReserved => pointsActual - pointsDone;

  factory RouteBudget.fromJson(Map<String, dynamic> j) => RouteBudget(
    budgetPoints: j['budget_points'] as int?,
    pointsPlan: j['points_plan'] as int? ?? 0,
    pointsActual: j['points_actual'] as int? ?? 0,
    pointsDone: j['points_done'] as int? ?? 0,
    overBudget: j['over_budget'] as bool? ?? false,
  );

  static const empty = RouteBudget(
    budgetPoints: null,
    pointsPlan: 0,
    pointsActual: 0,
    pointsDone: 0,
    overBudget: false,
  );
}

/// Something the route wants a person to look at.
class Attention {
  const Attention({
    required this.kind,
    required this.id,
    required this.severity,
    required this.why,
  });

  /// task_blocked | task_stalled | task_no_heartbeat | task_dep_cancelled | task_unreachable …
  final String kind;
  final String id;

  /// low | med | high | block
  final String severity;
  final String why;

  factory Attention.fromJson(Map<String, dynamic> j) => Attention(
    kind: j['kind'] as String? ?? '',
    id: j['id'] as String? ?? '',
    severity: j['severity'] as String? ?? 'med',
    why: j['why'] as String? ?? '',
  );
}

/// One `route log` event.
class RouteEvent {
  const RouteEvent({
    required this.at,
    required this.kind,
    required this.task,
    required this.title,
    required this.detail,
    required this.runs,
    required this.knowns,
  });
  final String at;

  /// complete | blocked | unblocked | priority | cancelled | start …
  final String kind;
  final String task;
  final String title;

  /// reason / evidence prose, when present.
  final String detail;
  final List<String> runs;
  final List<String> knowns;

  factory RouteEvent.fromJson(Map<String, dynamic> j) => RouteEvent(
    at: j['at'] as String? ?? '',
    kind: j['kind'] as String? ?? '',
    task: j['task'] as String? ?? '',
    title: j['title'] as String? ?? '',
    detail: (j['reason'] ?? j['evidence'] ?? '') as String,
    runs: (j['runs'] as List? ?? const []).map((x) => '$x').toList(),
    knowns: (j['knowns'] as List? ?? const []).map((x) => '$x').toList(),
  );
}

/// Everything `route status` returns that the app uses.
class RouteStatus {
  const RouteStatus({
    required this.tasks,
    required this.phases,
    required this.currentPhase,
    required this.budget,
    required this.attention,
    required this.planLocked,
  });
  final List<RouteTask> tasks;
  final List<PhaseRow> phases;
  final String? currentPhase;
  final RouteBudget budget;
  final List<Attention> attention;
  final bool planLocked;

  static const empty = RouteStatus(
    tasks: [],
    phases: [],
    currentPhase: null,
    budget: RouteBudget.empty,
    attention: [],
    planLocked: false,
  );

  factory RouteStatus.fromJson(Map<String, dynamic> j) {
    final ph = (j['phases'] as Map?)?.cast<String, dynamic>() ?? const {};
    return RouteStatus(
      tasks: [
        for (final t in (j['tasks'] as List? ?? const []))
          if (t is Map) RouteTask.fromJson(t.cast<String, dynamic>()),
      ],
      phases: [
        for (final p in (ph['phases'] as List? ?? const []))
          if (p is Map && (p['declared'] as bool? ?? true))
            PhaseRow.fromJson(p.cast<String, dynamic>()),
      ],
      currentPhase: ph['current'] as String?,
      budget: RouteBudget.fromJson(
        (j['budget'] as Map?)?.cast<String, dynamic>() ?? const {},
      ),
      attention: [
        for (final a in (j['attention'] as List? ?? const []))
          if (a is Map) Attention.fromJson(a.cast<String, dynamic>()),
      ],
      planLocked: j['plan_locked'] as bool? ?? false,
    );
  }

  PhaseRow? phase(String id) => phases.where((p) => p.id == id).firstOrNull;
  RouteTask? task(String id) => tasks.where((t) => t.id == id).firstOrNull;
}
