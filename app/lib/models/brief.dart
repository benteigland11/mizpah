/// Editable mirror of Terra's brief.json
/// (engine/terra/src/terra/brief.py::default_brief). Fields the GUI edits are
/// typed and mutable; everything else is preserved untouched in [rest] so a
/// round-trip through the editor never drops keys the engine cares about.
class Brief {
  Brief({
    required this.title,
    required this.mission,
    required this.budgetPoints,
    required this.budgetNotes,
    required this.environment,
    required this.needs,
    required this.nonGoals,
    required this.deliverables,
    required this.enablers,
    required this.phases,
    required this.rest,
  });

  String title;
  String mission;
  int? budgetPoints;
  String budgetNotes;

  /// The gym environment the run happens in, by name (a saved environment
  /// the engine binds into every task: toolchain, packages, env). Empty:
  /// none. Chosen on the draft, next to the crew; the brief carries it.
  String environment;
  List<Entry> needs;
  List<Entry> nonGoals;
  List<Entry> deliverables;
  List<Enabler> enablers;
  List<Phase> phases;
  final Map<String, dynamic> rest;

  static const _own = {
    'title',
    'mission',
    'budget_points',
    'budget_notes',
    'environment',
    'needs',
    'non_goals',
    'deliverables',
    'enablers',
    'phases',
  };

  factory Brief.fromJson(Map<String, dynamic> j) => Brief(
    title: j['title'] as String? ?? '',
    mission: j['mission'] as String? ?? '',
    budgetPoints: j['budget_points'] as int?,
    budgetNotes: j['budget_notes'] as String? ?? '',
    environment: j['environment'] as String? ?? '',
    needs: Entry.list(j['needs']),
    nonGoals: Entry.list(j['non_goals']),
    deliverables: Entry.list(j['deliverables']),
    enablers: [
      for (final e in (j['enablers'] as List? ?? const []))
        if (e is Map) Enabler.fromJson(e.cast<String, dynamic>()),
    ],
    phases: [
      for (final e in (j['phases'] as List? ?? const []))
        if (e is Map) Phase.fromJson(e.cast<String, dynamic>()),
    ],
    rest: {
      for (final e in j.entries)
        if (!_own.contains(e.key)) e.key: e.value,
    },
  );

  int get version => rest['version'] as int? ?? 1;

  /// draft until issued; active while the loop may route it.
  String get status => rest['status'] as String? ?? 'active';

  /// Queued changes. Read-only here: they're applied by the engine on
  /// accept, never edited in the document.
  List<Proposal> get proposals => [
    for (final p in (rest['proposals'] as List? ?? const []))
      if (p is Map) Proposal.fromJson(p.cast<String, dynamic>()),
  ];

  int get openProposals => proposals.where((p) => p.status == 'open').length;

  Map<String, dynamic> toJson() => {
    ...rest,
    'title': title,
    'mission': mission,
    'budget_points': budgetPoints,
    'budget_notes': budgetNotes,
    'environment': environment,
    'needs': [for (final e in needs) e.value],
    'non_goals': [for (final e in nonGoals) e.value],
    'deliverables': [for (final e in deliverables) e.value],
    'enablers': uniqueIds(enablers, (e) => (e as Enabler).toJson()),
    'phases': uniqueIds(phases, (p) => (p as Phase).toJson()),
  };
}

/// Terra's slug rule for ids: `^[a-z][a-z0-9_]*$`. Ids are pointers
/// (route tasks reference phases and enablers by them), so the app derives
/// them from titles rather than asking anyone to type one.
String slugOf(String title) {
  var s = title.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '_');
  s = s.replaceAll(RegExp(r'^_+|_+$'), '');
  if (s.isEmpty || !RegExp(r'^[a-z]').hasMatch(s)) s = 'item_$s';
  return s.replaceAll(RegExp(r'_+$'), '');
}

/// Something with a slug id that is frozen once saved.
abstract class Slugged {
  String get id;
  bool get frozen;
}

/// Auto-slugged ids can collide (two items titled alike); suffix the later
/// ones so Terra's uniqueness rule holds. Frozen ids never change.
List<Map<String, dynamic>> uniqueIds(
  Iterable<Slugged> items,
  Map<String, dynamic> Function(Slugged) toJson,
) {
  final seen = <String>{};
  return [
    for (final it in items)
      () {
        var id = it.id;
        if (!it.frozen) {
          var n = 2;
          while (seen.contains(id)) {
            id = '${it.id}_${n++}';
          }
        }
        seen.add(id);
        return {...toJson(it), 'id': id};
      }(),
  ];
}

/// A string in one of the brief's lists. Carries its own identity so the
/// form can keep text fields attached to the right row across add/remove.
class Entry {
  Entry(this.value);
  final Object key = Object();
  String value;

  static List<Entry> list(Object? v) => [
    for (final s in (v as List? ?? const [])) Entry(s.toString()),
  ];
}

/// Internal means of production, not a customer deliverable. Lifecycle:
/// needed → building → ready → graduated (extracted to a Cartograph
/// widget), or abandoned. `path` and `graduatesTo` are reported by the
/// engine as it builds; the app preserves them but doesn't edit them.
class Enabler implements Slugged {
  Enabler({
    required this.id,
    required String title,
    required this.status,
    this.path = '',
    this.graduatesTo = '',
    this.notes = '',
    Map<String, dynamic>? rest,
  }) : rest = rest ?? {},
       _frozen = id.isNotEmpty {
    this.title = title;
  }

  static const statuses = [
    'needed',
    'building',
    'ready',
    'graduated',
    'abandoned',
  ];

  final Object key = Object();
  @override
  String id;
  String status;
  String path;
  String graduatesTo;
  String notes;

  /// kind (default tooling) and anything else Terra adds, untouched.
  final Map<String, dynamic> rest;

  final bool _frozen;
  @override
  bool get frozen => _frozen;
  String _title = '';

  String get title => _title;
  set title(String v) {
    _title = v;
    if (!_frozen) id = slugOf(v);
  }

  static const _own = {
    'id',
    'title',
    'status',
    'path',
    'graduates_to',
    'notes',
  };

  factory Enabler.fromJson(Map<String, dynamic> j) => Enabler(
    id: j['id'] as String? ?? '',
    title: j['title'] as String? ?? '',
    status: j['status'] as String? ?? 'needed',
    path: j['path'] as String? ?? '',
    graduatesTo: j['graduates_to'] as String? ?? '',
    notes: j['notes'] as String? ?? '',
    rest: {
      for (final e in j.entries)
        if (!_own.contains(e.key)) e.key: e.value,
    },
  );

  Map<String, dynamic> toJson() => {
    'kind': 'tooling',
    ...rest,
    'id': id,
    'title': title,
    'status': status,
    'path': path,
    'graduates_to': graduatesTo.isEmpty ? null : graduatesTo,
    'notes': notes,
  };
}

/// A stage of the programme. Route tasks declare which phase they belong
/// to by [id]. The current phase is the first one not closed; closing is
/// a declared decision with a stated basis (terra brief phase-close).
class Phase implements Slugged {
  Phase({
    required this.id,
    required String title,
    required this.status,
    this.description = '',
    this.closedReason,
    this.closedAt,
    Map<String, dynamic>? rest,
  }) : rest = rest ?? {},
       _frozen = id.isNotEmpty {
    this.title = title;
  }

  final Object key = Object();
  @override
  String id;
  String status; // open | closed
  /// What the phase delivers, for the humans reading the brief.
  String description;
  String? closedReason;
  String? closedAt;
  final Map<String, dynamic> rest;

  final bool _frozen;
  @override
  bool get frozen => _frozen;
  String _title = '';

  String get title => _title;
  set title(String v) {
    _title = v;
    if (!_frozen) id = slugOf(v);
  }

  static const _own = {
    'id',
    'title',
    'status',
    'description',
    'closed_reason',
    'closed_at',
  };

  factory Phase.fromJson(Map<String, dynamic> j) => Phase(
    id: j['id'] as String? ?? '',
    title: j['title'] as String? ?? '',
    status: j['status'] as String? ?? 'open',
    description: j['description'] as String? ?? '',
    closedReason: j['closed_reason'] as String?,
    closedAt: j['closed_at'] as String?,
    rest: {
      for (final e in j.entries)
        if (!_own.contains(e.key)) e.key: e.value,
    },
  );

  Map<String, dynamic> toJson() => {
    ...rest,
    'id': id,
    'title': title,
    'description': description,
    'status': status,
    'closed_reason': closedReason,
    'closed_at': closedAt,
  };

  /// Declare the phase closed, on a stated basis.
  void close(String reason) {
    status = 'closed';
    closedReason = reason.trim();
    closedAt = DateTime.now().toUtc().toIso8601String().split('.').first;
  }

  void reopen() {
    status = 'open';
    closedReason = null;
    closedAt = null;
  }
}

/// A queued change to the brief (terra brief propose). Nothing is applied
/// until accept, which the engine performs and bumps the version.
class Proposal {
  const Proposal({
    required this.id,
    required this.summary,
    required this.status,
    required this.createdAt,
    required this.patch,
    this.decisionReason = '',
  });
  final String id;
  final String summary;
  final String status; // open | accepted | rejected
  final String createdAt;
  final Map<String, dynamic> patch;

  /// The person's reason at accept or reject (terra brief accept/reject
  /// --reason); empty when none was given.
  final String decisionReason;

  factory Proposal.fromJson(Map<String, dynamic> j) => Proposal(
    id: j['id'] as String? ?? '',
    summary: j['summary'] as String? ?? '',
    status: j['status'] as String? ?? 'open',
    createdAt: j['created_at'] as String? ?? '',
    patch: (j['patch'] as Map?)?.cast<String, dynamic>() ?? const {},
    decisionReason: j['decision_reason'] as String? ?? '',
  );

  /// One human line per patch key, in `terra brief propose` vocabulary.
  List<(String, String)> get patchLines => [
    for (final e in patch.entries)
      if (e.key != 'was_budget_points' && e.key != 'budget_before')
      switch (e.key) {
        'add_need' => ('NEED', '${e.value}'),
        'add_non_goal' => ('NON-GOAL', '${e.value}'),
        'add_deliverable' => ('DELIVERABLE', '${e.value}'),
        'add_enabler' => (
          'ENABLER',
          e.value is Map
              ? '${(e.value as Map)['id']} — ${(e.value as Map)['title']}'
              : '${e.value}',
        ),
        'mission' => ('MISSION', '${e.value}'),
        'note' => ('NOTE', '${e.value}'),
        'budget_delta' => (
          'BUDGET',
          '${(e.value as num) >= 0 ? '+' : ''}${e.value} points'
              '${patch['was_budget_points'] != null ? ' (${patch['budget_before'] ?? patch['was_budget_points']} → ${((patch['budget_before'] ?? patch['was_budget_points']) as num) + (e.value as num)})' : ''}',
        ),
        'budget_points' => (
          'BUDGET',
          'set to ${e.value} points${patch['was_budget_points'] != null ? ' (was ${patch['was_budget_points']})' : ''}',
        ),
        'was_budget_points' || 'budget_before' => ('', ''),
        _ => (e.key.toUpperCase(), '${e.value}'),
      },
  ];
}
