import 'dart:async';
import 'dart:convert';

import '../models/budget.dart';
import '../models/databook.dart';
import '../models/document.dart';
import '../models/environment.dart';
import '../models/loop.dart';
import '../models/procedure.dart';
import '../models/provider.dart';
import '../models/route.dart';
import '../models/storage.dart';
import 'engine.dart';
import 'procedure_history.dart';
import 'provider_client.dart';
import 'rpc.dart';
import 'run_floor.dart';
import 'wire.dart' as w;

/// The [Engine] as seen from a browser or another machine: every call is
/// one RPC to the desk that has the files, decoded with the wire codec.
/// The three things the seam keeps synchronously (sessions per task) are
/// mirrored here from the last read, since a remote cannot block.
class RemoteEngine implements Engine {
  RemoteEngine(this.rpc) {
    rpc.connect();
    _changes = rpc.changes.listen((_) => _onChange());
  }
  final RpcClient rpc;
  late final StreamSubscription<void> _changes;
  final _changesOut = StreamController<void>.broadcast();

  /// Sessions per task, refreshed on every change so the synchronous
  /// getters stay honest.
  final _sessions = <String, List<RunSession>>{};
  final _current = <String, String?>{};

  /// Refresh every task's session mirror first, then tell the managers:
  /// what they read synchronously after this is as fresh as the change.
  Future<void> _onChange() async {
    await Future.wait([for (final id in _sessions.keys.toList()) refreshSessions(id).catchError((_) {})]);
    _changesOut.add(null);
  }

  @override
  Stream<void> get changes => _changesOut.stream;

  Future<T> _call<T>(String m, Map<String, Object?> a, T Function(Object? r) decode) async => decode(await rpc.call(m, a));
  Future<void> _do(String m, [Map<String, Object?> a = const {}]) => rpc.call(m, a);
  List<w.Json> _list(Object? r) => [for (final e in (r as List? ?? const [])) (e as Map).cast<String, dynamic>()];

  @override
  Future<List<BriefSummary>> listBriefs() => _call('listBriefs', const {}, (r) => [for (final j in _list(r)) w.briefSummaryOf(j)]);
  @override
  Future<Map<String, dynamic>> readBrief(String id) => _call('readBrief', {'id': id}, (r) => (r as Map).cast<String, dynamic>());
  @override
  Future<void> writeBrief(String id, Map<String, dynamic> brief) => _do('writeBrief', {'id': id, 'brief': brief});
  @override
  Future<RouteStatus> readRoute(String id) => _call('readRoute', {'id': id}, (r) => w.routeStatusOf((r as Map).cast()));
  @override
  Future<List<RouteEvent>> readRouteLog(String id) => _call('readRouteLog', {'id': id}, (r) => [for (final j in _list(r)) w.routeEventOf(j)]);
  @override
  Future<void> setPriority(String id, String taskId, String priority, String reason) =>
      _do('setPriority', {'id': id, 'task': taskId, 'priority': priority, 'reason': reason});
  @override
  Future<void> cancelTask(String id, String taskId, String reason) => _do('cancelTask', {'id': id, 'task': taskId, 'reason': reason});
  @override
  Future<void> unblockTask(String id, String taskId) => _do('unblockTask', {'id': id, 'task': taskId});
  @override
  Future<void> acceptProposal(String id, String proposalId, {String reason = ''}) =>
      _do('acceptProposal', {'id': id, 'proposal': proposalId, 'reason': reason});
  @override
  Future<void> rejectProposal(String id, String proposalId, {String reason = ''}) =>
      _do('rejectProposal', {'id': id, 'proposal': proposalId, 'reason': reason});
  @override
  Stream<LoopState> watchLoop(String id) => rpc.subscribe('watchLoop', {'id': id}).map((v) => w.loopStateOf((v as Map).cast()));
  @override
  Future<void> setMode(String id, LoopMode mode) => _do('setMode', {'id': id, 'mode': mode.name});
  @override
  Stream<List<Turn>> watchTranscript(String id, String session) =>
      rpc.subscribe('watchTranscript', {'id': id, 'session': session}).map((v) => [for (final j in _list(v)) w.turnOf(j)]);

  @override
  Future<List<Procedure>> readProcedures() => _call('readProcedures', const {}, (r) => [for (final j in _list(r)) w.procedureOf(j)]);
  @override
  Future<String> createProcedure(String title) => _call('createProcedure', {'title': title}, (r) => r as String);
  @override
  Future<void> deleteProcedure(String id) => _do('deleteProcedure', {'id': id});
  @override
  Future<bool> commitProcedures(String message) => _call('commitProcedures', {'message': message}, (r) => r == true);
  @override
  Future<List<ProcedureVersion>> procedureLog(String id) =>
      _call('procedureLog', {'id': id}, (r) => [for (final j in _list(r)) w.procedureVersionOf(j)]);
  @override
  Future<List<DiffHunkLine>> procedureDiff(String id, String sha) =>
      _call('procedureDiff', {'id': id, 'sha': sha}, (r) => [for (final j in _list(r)) w.diffLineOf(j)]);
  @override
  Future<bool> restoreProcedure(String id, String sha) => _call('restoreProcedure', {'id': id, 'sha': sha}, (r) => r == true);
  @override
  Future<void> editProcedure(String id, {String? title, String? description, List<String>? tags}) =>
      _do('editProcedure', {'id': id, 'title': title, 'description': description, 'tags': tags});
  @override
  Future<void> editStep(String id, String stepTitle, {String? rename, String? do_}) =>
      _do('editStep', {'id': id, 'step': stepTitle, 'rename': rename, 'do': do_});
  @override
  Future<void> addStep(String id, String title, String do_, {String? after}) =>
      _do('addStep', {'id': id, 'title': title, 'do': do_, 'after': after});
  @override
  Future<void> removeStep(String id, String stepTitle) => _do('removeStep', {'id': id, 'step': stepTitle});
  @override
  Future<void> moveStep(String id, String stepTitle, int to) => _do('moveStep', {'id': id, 'step': stepTitle, 'to': to});
  @override
  Future<void> linkStep(String id, String stepTitle, String procedure) =>
      _do('linkStep', {'id': id, 'step': stepTitle, 'procedure': procedure});
  @override
  Future<LibraryPolicy> readLibraryPolicy() => _call('readLibraryPolicy', const {}, (r) => w.libraryPolicyOf((r as Map).cast()));
  @override
  Future<void> setLibraryPolicy({bool? enabled, int? grace}) => _do('setLibraryPolicy', {'enabled': enabled, 'grace': grace});
  @override
  Future<void> pinProcedure(String id, bool on) => _do('pinProcedure', {'id': id, 'on': on});
  @override
  Future<void> retireProcedure(String id) => _do('retireProcedure', {'id': id});
  @override
  Future<void> reviveProcedure(String id) => _do('reviveProcedure', {'id': id});
  @override
  Future<void> touchProcedure(String id) => _do('touchProcedure', {'id': id});

  @override
  Future<void> startTask(String id) => _do('startTask', {'id': id});
  @override
  Future<String> createTask(String title, {required String mission, String? repo}) =>
      _call('createTask', {'title': title, 'mission': mission, 'repo': repo}, (r) => r as String);
  @override
  Future<String> authorizeDraft(String id, {String? repo}) => _call('authorizeDraft', {'id': id, 'repo': repo}, (r) => r as String);

  @override
  Future<List<FloorSession>> readSessions(String id) =>
      _call('readSessions', {'id': id}, (r) => [for (final j in _list(r)) w.floorSessionOf(j)]);
  @override
  Future<List<Turn>> readTraceRules(String id, String session, {required int before}) => _call(
      'readTraceRules', {'id': id, 'session': session, 'before': before},
      (r) => [for (final t in (r as List)) w.turnOf((t as Map).cast())]);

  @override
  Future<TraceWindow> readTraceWindow(String id, String session, {int? end, int? from, int? ifLength}) => _call(
      'readTraceWindow', {'id': id, 'session': session, 'end': end, 'from': from, 'if_length': ifLength}, (r) => w.traceWindowOf((r as Map).cast()));
  @override
  Future<List<InboxDocument>> readInbox(String id) =>
      _call('readInbox', {'id': id}, (r) => [for (final j in _list(r)) InboxDocument.fromJson(j)]);
  @override
  Future<List<InboxDocument>> readWorkOrders(String id) =>
      _call('readWorkOrders', {'id': id}, (r) => [for (final j in _list(r)) InboxDocument.fromJson(j)]);
  @override
  Future<DataBook> readDataBook(String id) => _call('readDataBook', {'id': id}, (r) => w.dataBookOf((r as Map).cast()));
  @override
  Future<BudgetLedger> readBudget(String id) => _call('readBudget', {'id': id}, (r) => w.budgetLedgerOf((r as Map).cast()));
  @override
  Future<List<AttentionItem>> readAttention() => _call('readAttention', const {}, (r) => [for (final j in _list(r)) w.attentionItemOf(j)]);
  @override
  Future<Map<String, String>> readCrew(String id) =>
      _call('readCrew', {'id': id}, (r) => (r as Map).map((k, v) => MapEntry('$k', '$v')));

  @override
  Future<void> setDefaultEnvironment(String name) => _do('setDefaultEnvironment', {'name': name});
  @override
  Future<List<EnvironmentInfo>> readEnvironmentDetails() => _call('readEnvironmentDetails', const {},
      (r) => [for (final e in (r as List)) EnvironmentInfo.fromJson((e as Map).cast())]);

  @override
  Future<List<FileNode>> listEnvironmentFiles(String name, String path) => _call('listEnvironmentFiles', {'name': name, 'path': path},
      (r) => [for (final e in (r as List)) FileNode.fromJson((e as Map).cast())]);

  @override
  Future<EnvFile> readEnvironmentFile(String name, String path) =>
      _call('readEnvironmentFile', {'name': name, 'path': path}, (r) => EnvFile.fromJson((r as Map).cast()));
  @override
  Future<void> createEnvironment(String name) => _call('createEnvironment', {'name': name}, (_) {});
  @override
  Future<void> openEnvironmentInEditor(String name, String path) =>
      throw UnsupportedError('the editor opens at the desk');
  @override
  Future<void> openTaskInEditor(String id, [String path = '']) => throw UnsupportedError('the editor opens at the desk');
  @override
  Future<List<FileNode>> listTaskFiles(String id, String path) => _call('listTaskFiles', {'id': id, 'path': path},
      (r) => [for (final e in (r as List)) FileNode.fromJson((e as Map).cast())]);
  @override
  Future<List<int>> readTaskFileBytes(String id, String path) =>
      _call('readTaskFileBytes', {'id': id, 'path': path}, (r) => base64Decode(r as String));
  @override
  Future<EnvFile> readTaskFile(String id, String path) =>
      _call('readTaskFile', {'id': id, 'path': path}, (r) => EnvFile.fromJson((r as Map).cast()));

  @override
  Future<List<Map<String, String>>> environments() => _call('environments', const {}, (r) => [
        for (final e in (r as List))
          if (e is Map) e.map((k, v) => MapEntry('$k', '$v')),
      ]);

  /// The seam's synchronous session calls: answered from the mirror, which
  /// [refreshSessions] fills. Callers that need it fresh await that first.
  @override
  List<RunSession> sessions(String id) {
    final have = _sessions[id];
    if (have == null) {
      // First ask: fetch, then let listeners re-read.
      _sessions[id] = const [];
      refreshSessions(id).then((_) => _changesOut.add(null)).catchError((_) {});
    }
    return have ?? const [];
  }
  @override
  String? currentSession(String id) => _current[id];
  @override
  void chooseSession(String id, String session) {
    _current[id] = session;
    _do('chooseSession', {'id': id, 'session': session}).then((_) => _changesOut.add(null));
  }

  @override
  Future<void> archiveSession(String id, String session) async {
    await _do('archiveSession', {'id': id, 'session': session});
    await refreshSessions(id);
    _changesOut.add(null);
  }

  @override
  Future<void> archiveProject(String id) => _do('archiveProject', {'id': id});
  @override
  Future<void> unarchiveProject(String id) => _do('unarchiveProject', {'id': id});
  @override
  Future<void> deleteProject(String id) => _do('deleteProject', {'id': id});
  @override
  Future<void> discardDraft(String id) => _do('discardDraft', {'id': id});
  @override
  Future<void> deleteSession(String id, String session) async {
    await _do('deleteSession', {'id': id, 'session': session});
    await refreshSessions(id);
    _changesOut.add(null);
  }

  @override
  Future<void> replyToRun(String id, String text, {bool resume = false}) =>
      _do('replyToRun', {'id': id, 'text': text, 'resume': resume});
  @override
  Future<void> memoToWorker(String id, String task, String text) =>
      _do('memoToWorker', {'id': id, 'task': task, 'text': text});

  Future<void> refreshSessions(String id) async {
    final r = (await rpc.call('sessions', {'id': id}) as Map).cast<String, dynamic>();
    _sessions[id] = [for (final j in _list(r['sessions'])) w.runSessionOf(j)];
    _current[id] = r['current'] as String?;
  }

  @override
  Future<void> deputySay(String text) => _do('deputySay', {'text': text});
  @override
  Future<Map<String, dynamic>?> readDeputyActivity() =>
      _call('readDeputyActivity', const {}, (r) => (r as Map?)?.cast<String, dynamic>());
  @override
  Future<List<DeputyStep>> readDeputySteps() =>
      _call('readDeputySteps', const {}, (r) => [for (final j in _list(r)) DeputyStep.fromJson(j)]);
  @override
  Future<void> stopDeputy() => _do('stopDeputy');
  @override
  Future<List<DeputyTurn>> readDeputyTurns() =>
      _call('readDeputyTurns', const {}, (r) => [for (final j in _list(r)) DeputyTurn.fromJson(j)]);
  @override
  Future<Map<String, dynamic>?> readDeputyShowing() =>
      _call('readDeputyShowing', const {}, (r) => (r as Map?)?.cast<String, dynamic>());
  @override
  Future<void> resetDeputy() => _do('resetDeputy');

  @override
  Future<StorageReport> measureStorage() => _call('measureStorage', const {}, (r) => w.storageReportOf((r as Map).cast()));
  @override
  Future<int> clearTranscripts() => _call('clearTranscripts', const {}, (r) => (r as num).toInt());
  @override
  Future<Map<String, dynamic>> readAcknowledged() =>
      _call('readAcknowledged', const {}, (r) => (r as Map?)?.cast<String, dynamic>() ?? const {});
  @override
  Future<Map<String, dynamic>> markAcknowledged({
    List<String> read = const [],
    List<String> unread = const [],
    List<String> dismissed = const [],
  }) =>
      _call('markAcknowledged', {'read': read, 'unread': unread, 'dismissed': dismissed},
          (r) => (r as Map?)?.cast<String, dynamic>() ?? const {});
  @override
  Future<Map<String, dynamic>?> readAppSettings() => _call('readAppSettings', const {}, (r) => (r as Map?)?.cast<String, dynamic>());
  @override
  Future<void> writeAppSettings(Map<String, dynamic> settings) => _do('writeAppSettings', {'settings': settings});
  @override
  Future<String> storeSignatureImage(List<int> bytes, String ext) =>
      _call('storeSignatureImage', {'bytes': bytes, 'ext': ext}, (r) => r as String);
  @override
  Future<Map<String, bool>> inspectPath(String path) =>
      _call('inspectPath', {'path': path}, (r) => (r as Map).map((k, v) => MapEntry('$k', v == true)));
  @override
  Future<EngineInfo> hello() => _call('hello', const {}, (r) => EngineInfo.fromJson((r as Map).cast()));

  /// A file the desk holds (a signature image), as a URL this client can load.
  String fileUrl(String path) => '${rpc.base}/file?path=${Uri.encodeQueryComponent(path)}${rpc.token == null ? '' : '&token=${rpc.token}'}';

  void dispose() {
    _changes.cancel();
    rpc.close();
  }
}

/// The provider tools, over the same wire.
class RemoteProviderClient implements ProviderClient {
  RemoteProviderClient(this.rpc);
  final RpcClient rpc;

  Future<T> _call<T>(String m, Map<String, Object?> a, T Function(Object? r) decode) async => decode(await rpc.call('provider.$m', a));
  List<w.Json> _list(Object? r) => [for (final e in (r as List? ?? const [])) (e as Map).cast<String, dynamic>()];

  @override
  Future<List<ProviderStatus>> list() => _call('list', const {}, (r) => [for (final j in _list(r)) w.providerStatusOf(j)]);
  @override
  Future<ProviderStatus> status(String provider) => _call('status', {'provider': provider}, (r) => w.providerStatusOf((r as Map).cast()));
  @override
  Future<ProviderModels> models(String provider) => _call('models', {'provider': provider}, (r) => w.providerModelsOf((r as Map).cast()));
  @override
  Stream<LoginEvent> login(String provider, {String? apiKey}) =>
      rpc.subscribe('provider.login', {'provider': provider, 'api_key': apiKey}).map((v) => w.loginEventOf((v as Map).cast()));
  @override
  Future<void> logout(String provider) => rpc.call('provider.logout', {'provider': provider});
  @override
  Future<void> use(String provider, String model, ModelRole? role, {String? effort, String? project}) =>
      rpc.call('provider.use', {'provider': provider, 'model': model, 'role': role?.name, 'effort': effort, 'project': project});
  @override
  Future<ProviderStatus> configure(String provider, Map<String, String> set) =>
      _call('configure', {'provider': provider, 'set': set}, (r) => w.providerStatusOf((r as Map).cast()));
  @override
  Future<ProviderStatus> addLocal(String name, {required String baseUrl, String? displayName}) =>
      _call('addLocal', {'name': name, 'base_url': baseUrl, 'display_name': displayName}, (r) => w.providerStatusOf((r as Map).cast()));
  @override
  Future<void> remove(String provider) => rpc.call('provider.remove', {'provider': provider});
  @override
  Future<Map<String, dynamic>> check(String provider) => _call('check', {'provider': provider}, (r) => (r as Map).cast<String, dynamic>());
  @override
  Future<Map<ModelRole, ModelChoice>> current() => _call('current', const {}, (r) => _choices(r));
  @override
  Future<Map<ModelRole, ModelChoice>> taskChoices(String projectPath) => _call('taskChoices', {'project': projectPath}, (r) => _choices(r));

  static Map<ModelRole, ModelChoice> _choices(Object? r) => {
        for (final e in ((r as Map?) ?? const {}).entries)
          ModelRole.values.firstWhere((x) => x.name == e.key): ModelChoice(
            provider: (e.value as Map)['provider'] as String?,
            model: (e.value as Map)['model'] as String?,
            effort: (e.value as Map)['effort'] as String?,
          ),
      };
}
