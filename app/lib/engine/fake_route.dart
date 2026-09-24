/// A scripted `route status` / `route log` for the mizpah project, in
/// Terra's JSON shapes so the models are exercised the way the engine
/// will exercise them. Mutable so the write verbs have something to do.
class FakeRoute {
  FakeRoute(this.tasks);

  final List<Map<String, dynamic>> tasks;
  final List<Map<String, dynamic>> log = [];

  static FakeRoute mizpah() {
    Map<String, dynamic> t(
      String id,
      String title, {
      required String status,
      required String phase,
      required String bucket,
      String priority = 'p2',
      List<String> deps = const [],
      String? unknown,
      String? enabler,
      String skill = 'terra-probe',
      String? owner,
      String? blocked,
      List<Map<String, dynamic>> evidence = const [],
    }) => {
      'id': id,
      'title': title,
      'status': status,
      'phase': phase,
      'bucket': bucket,
      'points': {'low': 3, 'medium': 8, 'high': 21}[bucket],
      'priority': priority,
      'deps': deps,
      'map_id': unknown,
      'enabler_id': enabler,
      'skill': skill,
      'role': enabler != null ? 'enabler' : 'any',
      'owner_agent': owner,
      'blocked_reason': blocked,
      'acceptance': <String>[],
      'evidence': evidence,
      'updated_at': '2026-09-18T02:40:00Z',
    };
    final r = FakeRoute([
      t(
        'mean_of_file',
        'Mean of data.txt',
        status: 'done',
        phase: 'shell',
        bucket: 'medium',
        unknown: 'data_mean',
        evidence: [
          {
            'runs': ['20260918T021000Z_mean_probe_036d04'],
            'knowns': ['data_mean'],
          },
        ],
      ),
      t(
        'count_rows',
        'Row count of data.txt',
        status: 'done',
        phase: 'shell',
        bucket: 'low',
        unknown: 'data_rows',
        evidence: [
          {
            'runs': ['20260918T022000Z_rows_probe_11aa'],
            'knowns': ['data_rows'],
          },
        ],
      ),
      t(
        'measure_latency',
        'Measure p95 request latency on the fixture',
        status: 'in_progress',
        phase: 'shell',
        bucket: 'medium',
        priority: 'p1',
        unknown: 'latency_p95',
        owner: 'worker-1',
      ),
      t(
        'size_dataset',
        'Count rows in the fixture dataset',
        status: 'blocked',
        phase: 'shell',
        bucket: 'low',
        unknown: 'dataset_rows',
        blocked: 'worker budget exhausted at 21 turns; gate: known dataset_rows below med bar (n=1)',
      ),
      t(
        'sidecar_protocol',
        'Define the engine sidecar wire protocol',
        status: 'ready',
        phase: 'shell',
        bucket: 'high',
        priority: 'p0',
        enabler: 'sidecar',
        skill: 'tooling',
      ),
      t(
        'probe_throughput',
        'Measure requests per second under load',
        status: 'ready',
        phase: 'loop',
        bucket: 'medium',
        deps: ['measure_latency'],
        unknown: 'throughput_rps',
      ),
      t(
        'close_gate',
        'Bring the gate green for the loop phase',
        status: 'ready',
        phase: 'loop',
        bucket: 'low',
        priority: 'p1',
        deps: ['probe_throughput', 'size_dataset'],
        skill: 'deliverable',
      ),
      t(
        'ship_report',
        'Write the flywheel report',
        status: 'ready',
        phase: 'loop',
        bucket: 'medium',
        priority: 'p3',
        deps: ['close_gate'],
        skill: 'deliverable',
      ),
    ]);
    r.log.addAll([
      {
        'at': '2026-09-18T02:38:10Z',
        'kind': 'blocked',
        'task': 'size_dataset',
        'title': 'Count rows in the fixture dataset',
        'reason': 'worker budget exhausted at 21 turns; gate: known dataset_rows below med bar (n=1)',
      },
      {
        'at': '2026-09-18T02:31:00Z',
        'kind': 'start',
        'task': 'measure_latency',
        'title': 'Measure p95 request latency on the fixture',
        'agent': 'worker-1',
      },
      {
        'at': '2026-09-18T02:22:40Z',
        'kind': 'complete',
        'task': 'count_rows',
        'title': 'Row count of data.txt',
        'runs': ['20260918T022000Z_rows_probe_11aa'],
        'knowns': ['data_rows'],
      },
      {
        'at': '2026-09-18T02:12:05Z',
        'kind': 'complete',
        'task': 'mean_of_file',
        'title': 'Mean of data.txt',
        'runs': ['20260918T021000Z_mean_probe_036d04'],
        'knowns': ['data_mean'],
      },
      {
        'at': '2026-09-18T01:55:00Z',
        'kind': 'priority',
        'task': 'sidecar_protocol',
        'title': 'Define the engine sidecar wire protocol',
        'reason': 'on the spine: nothing ships without it',
      },
    ]);
    return r;
  }

  /// Terra's `route status` payload, derived from the task table the way
  /// route.py derives it.
  Map<String, dynamic> status(
    List<Map<String, dynamic>> phases,
    int? budgetPoints,
  ) {
    final done = {
      for (final t in tasks)
        if (t['status'] == 'done') t['id'],
    };
    final cancelled = {
      for (final t in tasks)
        if (t['status'] == 'cancelled') t['id'],
    };
    for (final t in tasks) {
      final deps = (t['deps'] as List).cast<String>();
      t['pickable'] = t['status'] == 'ready' && deps.every(done.contains);
      t['unreachable'] = t['status'] != 'done' && deps.any(cancelled.contains);
    }
    int pts(bool Function(Map<String, dynamic>) f) =>
        tasks.where(f).fold(0, (a, t) => a + (t['points'] as int));
    final actual = pts((t) => t['status'] != 'cancelled');
    final donePts = pts((t) => t['status'] == 'done');
    final rows = [
      for (final p in phases)
        () {
          final mine = tasks.where((t) => t['phase'] == p['id']).toList();
          int n(String s) => mine.where((t) => t['status'] == s).length;
          final open = mine
              .where((t) => t['status'] != 'done' && t['status'] != 'cancelled')
              .length;
          return {
            'id': p['id'],
            'title': p['title'],
            'declared': true,
            'closed': p['status'] == 'closed',
            'closed_reason': p['closed_reason'],
            'open': open,
            'done': n('done'),
            'cancelled': n('cancelled'),
            'blocked': n('blocked'),
            'in_progress': n('in_progress'),
            'unreachable': mine.where((t) => t['unreachable'] == true).length,
            'points_open': mine
                .where(
                  (t) => t['status'] != 'done' && t['status'] != 'cancelled',
                )
                .fold(0, (a, t) => a + (t['points'] as int)),
            'total': mine.length,
            'exit_ready': mine.isNotEmpty && open == 0,
          };
        }(),
    ];
    final current = rows
        .where((r) => r['closed'] != true)
        .map((r) => r['id'])
        .firstOrNull;
    return {
      'plan_locked': false,
      'tasks': tasks,
      'phases': {
        'declared': [for (final p in phases) p['id']],
        'current': current,
        'phases': rows,
      },
      'budget': {
        'budget_points': budgetPoints,
        'points_plan': actual,
        'points_actual': actual,
        'points_done': donePts,
        'over_budget': budgetPoints != null && actual > budgetPoints,
      },
      'attention': [
        for (final t in tasks)
          if (t['status'] == 'blocked')
            {
              'kind': 'task_blocked',
              'id': t['id'],
              'severity': 'med',
              'why': 'blocked: ${t['blocked_reason']}',
              'plane': 'route',
            },
        for (final t in tasks)
          if (t['unreachable'] == true)
            {
              'kind': 'task_unreachable',
              'id': t['id'],
              'severity': 'block',
              'why': 'stranded on a cancelled dependency',
              'plane': 'route',
            },
      ],
    };
  }

  Map<String, dynamic> _task(String id) => tasks.firstWhere(
    (t) => t['id'] == id,
    orElse: () => throw ArgumentError('no task $id'),
  );

  void _event(Map<String, dynamic> e) => log.insert(0, {
    'at': '${DateTime.now().toUtc().toIso8601String().split('.').first}Z',
    ...e,
  });

  void setPriority(String id, String priority, String reason) {
    final t = _task(id);
    t['priority'] = priority;
    _event({
      'kind': 'priority',
      'task': id,
      'title': t['title'],
      'reason': reason,
    });
  }

  /// Names what the cancel strands, like `route cancel` does.
  List<String> strandedBy(String id) => [
    for (final t in tasks)
      if (t['status'] != 'done' &&
          t['status'] != 'cancelled' &&
          (t['deps'] as List).contains(id))
        t['id'] as String,
  ];

  void cancel(String id, String reason) {
    final t = _task(id);
    t['status'] = 'cancelled';
    _event({
      'kind': 'cancelled',
      'task': id,
      'title': t['title'],
      'reason': reason,
    });
  }

  void unblock(String id) {
    final t = _task(id);
    t['status'] = 'ready';
    t['blocked_reason'] = null;
    _event({'kind': 'unblocked', 'task': id, 'title': t['title']});
  }
}
