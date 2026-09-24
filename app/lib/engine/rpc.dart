import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

/// The wire between a browser (or any remote desk) and the engine server.
///
/// Calls are `POST /rpc` with `{"m": method, "a": {args}}` and answer
/// `{"r": result}` or `{"e": message}`. Streams ride one WebSocket at
/// `/ws`: the client subscribes with `{"t": "sub", "id", "m", "a"}` and
/// receives `{"t": "v", "id", "v"}` values until `{"t": "done"}` or
/// `{"t": "err", "e"}`; the server also pushes `{"t": "changes"}` whenever
/// the engine's change stream fires. Works on web and desktop alike.
class RpcClient {
  RpcClient(this.base, {this.token});

  /// `http://host:port`, no trailing slash.
  final String base;
  final String? token;

  final _http = http.Client();
  WebSocketChannel? _ws;
  int _nextSub = 1;
  final _subs = <int, StreamController<Object?>>{};
  final _specs = <int, Map<String, Object?>>{};
  final _changes = StreamController<void>.broadcast();
  final _connected = StreamController<bool>.broadcast();
  bool _closed = false;

  Map<String, String> get _headers => {
        'content-type': 'application/json',
        if (token != null) 'authorization': 'Bearer $token',
      };

  /// One call. Throws [RpcException] with the server's message on error.
  Future<Object?> call(String method, [Map<String, Object?> args = const {}]) async {
    final res = await _http.post(Uri.parse('$base/rpc'), headers: _headers, body: jsonEncode({'m': method, 'a': args}));
    if (res.statusCode != 200) throw RpcException('$method: HTTP ${res.statusCode} ${res.body}');
    final j = jsonDecode(res.body) as Map<String, dynamic>;
    if (j.containsKey('e')) throw RpcException('${j['e']}');
    return j['r'];
  }

  /// The engine's change stream, relayed.
  Stream<void> get changes => _changes.stream;

  /// Connection state, for a banner.
  Stream<bool> get connected => _connected.stream;

  /// A server-side stream, as a subscription over the socket.
  Stream<Object?> subscribe(String method, [Map<String, Object?> args = const {}]) {
    final id = _nextSub++;
    late StreamController<Object?> c;
    c = StreamController<Object?>(
      onListen: () {
        _subs[id] = c;
        _specs[id] = {'t': 'sub', 'id': id, 'm': method, 'a': args};
        _send(_specs[id]!);
      },
      onCancel: () {
        _subs.remove(id);
        _specs.remove(id);
        _send({'t': 'unsub', 'id': id});
        c.close();
      },
    );
    return c.stream;
  }

  String get _wsUrl {
    final u = Uri.parse(base);
    final scheme = u.scheme == 'https' ? 'wss' : 'ws';
    final q = token == null ? '' : '?token=$token';
    return '$scheme://${u.authority}/ws$q';
  }

  /// Messages wait until the socket is open: adding to the sink before
  /// then throws on the web and closes the handshake.
  bool _open = false;
  final _pending = <String>[];

  void _send(Map<String, Object?> msg) {
    _ensureSocket();
    final text = jsonEncode(msg);
    if (_open) {
      _ws?.sink.add(text);
    } else {
      _pending.add(text);
    }
  }

  /// Open the socket (once); reconnect with backoff when it drops, and
  /// re-subscribe whatever was live.
  void _ensureSocket() {
    if (_ws != null || _closed) return;
    final ws = WebSocketChannel.connect(Uri.parse(_wsUrl));
    _ws = ws;
    _open = false;
    ws.ready.then((_) {
      if (_ws != ws) return;
      _open = true;
      _connected.add(true);
      for (final text in _pending) {
        ws.sink.add(text);
      }
      _pending.clear();
    }).catchError((_) => _dropped());
    ws.stream.listen(
      (data) {
        final j = jsonDecode(data as String) as Map<String, dynamic>;
        switch (j['t']) {
          case 'changes':
            _changes.add(null);
          case 'v':
            _subs[j['id'] as int]?.add(j['v']);
          case 'done':
            _subs.remove(j['id'] as int)?.close();
          case 'err':
            _subs[j['id'] as int]?.addError(RpcException('${j['e']}'));
        }
      },
      onDone: _dropped,
      onError: (_) => _dropped(),
    );
  }

  Timer? _retry;
  void _dropped() {
    if (_ws == null) return;
    _ws = null;
    _open = false;
    _connected.add(false);
    if (_closed) return;
    _retry?.cancel();
    _retry = Timer(const Duration(seconds: 3), () {
      if (_closed) return;
      // Whatever is still listened to is asked for again, once open.
      _pending
        ..clear()
        ..addAll(_specs.values.map(jsonEncode));
      _ensureSocket();
    });
  }

  /// Keep the change relay alive from the start, so the first change is
  /// not missed while no stream is subscribed.
  void connect() => _ensureSocket();

  void close() {
    _closed = true;
    _retry?.cancel();
    _ws?.sink.close();
    _http.close();
  }
}

class RpcException implements Exception {
  RpcException(this.message);
  final String message;
  @override
  String toString() => message;
}
