import '../models/budget.dart';
import '../models/databook.dart';
import '../models/document.dart';
import '../models/loop.dart';
import '../models/route.dart';

/// The seam between the GUI and whatever runs the loop. Screens only ever
/// talk to this. Today the only implementation is [FakeEngine]; the real
/// sidecar client will be a second implementation of the same interface.
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

  /// The day's paperwork for a project, in arrival order: briefings, work
  /// orders, change requests, anomalies, the closing report.
  Future<List<InboxDocument>> readInbox(String id);

  /// The map pivoted by the brief: every need and deliverable with what
  /// answers it.
  Future<DataBook> readDataBook(String id);

  /// Across every project: the documents waiting on a person — change
  /// requests for signature, anomalies, blocked or aborted work orders,
  /// runs that stopped short. Newest first.
  Future<List<AttentionItem>> readAttention();

  /// Every route task as a work order, picked up or not.
  Future<List<InboxDocument>> readWorkOrders(String id);

  /// The points ledger: budget, sectors and their reserves, the free
  /// pool, every task as a line item.
  Future<BudgetLedger> readBudget(String id);

  /// Fires when the set of projects or a project's state changed on disk:
  /// a new run appeared, a loop stopped, paperwork arrived.
  Stream<void> get changes;
}

class BriefSummary {
  const BriefSummary({
    required this.id,
    required this.title,
    required this.path,
    this.state = 'idle',
    this.attention = 0,
  });
  final String id;
  final String title;

  /// Project root on disk; the brief lives at `<path>/.terra/brief.json`.
  final String path;

  /// live — the loop is running now · completed — it stopped with nothing
  /// owed · stopped — it ended any other way (stalled, interrupted,
  /// killed, out of cycles) · idle — no run yet.
  final String state;

  /// Documents waiting on a person (open change requests, a stall).
  final int attention;

  bool get running => state == 'live';
}

/// A document that wants a person, with the project it belongs to.
class AttentionItem {
  const AttentionItem({required this.project, required this.document});
  final BriefSummary project;
  final InboxDocument document;
}
