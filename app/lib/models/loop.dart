/// The loop as the GUI sees it: mode, cycle, the controller's recent
/// activity and one session per route task. Mirrors what the engine keeps
/// in loop.json, controller.jsonl and each task's session root.
library;

/// How much rope the loop gets. The user turns this; the engine obeys.
enum LoopMode {
  /// Nothing minted, nothing run.
  hold,

  /// Controller reads brief against map and queues proposals; workers idle.
  propose,

  /// Controller mints and workers run; proposals still wait for a person.
  run;

  static LoopMode parse(String? s) =>
      LoopMode.values.firstWhere((m) => m.name == s, orElse: () => hold);

  String get blurb => switch (this) {
    hold => 'Engine idle. Nothing minted, nothing run.',
    propose => 'Controller queues proposals; workers wait.',
    run => 'Controller mints, workers run. Proposals still wait for you.',
  };
}

class LoopState {
  const LoopState({
    required this.mode,
    required this.running,
    required this.cycle,
    required this.tasksRun,
    required this.maxTasks,
    required this.maxCycles,
    required this.stopReason,
    required this.controller,
    required this.workers,
  });

  final LoopMode mode;
  final bool running;
  final int cycle;
  final int tasksRun;
  final int maxTasks;
  final int maxCycles;

  /// Why the driver stopped, or null while it runs / before it starts.
  final String? stopReason;
  final ControllerState controller;
  final List<WorkerSession> workers;

  static const idle = LoopState(
    mode: LoopMode.hold,
    running: false,
    cycle: 0,
    tasksRun: 0,
    maxTasks: 0,
    maxCycles: 0,
    stopReason: null,
    controller: ControllerState.idle,
    workers: [],
  );
}

/// One controller action, as journaled. The controller is one call today
/// and will be more; a step is a list of these.
class ControllerAction {
  const ControllerAction({
    required this.kind,
    required this.at,
    required this.summary,
    this.refused = const [],
  });

  /// route | eval | checkin | proposal
  final String kind;
  final DateTime at;
  final String summary;

  /// Guard refusals, verbatim.
  final List<String> refused;
}

class ControllerState {
  const ControllerState({
    required this.model,
    required this.endpoint,
    required this.recent,
    required this.next,
    required this.heldGuidance,
  });
  final String model;
  final String endpoint;

  /// Newest first.
  final List<ControllerAction> recent;

  /// What triggers its next step.
  final String next;

  /// Guidance currently held against a worker, or null.
  final String? heldGuidance;

  static const idle = ControllerState(
    model: '',
    endpoint: '',
    recent: [],
    next: '',
    heldGuidance: null,
  );
}

/// A worker's session on one route task.
class WorkerSession {
  const WorkerSession({
    required this.taskId,
    required this.taskTitle,
    required this.unknownId,
    required this.bucket,
    required this.status,
    required this.turns,
    required this.budgetTurns,
    required this.handoffs,
    required this.gateOk,
    required this.gateProblems,
    required this.note,
    this.lastCheckinTurn,
    this.lastCheckinVerdict,
    this.blockedReason,
  });

  final String taskId;
  final String taskTitle;
  final String unknownId;
  final String bucket; // low | medium | high
  final String status; // in_progress | blocked | done
  final int turns;
  final int budgetTurns;
  final int handoffs;
  final bool gateOk;
  final List<String> gateProblems;

  /// One line on where the worker is (probe validated, runs taken, …).
  final String note;
  final int? lastCheckinTurn;

  /// held | corrected
  final String? lastCheckinVerdict;
  final String? blockedReason;
}

/// One entry in a session's transcript. Workers: a tool call and its
/// result. Controller: a step and what it emitted. Check-ins and handoffs
/// are entries too, so they read inline where they happened.
class Turn {
  const Turn({
    required this.n,
    required this.kind,
    required this.at,
    required this.title,
    this.body = '',
    this.ok = true,
    this.before,
    this.after,
    this.tone = '',
    this.anchor = 'center',
    this.lang,
    this.resultLang,
  });

  /// The language of a tool call's own text (its command, or the file it
  /// writes), when the reader knows it; null to guess from the text.
  final String? lang;

  /// The language of what the call returned (a read file's), when known.
  final String? resultLang;

  /// A rule's colour by name (`system`, `pass`, `hold`, `crimson`, `rose`…);
  /// empty for the kind's own colour.
  final String tone;

  /// Where a rule's label sits on its line: `left`, `center` or `right`.
  final String anchor;

  /// For an edit: the text replaced and its replacement, so the trace can
  /// draw the change as a diff rather than as two blobs.
  final String? before;
  final String? after;

  /// Turn number within the session.
  final int n;

  /// tool | checkin | handoff | step | note | thought
  final String kind;
  final DateTime at;

  /// One line: `bash  terra probe run latency --to …`, or the check-in verdict.
  final String title;

  /// Collapsed detail: command output, the emitted JSON, the handoff text.
  final String body;

  /// False when the tool call was rejected or the step was refused.
  final bool ok;

  /// What this step is, across re-reads: the tail is re-read on every
  /// refresh and every read makes new objects, so anything the screen
  /// keeps about a step (opened, say) is keyed by this, never by identity.
  String get key => '$n/$kind/${at.microsecondsSinceEpoch}/$title';
}
