import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/local_engine.dart';
import 'package:mizpah_app/engine/file_settings_store.dart';
import 'package:mizpah_app/engine/provider_client.dart';
import 'package:mizpah_app/engine/remote_engine.dart';
import 'package:mizpah_app/engine/rpc.dart';
import 'package:mizpah_app/engine/rpc_server.dart';
import 'package:mizpah_app/models/provider.dart';

/// The wire, end to end: a LocalEngine on a scratch tasks folder behind an
/// RpcServer, read through RemoteEngine the way a browser would.
void main() {
  late Directory tmp;
  late LocalEngine engine;
  late RpcServer server;
  late RpcClient rpc;
  late RemoteEngine remote;
  late int port;

  setUp(() async {
    tmp = Directory.systemTemp.createTempSync('rpc');
    engine = LocalEngine(runsRoot: Directory('${tmp.path}/tasks'), marksFile: File('${tmp.path}/acknowledged.json'))
      ..settingsStore = FileSettingsStore(file: File('${tmp.path}/config/app.json'));
    server = RpcServer(engine: engine, providers: FakeProviderClient(), token: 'secret', fileRoots: ['${tmp.path}/config']);
    await server.start(address: InternetAddress.loopbackIPv4, port: 0);
    port = server.port;
    rpc = RpcClient('http://127.0.0.1:$port', token: 'secret');
    remote = RemoteEngine(rpc);
  });

  tearDown(() async {
    remote.dispose();
    await server.stop();
    engine.dispose();
    tmp.deleteSync(recursive: true);
  });

  test('calls cross the wire and come back typed', () async {
    final info = await remote.hello();
    expect(info.runsRoot, '${tmp.path}/tasks');
    expect(await remote.listBriefs(), isA<List>());
    expect(await remote.readAppSettings(), isNull);
    await remote.writeAppSettings({'theme': 'dark', 'signer_name': 'A. Person'});
    expect((await remote.readAppSettings())!['signer_name'], 'A. Person');
    // Marks are deltas merged on the host, never a copy written over.
    expect((await remote.markAcknowledged(read: ['x', 'y']))['read'], ['x', 'y']);
    expect((await remote.markAcknowledged(unread: ['x'], dismissed: ['z']))['read'], ['y']);
    final marks = await remote.readAcknowledged();
    expect(marks['read'], ['y']);
    expect(marks['dismissed'], ['z']);
    final at = await remote.inspectPath(tmp.path);
    expect(at['exists'], isTrue);
    expect(at['git'], isFalse);
    final path = await remote.storeSignatureImage([1, 2, 3], 'png');
    expect(File(path).readAsBytesSync(), [1, 2, 3]);
    // The file is served, with the token, only from an allowed root.
    final ok = await HttpClient().getUrl(Uri.parse(remote.fileUrl(path))).then((r) => r.close());
    expect(ok.statusCode, 200);
    final no = await HttpClient().getUrl(Uri.parse('http://127.0.0.1:$port/file?path=/etc/hostname&token=secret')).then((r) => r.close());
    expect(no.statusCode, 404);
    // The page is public; the engine is not.
    final page = await HttpClient().getUrl(Uri.parse('http://127.0.0.1:$port/main.dart.js')).then((r) => r.close());
    expect(page.statusCode, isNot(401));
    final rpcNoToken = await HttpClient().postUrl(Uri.parse('http://127.0.0.1:$port/rpc')).then((r) => r.close());
    expect(rpcNoToken.statusCode, 401);
  });

  test('a wrong token is refused; an unknown method is an error', () async {
    final bad = RpcClient('http://127.0.0.1:$port', token: 'nope');
    await expectLater(bad.call('hello'), throwsA(isA<RpcException>()));
    bad.close();
    await expectLater(rpc.call('noSuchThing'), throwsA(isA<RpcException>().having((e) => e.message, 'message', contains('no such method'))));
  });

  test('streams ride the socket: a fake provider login, prompt then done', () async {
    final providers = RemoteProviderClient(rpc);
    final events = await providers.login('xai_grok').toList();
    expect(events.first, isA<LoginPrompt>());
    expect(events.last, isA<LoginDone>());
    expect((events.last as LoginDone).status.signedIn, isTrue);
  });

  test('the engine change stream reaches the remote', () async {
    final seen = remote.changes.first.timeout(const Duration(seconds: 5));
    await Future<void>.delayed(const Duration(milliseconds: 200)); // socket up
    // A new draft appears in the tasks folder: discovery sees it on rescan.
    File('${tmp.path}/tasks/draft-x/.mizpah/brief.json')
      ..createSync(recursive: true)
      ..writeAsStringSync('{"title": "X", "mission": "m", "status": "draft", "needs": [], "deliverables": [], "non_goals": []}');
    engine.rescan();
    await seen;
    expect((await remote.listBriefs()).map((b) => b.title), contains('X'));
  });
}
