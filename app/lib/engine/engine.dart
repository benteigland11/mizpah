import '../models/budget.dart';
import 'deputy_activity.dart';
export 'deputy_activity.dart' show DeputyStep;
import '../engine/procedure_history.dart' show DiffHunkLine, ProcedureVersion;
import '../engine/run_floor.dart' show FloorSession, TraceWindow;
import '../models/databook.dart';
import '../models/document.dart';
import '../models/procedure.dart';
import '../models/loop.dart';
import '../models/environment.dart';
import '../models/route.dart';
import '../models/storage.dart';

/// The seam between the GUI and whatever runs the loop. Screens only ever
/// talk to this. [LocalEngine] runs where the files are; [RemoteEngine] is
/// the same interface over RPC for a desk elsewhere.
abstract class Engine {
  Future<List<BriefSummary>> listBriefs();

  /// The brief's JSON object, as it sits on disk.
  Future<Map<String, dynamic>> readBrief(String id);

  /// Replaces the brief on disk.
  Future<void> writeBrief(String id, Map<String, dynamic> brief);

  /// `terra route status`: tasks, phases, budget rollup, attention.
  Future<RouteStatus> readRoute(String id);

  /// `terra route log`, newest first.
  Future<List<RouteEvent>> readRouteLog(String id);

  /// Re-rank: which work, not how much. Never moves points.
  Future<void> setPriority(
    String id,
    String taskId,
    String priority,
    String reason,
  );

  /// Assert the task should never be worked (dead premise). Terra names
  /// what it strands; the app shows that before asking.
  Future<void> cancelTask(String id, String taskId, String reason);

  Future<void> unblockTask(String id, String taskId);

  /// Apply a queued proposal's patch to the brief and bump its version
  /// (terra brief accept --reason). The brief on disk changes; a run that
  /// stopped for the decision resumes.
  Future<void> acceptProposal(String id, String proposalId, {String reason = ''});

  /// Mark a proposal rejected (terra brief reject --reason). The reason is
  /// what stops the controller proposing the same thing again; a run that
  /// stopped for the decision resumes.
  Future<void> rejectProposal(String id, String proposalId, {String reason = ''});

  /// The loop for a project, live: a new state on every change.
  Stream<LoopState> watchLoop(String id);

  /// Turn the dial. The engine applies it at its next boundary.
  Future<void> setMode(String id, LoopMode mode);

  /// A session's transcript, live. [session] is a worker's task id, or
  /// 'controller'.
  Stream<List<Turn>> watchTranscript(String id, String session);

  /// The playbook: every procedure in the shop's store, with who minted
  /// and who used it where a run on this machine says so.
  Future<List<Procedure>> readProcedures();

  /// A new, near-blank procedure to write in place. Returns its id.
  Future<String> createProcedure(String title);

  /// Remove a procedure. The playbook refuses while another procedure's
  /// step links it.
  Future<void> deleteProcedure(String id);

  /// Seal the store's pending changes as one commit (a person's save).
  Future<bool> commitProcedures(String message);

  /// A procedure's history, newest first; one version's change; put a
  /// version back (as a new commit).
  Future<List<ProcedureVersion>> procedureLog(String id);
  Future<List<DiffHunkLine>> procedureDiff(String id, String sha);
  Future<bool> restoreProcedure(String id, String sha);

  /// Edits go through the playbook CLI so its validation holds.
  Future<void> editProcedure(String id, {String? title, String? description, List<String>? tags});
  Future<void> editStep(String id, String stepTitle, {String? rename, String? do_});
  Future<void> addStep(String id, String title, String do_, {String? after});
  Future<void> removeStep(String id, String stepTitle);

  /// Move a step to a 1-based position; the others keep their order.
  Future<void> moveStep(String id, String stepTitle, int to);

  /// Link a step to another procedure ('' unlinks): walking the step
  /// means opening that procedure.
  Future<void> linkStep(String id, String stepTitle, String procedure);

  /// Use it or lose it (experimental). The policy and clock; turn decay
  /// on or off and set the grace; pin, retire or revive one procedure;
  /// note that a person opened one (counts as a search).
  Future<LibraryPolicy> readLibraryPolicy();
  Future<void> setLibraryPolicy({bool? enabled, int? grace});
  Future<void> pinProcedure(String id, bool on);
  Future<void> retireProcedure(String id);
  Future<void> reviveProcedure(String id);
  Future<void> touchProcedure(String id);

  /// Issue a draft: mark the brief active, open a session under the
  /// task's state dir, and launch the loop with the app's engine config.
  Future<void> startTask(String id);

  /// Open a new task: `mizpah init` with a title and a mission, inside a
  /// repository of the person's ([repo]) or, with none, as a gym. Returns
  /// its id; nothing runs.
  Future<String> createTask(String title, {required String mission, String? repo});

  /// The agents on a task: the controller, then every worker session,
  /// newest first.
  Future<List<FloorSession>> readSessions(String id);

  /// A slice of a worker's chat ending at byte [end] of its log (the tail
  /// when null). Page back by passing the previous slice's `start`.
  /// [ifLength]: the file length the caller last saw; when the log is
  /// still that long the answer is an empty window marked unchanged, and
  /// nothing is read or sent.
  Future<TraceWindow> readTraceWindow(String id, String session, {int? end, int? from, int? ifLength});

  /// The rule rows (handoffs, phase lines, markers) of a worker's log before
  /// byte [before]: the part of the session the trace has not loaded, so its
  /// rule menu can list the whole session. Read once per seat; the log is
  /// append-only.
  Future<List<Turn>> readTraceRules(String id, String session, {required int before});

  /// The day's paperwork for a project, in arrival order: briefings, work
  /// orders, change requests, anomalies, the notice of task stopped.
  Future<List<InboxDocument>> readInbox(String id);

  /// The map pivoted by the brief: every need and deliverable with what
  /// answers it.
  Future<DataBook> readDataBook(String id);

  /// Across every project: the documents waiting on a person — change
  /// requests for signature, anomalies, blocked or aborted work orders,
  /// runs that stopped short. Newest first.
  Future<List<AttentionItem>> readAttention();

  /// Who is on the job: `{controller: label, worker: label}`; empty when
  /// the run has not recorded it.
  Future<Map<String, String>> readCrew(String id);

  /// The saved gym environments a brief may name (`mizpah.draft
  /// environments`): each `{'name': …, 'note': …}`, the note being what the
  /// worker is told it has. A brief names one by `environment`.
  Future<List<Map<String, String>>> environments();

  /// Every saved environment with what a gym gets from it: note, env, hosts,
  /// size, Python packages, and the gyms that run in it. For the Gyms page.
  Future<List<EnvironmentInfo>> readEnvironmentDetails();

  /// One folder of an environment's file tree ([path] relative to it; '' is
  /// its top), folders first.
  Future<List<FileNode>> listEnvironmentFiles(String name, String path);

  /// A file of an environment, for a read-only preview: its text when it is
  /// text and small, else its size. Paths outside the environment are refused.
  Future<EnvFile> readEnvironmentFile(String name, String path);

  /// A new, empty environment (its folder and `base.json`).
  Future<void> createEnvironment(String name);

  /// Open an environment (and a file in it, when [path] names one) in the
  /// desk's code editor: `$MIZPAH_EDITOR`, else VS Code (`code`), else the
  /// system's opener. Environments are edited there, not in the app; a
  /// running gym has its own frozen copy, so edits reach the next run.
  Future<void> openEnvironmentInEditor(String name, String path);

  /// Open a task's folder in the desk's code editor, showing [path] in it
  /// when that names a file.
  Future<void> openTaskInEditor(String id, [String path = '']);

  /// A task's folder, read-only, for its Files tab: one folder of it ([path]
  /// inside it; '' is the top), and a file for preview. Paths that leave the
  /// folder are refused.
  Future<List<FileNode>> listTaskFiles(String id, String path);
  Future<EnvFile> readTaskFile(String id, String path);

  /// A file of a task's folder as bytes, for a viewer (PDF, SVG, image);
  /// refused past 64 MB.
  Future<List<int>> readTaskFileBytes(String id, String path);

  /// Make [name] the environment a gym gets when its brief names none
  /// (`mizpah.bases default <name>`). `environments()` marks the default
  /// with `'default': 'true'`.
  Future<void> setDefaultEnvironment(String name);

  /// Every session the project has run, newest first. The one whose
  /// paperwork the desk shows is [currentSession]: the live one when a
  /// loop runs, else the newest, unless the person chose another.
  List<RunSession> sessions(String id);
  String? currentSession(String id);
  void chooseSession(String id, String session);

  /// Put a session away: it folds to the foot of the sidebar and the
  /// controller's library stops reading it. Files untouched; reversible.
  Future<void> archiveSession(String id, String session);

  /// Remove a session's files, its registry lines and its library record.
  /// Not reversible; the caller confirms first.
  Future<void> deleteSession(String id, String session);

  /// The project as a whole. Archive puts every run of it away (refused
  /// while one is live); unarchive brings them back; delete removes its
  /// runs and, for a gym, the folder — a repository of the person's keeps
  /// everything but its `.mizpah/` tree.
  Future<void> archiveProject(String id);
  Future<void> unarchiveProject(String id);
  Future<void> deleteProject(String id);

  /// Throw a draft away. A gym goes whole (the engine's `draft discard`);
  /// a draft in a repository of the person's loses only its `.mizpah/`
  /// tree — the repository is theirs. Refused for an issued brief.
  Future<void> discardDraft(String id);

  /// A note to the run: the controller reads it first at its next briefing
  /// and that briefing is the answer. With [resume], a stopped loop is
  /// relaunched on the same session so the answer comes now.
  Future<void> replyToRun(String id, String text, {bool resume = false});

  /// A memo to a worker on [task]: delivered at its next boundary, on
  /// record beside the memos to the loop with a `to` line.
  Future<void> memoToWorker(String id, String task, String text);

  /// Every route task as a work order, picked up or not.
  Future<List<InboxDocument>> readWorkOrders(String id);

  /// The points ledger: budget, sectors and their reserves, the free
  /// pool, every task as a line item.
  Future<BudgetLedger> readBudget(String id);

  /// Fires when the set of projects or a project's state changed on disk:
  /// a new run appeared, a loop stopped, paperwork arrived.
  Stream<void> get changes;

  /// The Deputy's office. [deputySay] is one turn: the person's text in,
  /// the Deputy's reply appended to the log (slow — it thinks and runs
  /// tools). [readDeputyTurns] is the whole conversation on record;
  /// [readDeputyShowing] the paper it last pulled up, as `{'draft': slug}`.
  Future<void> deputySay(String text);

  /// What the Deputy is doing this moment while a turn runs: the engine's
  /// `pending_io` — `{kind: 'tool', command}` or `{kind: 'model'}` — or
  /// null between steps and when no turn is under way. [stopDeputy] ends
  /// the turn at its next step; the reply so far goes on record.
  Future<Map<String, dynamic>?> readDeputyActivity();

  /// Every step of the turn under way (or, between turns, the last one),
  /// off the session journal: each model call, each tool call with its
  /// command and outcome, a compaction — with how long each took.
  Future<List<DeputyStep>> readDeputySteps();
  Future<void> stopDeputy();
  Future<List<DeputyTurn>> readDeputyTurns();
  Future<Map<String, dynamic>?> readDeputyShowing();
  Future<void> resetDeputy();

  /// The signature on a draft: the engine makes a project of it where it
  /// is (idempotent), or moves it into [repo] when the person names one.
  /// Returns the project's id, which changes only on a move; [startTask]
  /// then issues it.
  Future<String> authorizeDraft(String id, {String? repo});

  /// The desk's own files, which a browser cannot reach: what the tasks
  /// folder weighs and the transcripts that can go; what has been read or
  /// dismissed on Home; the settings; a file to serve; whether a path is
  /// a folder (and a git repository) on the machine that runs the loops.
  Future<StorageReport> measureStorage();
  Future<int> clearTranscripts();
  Future<Map<String, dynamic>> readAcknowledged();

  /// Change the marks by delta — keys read, keys made unread, keys
  /// dismissed — applied to the file where it lives and the whole record
  /// returned. Never a replacement: two desks (this one and a browser)
  /// each wrote their own copy of the marks before, last write winning,
  /// and a stale copy wiped what the other had read.
  Future<Map<String, dynamic>> markAcknowledged({
    List<String> read = const [],
    List<String> unread = const [],
    List<String> dismissed = const [],
  });
  Future<Map<String, dynamic>?> readAppSettings();
  Future<void> writeAppSettings(Map<String, dynamic> settings);
  Future<String> storeSignatureImage(List<int> bytes, String ext);
  Future<Map<String, bool>> inspectPath(String path);

  /// Where this engine runs: the home directory (paths are shown relative
  /// to it) and the default tasks root.
  Future<EngineInfo> hello();
}

class EngineInfo {
  const EngineInfo({required this.home, required this.runsRoot, this.version = ''});
  final String home;
  final String runsRoot;
  final String version;
  Map<String, dynamic> toJson() => {'home': home, 'runs_root': runsRoot, 'version': version};
  factory EngineInfo.fromJson(Map<String, dynamic> j) =>
      EngineInfo(home: j['home'] as String? ?? '', runsRoot: j['runs_root'] as String? ?? '', version: j['version'] as String? ?? '');
}

class BriefSummary {
  const BriefSummary({
    required this.id,
    required this.title,
    required this.path,
    this.state = 'idle',
    this.attention = 0,
    this.endedAt,
  });

  /// When the newest run stopped (its `loop.json` last written, which the
  /// loop does as it ends); null while live or never run. Completed tasks
  /// list by this, most recently finished first.
  final DateTime? endedAt;
  final String id;
  final String title;

  /// Project root on disk; the brief lives at `<path>/.terra/brief.json`.
  final String path;

  /// live — the loop is running now · completed — it stopped with nothing
  /// owed · stopped — it ended any other way (stalled, interrupted,
  /// killed, out of cycles) · idle — no run yet · archived — put away
  /// (`archived: true` in run.json); walkable, off Home.
  final String state;

  /// Change requests awaiting signature.
  final int attention;

  bool get running => state == 'live';

}

/// One line of the conversation with the Deputy, as the engine logged it:
/// what the person said, what the Deputy answered, or a note from the
/// engine (a reset, a model that did not answer).
class DeputyTurn {
  const DeputyTurn({
    required this.at,
    required this.role,
    required this.text,
    this.showing,
    this.error = false,
    this.seconds,
    this.toolCalls,
  });

  final DateTime at;

  /// `user`, `deputy` or `system`.
  final String role;
  final String text;

  /// The paper on the desk after this turn (`{'draft': slug}`).
  final Map<String, dynamic>? showing;
  final bool error;
  final double? seconds;
  final int? toolCalls;

  factory DeputyTurn.fromJson(Map<String, dynamic> j) => DeputyTurn(
    at: DateTime.fromMillisecondsSinceEpoch((((j['at'] as num?) ?? 0) * 1000).round(), isUtc: true),
    role: j['role'] as String? ?? 'system',
    text: j['text'] as String? ?? '',
    showing: (j['showing'] as Map?)?.cast<String, dynamic>(),
    error: j['error'] == true,
    seconds: (j['seconds'] as num?)?.toDouble(),
    toolCalls: j['tool_calls'] as int?,
  );
}

/// A document that wants a person, with the project it belongs to.
class AttentionItem {
  const AttentionItem({required this.project, required this.document});
  final BriefSummary project;
  final InboxDocument document;
}

/// One loop run of a project: its session root on disk and how it ended.
class RunSession {
  const RunSession({
    required this.path,
    required this.startedAt,
    required this.running,
    required this.archived,
    this.stop,
  });
  final String path;
  final DateTime? startedAt;
  final bool running;
  final bool archived;

  /// Why the loop stopped, in the engine's words; null while it runs.
  final String? stop;

  /// The folder's own name: the stamp under `.mizpah/sessions/`, or the
  /// older `<task>-sess` convention.
  String get name => path.split('/').where((s) => s.isNotEmpty).last;
}
