import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/loop.dart';
import '../models/provider.dart';
import 'engine.dart';
import 'provider_client.dart';
import 'rpc.dart' show RpcException;
import 'wire.dart' as w;

/// Serves an [Engine] and a [ProviderClient] over HTTP + WebSocket in the
/// shape [RpcClient] speaks, and the built web app beside it. One desk,
/// any number of browsers on the tailnet.
class RpcServer {
  RpcServer({
    required this.engine,
    required this.providers,
    this.webRoot,
    this.token,
    this.fileRoots = const [],
  });

  final Engine engine;
  final ProviderClient providers;

  /// `build/web`, when the page should be served too.
  final Directory? webRoot;

  /// When set, every request must carry it (`Authorization: Bearer`, or
  /// `?token=` for sockets and files).
  final String? token;

  /// Directories a `/file?path=` request may read from (the config dir,
  /// for signature images). Nothing else is ever served.
  final List<String> fileRoots;

  HttpServer? _server;

  /// The port actually bound (0 asks the OS for a free one).
  int get port => _server?.port ?? 0;
  final _sockets = <WebSocket>{};
  StreamSubscription<void>? _changes;

  Future<void> start({InternetAddress? address, int port = 7355}) async {
    _server = await HttpServer.bind(address ?? InternetAddress.anyIPv4, port);
    _changes = engine.changes.listen((_) {
      final msg = jsonEncode({'t': 'changes'});
      for (final s in _sockets) {
        s.add(msg);
      }
    });
    _server!.listen(_handle);
  }

  Future<void> stop() async {
    await _changes?.cancel();
    for (final s in _sockets) {
      await s.close();
    }
    await _server?.close(force: true);
  }

  bool _authorized(HttpRequest r) {
    if (token == null) return true;
    final h = r.headers.value('authorization');
    if (h == 'Bearer $token') return true;
    return r.uri.queryParameters['token'] == token;
  }

  /// The page and its scripts are public (they hold nothing); the engine,
  /// its socket and the desk's files need the token.
  static bool _guarded(HttpRequest r) => const {'/rpc', '/ws', '/file'}.contains(r.uri.path);

  Future<void> _handle(HttpRequest r) async {
    try {
      if (_guarded(r) && !_authorized(r)) {
        r.response.statusCode = HttpStatus.unauthorized;
        await r.response.close();
        return;
      }
      switch ((r.method, r.uri.path)) {
        case ('POST', '/rpc'):
          await _rpc(r);
        case ('GET', '/ws'):
          final ws = await WebSocketTransformer.upgrade(r);
          _socket(ws);
        case ('GET', '/file'):
          await _file(r);
        case ('GET', _):
          await _static(r);
        default:
          r.response.statusCode = HttpStatus.methodNotAllowed;
          await r.response.close();
      }
    } catch (e) {
      try {
        r.response.statusCode = HttpStatus.internalServerError;
        r.response.write('$e');
        await r.response.close();
      } catch (_) {}
    }
  }

  Future<void> _rpc(HttpRequest r) async {
    final body = jsonDecode(await utf8.decoder.bind(r).join()) as Map<String, dynamic>;
    final m = body['m'] as String;
    final a = (body['a'] as Map?)?.cast<String, dynamic>() ?? const {};
    Object? out;
    try {
      out = {'r': await dispatch(m, a)};
    } catch (e) {
      out = {'e': '$e'};
    }
    r.response.headers.contentType = ContentType.json;
    r.response.write(jsonEncode(out));
    await r.response.close();
  }

  void _socket(WebSocket ws) {
    _sockets.add(ws);
    final subs = <int, StreamSubscription<Object?>>{};
    ws.listen(
      (data) {
        final j = jsonDecode(data as String) as Map<String, dynamic>;
        final id = j['id'] as int?;
        switch (j['t']) {
          case 'sub' when id != null:
            final stream = subscribeTo(j['m'] as String, (j['a'] as Map?)?.cast<String, dynamic>() ?? const {});
            subs[id]?.cancel();
            subs[id] = stream.listen(
              (v) => ws.add(jsonEncode({'t': 'v', 'id': id, 'v': v})),
              onError: (Object e) => ws.add(jsonEncode({'t': 'err', 'id': id, 'e': '$e'})),
              onDone: () => ws.add(jsonEncode({'t': 'done', 'id': id})),
            );
          case 'unsub' when id != null:
            subs.remove(id)?.cancel();
        }
      },
      onDone: () {
        for (final s in subs.values) {
          s.cancel();
        }
        _sockets.remove(ws);
      },
      onError: (_) {},
    );
  }

  Future<void> _file(HttpRequest r) async {
    final path = r.uri.queryParameters['path'] ?? '';
    final f = File(path);
    final allowed = fileRoots.any((root) => f.absolute.path.startsWith(root));
    if (!allowed || !f.existsSync()) {
      r.response.statusCode = HttpStatus.notFound;
      await r.response.close();
      return;
    }
    r.response.headers.contentType = _mime(path);
    await r.response.addStream(f.openRead());
    await r.response.close();
  }

  Future<void> _static(HttpRequest r) async {
    final root = webRoot;
    if (root == null) {
      r.response.statusCode = HttpStatus.notFound;
      r.response.write('mizpah engine: no web app built beside me (flutter build web)');
      await r.response.close();
      return;
    }
    var rel = r.uri.path == '/' ? 'index.html' : r.uri.path.substring(1);
    var f = File('${root.path}/$rel');
    if (!f.existsSync() || !f.absolute.path.startsWith(root.absolute.path)) {
      // A single-page app: unknown paths are the page.
      f = File('${root.path}/index.html');
      rel = 'index.html';
    }
    r.response.headers.contentType = _mime(rel);
    // Fresh page each time; the flutter bootstrap is versioned by content.
    if (rel == 'index.html') r.response.headers.set('cache-control', 'no-cache');
    await r.response.addStream(f.openRead());
    await r.response.close();
  }

  static ContentType _mime(String path) {
    final ext = path.split('.').last.toLowerCase();
    return switch (ext) {
      'html' => ContentType.html,
      'js' => ContentType('text', 'javascript', charset: 'utf-8'),
      'mjs' => ContentType('text', 'javascript', charset: 'utf-8'),
      'css' => ContentType('text', 'css', charset: 'utf-8'),
      'json' => ContentType.json,
      'png' => ContentType('image', 'png'),
      'jpg' || 'jpeg' => ContentType('image', 'jpeg'),
      'svg' => ContentType('image', 'svg+xml'),
      'ico' => ContentType('image', 'x-icon'),
      'wasm' => ContentType('application', 'wasm'),
      'ttf' => ContentType('font', 'ttf'),
      'otf' => ContentType('font', 'otf'),
      'woff2' => ContentType('font', 'woff2'),
      _ => ContentType.binary,
    };
  }

  // ---- the method table ------------------------------------------------------

  static String _s(Map<String, dynamic> a, String k) => a[k] as String;
  static String? _sn(Map<String, dynamic> a, String k) => a[k] as String?;
  static int? _in(Map<String, dynamic> a, String k) => (a[k] as num?)?.toInt();
  static List<w.Json> _l(Iterable<Map<String, dynamic>> xs) => xs.toList();

  /// Every call the seam has, by name. Results are wire JSON.
  Future<Object?> dispatch(String m, Map<String, dynamic> a) async {
    final e = engine;
    switch (m) {
      case 'hello':
        return (await e.hello()).toJson();
      case 'listBriefs':
        return [for (final b in await e.listBriefs()) w.briefSummary(b)];
      case 'readBrief':
        return e.readBrief(_s(a, 'id'));
      case 'writeBrief':
        await e.writeBrief(_s(a, 'id'), (a['brief'] as Map).cast<String, dynamic>());
        return null;
      case 'readRoute':
        return w.routeStatus(await e.readRoute(_s(a, 'id')));
      case 'readRouteLog':
        return [for (final x in await e.readRouteLog(_s(a, 'id'))) w.routeEvent(x)];
      case 'setPriority':
        await e.setPriority(_s(a, 'id'), _s(a, 'task'), _s(a, 'priority'), _sn(a, 'reason') ?? '');
        return null;
      case 'cancelTask':
        await e.cancelTask(_s(a, 'id'), _s(a, 'task'), _sn(a, 'reason') ?? '');
        return null;
      case 'unblockTask':
        await e.unblockTask(_s(a, 'id'), _s(a, 'task'));
        return null;
      case 'acceptProposal':
        await e.acceptProposal(_s(a, 'id'), _s(a, 'proposal'), reason: _sn(a, 'reason') ?? '');
        return null;
      case 'rejectProposal':
        await e.rejectProposal(_s(a, 'id'), _s(a, 'proposal'), reason: _sn(a, 'reason') ?? '');
        return null;
      case 'setMode':
        await e.setMode(_s(a, 'id'), LoopMode.values.firstWhere((x) => x.name == a['mode']));
        return null;
      case 'readProcedures':
        return [for (final p in await e.readProcedures()) w.procedure(p)];
      case 'createProcedure':
        return e.createProcedure(_s(a, 'title'));
      case 'deleteProcedure':
        await e.deleteProcedure(_s(a, 'id'));
        return null;
      case 'commitProcedures':
        return e.commitProcedures(_s(a, 'message'));
      case 'procedureLog':
        return [for (final v in await e.procedureLog(_s(a, 'id'))) w.procedureVersion(v)];
      case 'procedureDiff':
        return [for (final l in await e.procedureDiff(_s(a, 'id'), _s(a, 'sha'))) w.diffLine(l)];
      case 'restoreProcedure':
        return e.restoreProcedure(_s(a, 'id'), _s(a, 'sha'));
      case 'editProcedure':
        await e.editProcedure(_s(a, 'id'), title: _sn(a, 'title'), description: _sn(a, 'description'), tags: (a['tags'] as List?)?.cast<String>());
        return null;
      case 'editStep':
        await e.editStep(_s(a, 'id'), _s(a, 'step'), rename: _sn(a, 'rename'), do_: _sn(a, 'do'));
        return null;
      case 'addStep':
        await e.addStep(_s(a, 'id'), _s(a, 'title'), _s(a, 'do'), after: _sn(a, 'after'));
        return null;
      case 'removeStep':
        await e.removeStep(_s(a, 'id'), _s(a, 'step'));
        return null;
      case 'moveStep':
        await e.moveStep(_s(a, 'id'), _s(a, 'step'), _in(a, 'to')!);
        return null;
      case 'linkStep':
        await e.linkStep(_s(a, 'id'), _s(a, 'step'), _s(a, 'procedure'));
        return null;
      case 'readLibraryPolicy':
        return w.libraryPolicy(await e.readLibraryPolicy());
      case 'setLibraryPolicy':
        await e.setLibraryPolicy(enabled: a['enabled'] as bool?, grace: _in(a, 'grace'));
        return null;
      case 'pinProcedure':
        await e.pinProcedure(_s(a, 'id'), a['on'] == true);
        return null;
      case 'retireProcedure':
        await e.retireProcedure(_s(a, 'id'));
        return null;
      case 'reviveProcedure':
        await e.reviveProcedure(_s(a, 'id'));
        return null;
      case 'touchProcedure':
        await e.touchProcedure(_s(a, 'id'));
        return null;
      case 'startTask':
        await e.startTask(_s(a, 'id'));
        return null;
      case 'createTask':
        return e.createTask(_s(a, 'title'), mission: _s(a, 'mission'), repo: _sn(a, 'repo'));
      case 'authorizeDraft':
        return e.authorizeDraft(_s(a, 'id'), repo: _sn(a, 'repo'));
      case 'readSessions':
        return [for (final s in await e.readSessions(_s(a, 'id'))) w.floorSession(s)];
      case 'readTraceRules':
        return [for (final t in await e.readTraceRules(_s(a, 'id'), _s(a, 'session'), before: _in(a, 'before') ?? 0)) w.turn(t)];
      case 'readTraceWindow':
        return w.traceWindow(await e.readTraceWindow(_s(a, 'id'), _s(a, 'session'), end: _in(a, 'end'), from: _in(a, 'from'), ifLength: _in(a, 'if_length')));
      case 'readInbox':
        return [for (final d in await e.readInbox(_s(a, 'id'))) d.toJson()];
      case 'readWorkOrders':
        return [for (final d in await e.readWorkOrders(_s(a, 'id'))) d.toJson()];
      case 'readDataBook':
        return w.dataBook(await e.readDataBook(_s(a, 'id')));
      case 'readBudget':
        return w.budgetLedger(await e.readBudget(_s(a, 'id')));
      case 'readAttention':
        return [for (final x in await e.readAttention()) w.attentionItem(x)];
      case 'readCrew':
        return e.readCrew(_s(a, 'id'));
      case 'environments':
        return e.environments();
      case 'listTaskFiles':
        return [for (final x in await e.listTaskFiles(_s(a, 'id'), a['path'] as String? ?? '')) x.toJson()];
      case 'readTaskFileBytes':
        return base64Encode(await e.readTaskFileBytes(_s(a, 'id'), _s(a, 'path')));
      case 'readTaskFile':
        return (await e.readTaskFile(_s(a, 'id'), _s(a, 'path'))).toJson();
      case 'readEnvironmentFile':
        return (await e.readEnvironmentFile(_s(a, 'name'), _s(a, 'path'))).toJson();
      case 'createEnvironment':
        await e.createEnvironment(_s(a, 'name'));
        return null;
      case 'readEnvironmentDetails':
        return [for (final x in await e.readEnvironmentDetails()) x.toJson()];
      case 'listEnvironmentFiles':
        return [for (final x in await e.listEnvironmentFiles(_s(a, 'name'), a['path'] as String? ?? '')) x.toJson()];
      case 'setDefaultEnvironment':
        await e.setDefaultEnvironment(a['name'] as String);
        return null;
      case 'sessions':
        return {'sessions': [for (final s in e.sessions(_s(a, 'id'))) w.runSession(s)], 'current': e.currentSession(_s(a, 'id'))};
      case 'chooseSession':
        e.chooseSession(_s(a, 'id'), _s(a, 'session'));
        return null;
      case 'archiveSession':
        await e.archiveSession(_s(a, 'id'), _s(a, 'session'));
        return null;
      case 'archiveProject':
        await e.archiveProject(_s(a, 'id'));
        return null;
      case 'unarchiveProject':
        await e.unarchiveProject(_s(a, 'id'));
        return null;
      case 'deleteProject':
        await e.deleteProject(_s(a, 'id'));
        return null;
      case 'discardDraft':
        await e.discardDraft(_s(a, 'id'));
        return null;
      case 'deleteSession':
        await e.deleteSession(_s(a, 'id'), _s(a, 'session'));
        return null;
      case 'memoToWorker':
        await e.memoToWorker(_s(a, 'id'), _s(a, 'task'), _s(a, 'text'));
        return null;
      case 'replyToRun':
        await e.replyToRun(_s(a, 'id'), _s(a, 'text'), resume: a['resume'] == true);
        return null;
      case 'deputySay':
        await e.deputySay(_s(a, 'text'));
        return null;
      case 'readDeputyActivity':
        return e.readDeputyActivity();
      case 'readDeputySteps':
        return [for (final s in await e.readDeputySteps()) s.toJson()];
      case 'stopDeputy':
        await e.stopDeputy();
        return null;
      case 'readDeputyTurns':
        return [for (final t in await e.readDeputyTurns()) w.deputyTurn(t)];
      case 'readDeputyShowing':
        return e.readDeputyShowing();
      case 'resetDeputy':
        await e.resetDeputy();
        return null;
      case 'measureStorage':
        return w.storageReport(await e.measureStorage());
      case 'clearTranscripts':
        return e.clearTranscripts();
      case 'readAcknowledged':
        return e.readAcknowledged();
      case 'markAcknowledged':
        return e.markAcknowledged(
          read: (a['read'] as List? ?? const []).cast<String>(),
          unread: (a['unread'] as List? ?? const []).cast<String>(),
          dismissed: (a['dismissed'] as List? ?? const []).cast<String>(),
        );
      case 'readAppSettings':
        return e.readAppSettings();
      case 'writeAppSettings':
        await e.writeAppSettings((a['settings'] as Map).cast<String, dynamic>());
        return null;
      case 'storeSignatureImage':
        return e.storeSignatureImage((a['bytes'] as List).cast<int>(), _s(a, 'ext'));
      case 'inspectPath':
        return e.inspectPath(_s(a, 'path'));
      // -- providers --
      case 'provider.list':
        return _l([for (final s in await providers.list()) w.providerStatus(s)]);
      case 'provider.status':
        return w.providerStatus(await providers.status(_s(a, 'provider')));
      case 'provider.models':
        return w.providerModels(await providers.models(_s(a, 'provider')));
      case 'provider.logout':
        await providers.logout(_s(a, 'provider'));
        return null;
      case 'provider.use':
        final role = a['role'] == null ? null : ModelRole.values.firstWhere((x) => x.name == a['role']);
        await providers.use(_s(a, 'provider'), _s(a, 'model'), role, effort: _sn(a, 'effort'), project: _sn(a, 'project'));
        return null;
      case 'provider.configure':
        return w.providerStatus(await providers.configure(_s(a, 'provider'), (a['set'] as Map).cast<String, String>()));
      case 'provider.addLocal':
        return w.providerStatus(await providers.addLocal(_s(a, 'name'), baseUrl: _s(a, 'base_url'), displayName: _sn(a, 'display_name')));
      case 'provider.remove':
        await providers.remove(_s(a, 'provider'));
        return null;
      case 'provider.check':
        return providers.check(_s(a, 'provider'));
      case 'provider.current':
        return _choices(await providers.current());
      case 'provider.taskChoices':
        return _choices(await providers.taskChoices(_s(a, 'project')));
      default:
        throw RpcException('no such method: $m');
    }
  }

  static Map<String, Object?> _choices(Map<ModelRole, ModelChoice> c) => {
        for (final e in c.entries) e.key.name: {'provider': e.value.provider, 'model': e.value.model, 'effort': e.value.effort},
      };

  /// Every stream the seam has, by name, as wire JSON values.
  Stream<Object?> subscribeTo(String m, Map<String, dynamic> a) => switch (m) {
        'watchLoop' => engine.watchLoop(_s(a, 'id')).map(w.loopState),
        'watchTranscript' => engine.watchTranscript(_s(a, 'id'), _s(a, 'session')).map((ts) => [for (final t in ts) w.turn(t)]),
        'provider.login' => providers.login(_s(a, 'provider'), apiKey: _sn(a, 'api_key')).map(w.loginEvent),
        _ => Stream.error(RpcException('no such stream: $m')),
      };
}
