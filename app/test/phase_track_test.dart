import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/engine.dart';
import 'package:mizpah_app/engine/procedure_history.dart';
import 'package:mizpah_app/engine/run_floor.dart';
import 'package:mizpah_app/models/budget.dart';
import 'package:mizpah_app/models/databook.dart';
import 'package:mizpah_app/models/document.dart';
import 'package:mizpah_app/models/procedure.dart';
import 'package:mizpah_app/models/environment.dart';
import 'package:mizpah_app/models/loop.dart';
import 'package:mizpah_app/models/storage.dart';
import 'package:mizpah_app/models/route.dart';
import 'package:mizpah_app/screens/brief/brief_screen.dart';
import 'package:mizpah_app/state/brief_manager.dart';
import 'package:mizpah_app/theme/app_theme.dart';

class _Engine implements Engine {
  _Engine(this.brief);
  Map<String, dynamic> brief;
  @override
  List<RunSession> sessions(String id) => const [];
  @override
  String? currentSession(String id) => null;
  @override
  void chooseSession(String id, String session) {}
  @override
  Future<void> archiveSession(String id, String session) async {}
  @override
  Future<void> deleteSession(String id, String session) async {}
  @override
  Future<void> discardDraft(String id) async {}
  @override
  Future<void> archiveProject(String id) async {}
  @override
  Future<void> unarchiveProject(String id) async {}
  @override
  Future<void> deleteProject(String id) async {}
  @override
  Future<void> replyToRun(String id, String text, {bool resume = false}) async {}
  @override
  Future<void> memoToWorker(String id, String task, String text) async {}
  @override
  Future<List<BriefSummary>> listBriefs() async => [
    const BriefSummary(id: 'b', title: 'B', path: '/tmp/b'),
  ];
  @override
  Future<Map<String, dynamic>> readBrief(String id) async => brief;
  @override
  Future<void> writeBrief(String id, Map<String, dynamic> b) async => brief = b;
  @override
  Future<RouteStatus> readRoute(String id) async => RouteStatus.empty;
  @override
  Future<List<RouteEvent>> readRouteLog(String id) async => const [];
  @override
  Future<void> setPriority(String id, String t, String p, String r) async {}
  @override
  Future<void> cancelTask(String id, String t, String r) async {}
  @override
  Future<void> deputySay(String text) async {}
  @override
  Future<List<DeputyTurn>> readDeputyTurns() async => const [];
  @override
  Future<Map<String, dynamic>?> readDeputyShowing() async => null;
  @override
  Future<void> resetDeputy() async {}
  @override
  Future<Map<String, dynamic>?> readDeputyActivity() async => null;
  @override
  Future<List<DeputyStep>> readDeputySteps() async => const [];
  @override
  Future<void> stopDeputy() async {}
  @override
  Future<String> authorizeDraft(String id, {String? repo}) async => id;
  @override
  Future<void> unblockTask(String id, String t) async {}
  @override
  Future<void> acceptProposal(String id, String p, {String reason = ''}) async {}
  @override
  Future<void> rejectProposal(String id, String p, {String reason = ''}) async {}
  @override
  Stream<LoopState> watchLoop(String id) => const Stream.empty();
  @override
  Future<void> setMode(String id, LoopMode mode) async {}
  @override
  Future<List<InboxDocument>> readInbox(String id) async => const [];
  @override
  Stream<void> get changes => const Stream.empty();
  @override
  Future<DataBook> readDataBook(String id) async => DataBook.empty;
  @override
  Future<BudgetLedger> readBudget(String id) async => BudgetLedger.empty;
  @override
  Future<List<InboxDocument>> readWorkOrders(String id) async => const [];
  @override
  Future<List<AttentionItem>> readAttention() async => const [];
  @override
  Future<Map<String, String>> readCrew(String id) async => const {};
  @override
  Future<List<Map<String, String>>> environments() async => const [];
  @override
  Future<void> setDefaultEnvironment(String name) async {}

  @override
  Future<List<FloorSession>> readSessions(String id) async => const [];
  @override
  Future<String> createTask(String title, {required String mission, String? repo}) async => 'b';
  @override
  Future<void> startTask(String id) async {}
  @override
  Future<List<Procedure>> readProcedures() async => const [];
  @override
  Future<StorageReport> measureStorage() async => throw UnimplementedError();
  @override
  Future<int> clearTranscripts() async => 0;
  @override
  Future<Map<String, dynamic>> readAcknowledged() async => const {};
  @override
  Future<Map<String, dynamic>> markAcknowledged({List<String> read = const [], List<String> unread = const [], List<String> dismissed = const []}) async => const {};
  @override
  Future<Map<String, dynamic>?> readAppSettings() async => null;
  @override
  Future<void> writeAppSettings(Map<String, dynamic> settings) async {}
  @override
  Future<String> storeSignatureImage(List<int> bytes, String ext) async => '';
  @override
  Future<Map<String, bool>> inspectPath(String path) async => const {};
  @override
  Future<EngineInfo> hello() async => const EngineInfo(home: '', runsRoot: '');
  @override
  Future<LibraryPolicy> readLibraryPolicy() async => const LibraryPolicy();
  @override
  Future<void> setLibraryPolicy({bool? enabled, int? grace}) async {}
  @override
  Future<void> pinProcedure(String id, bool on) async {}
  @override
  Future<void> retireProcedure(String id) async {}
  @override
  Future<void> reviveProcedure(String id) async {}
  @override
  Future<void> touchProcedure(String id) async {}
  @override
  Future<String> createProcedure(String title) async => 'p';
  @override
  Future<void> deleteProcedure(String id) async {}
  @override
  Future<bool> commitProcedures(String message) async => false;
  @override
  Future<List<ProcedureVersion>> procedureLog(String id) async => const [];
  @override
  Future<List<DiffHunkLine>> procedureDiff(String id, String sha) async => const [];
  @override
  Future<bool> restoreProcedure(String id, String sha) async => false;
  @override
  Future<void> editProcedure(String id, {String? title, String? description, List<String>? tags}) async {}
  @override
  Future<void> editStep(String id, String stepTitle, {String? rename, String? do_}) async {}
  @override
  Future<void> addStep(String id, String title, String do_, {String? after}) async {}
  @override
  Future<void> removeStep(String id, String stepTitle) async {}
  @override
  Future<void> moveStep(String id, String stepTitle, int to) async {}
  @override
  Future<void> linkStep(String id, String stepTitle, String procedure) async {}
  @override
  Future<List<Turn>> readTraceRules(String id, String s, {required int before}) async => const [];
  @override
  Future<List<EnvironmentInfo>> readEnvironmentDetails() async => const [];
  @override
  Future<List<FileNode>> listEnvironmentFiles(String name, String path) async => const [];
  @override
  Future<EnvFile> readEnvironmentFile(String name, String path) async => const EnvFile(size: 0);
  @override
  Future<void> createEnvironment(String name) async {}
  @override
  Future<void> openEnvironmentInEditor(String name, String path) async {}
  @override
  Future<void> openTaskInEditor(String id, [String path = '']) async {}
  @override
  Future<List<FileNode>> listTaskFiles(String id, String path) async => const [];
  @override
  Future<EnvFile> readTaskFile(String id, String path) async => const EnvFile(size: 0);
  @override
  Future<List<int>> readTaskFileBytes(String id, String path) async => const [];
  @override
  Future<TraceWindow> readTraceWindow(String id, String s, {int? end, int? from, int? ifLength}) async =>
      const TraceWindow(turns: [], start: 0, end: 0, fileLength: 0);
  @override
  Stream<List<Turn>> watchTranscript(String id, String s) =>
      const Stream.empty();
}

Map<String, dynamic> briefWith(List<String> statuses) => {
  'title': 'B',
  'status': 'draft',
  'mission': '',
  'version': 1,
  'budget_points': null,
  'budget_notes': '',
  'needs': [],
  'non_goals': [],
  'deliverables': [],
  'enablers': [],
  'proposals': [],
  'phases': [
    for (final (i, s) in statuses.indexed)
      {'id': 'p$i', 'title': 'Phase $i', 'status': s},
  ],
};

void main() {
  testWidgets('current phase opens centred', (t) async {
    await t.binding.setSurfaceSize(const Size(1400, 1000));
    final m = BriefManager(
      _Engine(briefWith(['closed', 'closed', 'open', 'open'])),
    );
    await m.load();
    await m.select(m.briefs.first.id);
    await t.pumpWidget(
      MaterialApp(
        theme: AppTheme.dark(),
        home: Scaffold(body: BriefScreen(manager: m)),
      ),
    );
    await t.pumpAndSettle();
    final scroll = t.widget<SingleChildScrollView>(
      find.byWidgetPredicate(
        (w) =>
            w is SingleChildScrollView && w.scrollDirection == Axis.horizontal,
      ),
    );
    final pos = scroll.controller!.position;
    // Four cells, three visible: max extent is one cell. Centred current
    // (index 2) means first shown is index 1 → offset == one cell.
    expect(pos.pixels, closeTo(pos.maxScrollExtent, 1));
    expect(pos.maxScrollExtent, greaterThan(0));
  });

  testWidgets('closing phases in-session recentres on the new current', (
    t,
  ) async {
    await t.binding.setSurfaceSize(const Size(1400, 1000));
    final m = BriefManager(
      _Engine(briefWith(['open', 'open', 'open', 'open'])),
    );
    await m.load();
    await m.select(m.briefs.first.id);
    await t.pumpWidget(
      MaterialApp(
        theme: AppTheme.dark(),
        home: Scaffold(body: BriefScreen(manager: m)),
      ),
    );
    await t.pumpAndSettle();

    Future<void> closeCurrent() async {
      await t.tap(find.text('CLOSE PHASE').first);
      await t.pumpAndSettle();
      await t.enterText(find.byType(TextField).last, 'done');
      await t.pumpAndSettle();
      await t.tap(find.widgetWithText(FilledButton, 'CLOSE PHASE'));
      await t.pumpAndSettle();
    }

    await closeCurrent(); // current → 1
    await closeCurrent(); // current → 2
    final scroll = t.widget<SingleChildScrollView>(
      find.byWidgetPredicate(
        (w) =>
            w is SingleChildScrollView && w.scrollDirection == Axis.horizontal,
      ),
    );
    final pos = scroll.controller!.position;
    expect(pos.pixels, closeTo(pos.maxScrollExtent, 1));
  });
}
