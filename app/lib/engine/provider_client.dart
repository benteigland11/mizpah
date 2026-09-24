import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../models/provider.dart';
import 'run_tool.dart';

/// The seam to `mizpah-provider`. One JSON object per line out of the
/// engine; the client turns lines into models and nothing else.
abstract class ProviderClient {
  Future<List<ProviderStatus>> list();
  Future<ProviderStatus> status(String provider);
  Future<ProviderModels> models(String provider);

  /// Runs the provider's sign-in flow. Emits the prompt first, then exactly
  /// one of [LoginDone] / [LoginFailed]. Cancelling the subscription aborts
  /// the flow.
  Stream<LoginEvent> login(String provider, {String? apiKey});

  Future<void> logout(String provider);

  /// Point [role] (or both) at a provider and model, with a reasoning
  /// effort when the model takes one (`'none'` removes it). The engine
  /// rewrites its harness config; the next session picks it up.
  /// With [project], the choice is that task's only: written to its
  /// `.mizpah/config.json` and layered over yours when it runs.
  Future<void> use(String provider, String model, ModelRole? role,
      {String? effort, String? project});

  /// Override profile fields in the engine config: any key the engine's
  /// `configure --set` accepts (api_base_url, completion_path, models_path,
  /// wire, tokenize_path, template_path, context_window, timeout_seconds,
  /// static_headers, auth.client_id…). An empty value removes the
  /// override. Returns the provider's status afterwards.
  Future<ProviderStatus> configure(String provider, Map<String, String> set);

  /// Add a server on this machine as a provider. The engine asks the
  /// server what it is (llama.cpp gets exact token counting).
  Future<ProviderStatus> addLocal(
    String name, {
    required String baseUrl,
    String? displayName,
  });

  /// Remove a provider the person added.
  Future<void> remove(String provider);

  /// List models and run one tiny completion; the engine reports each
  /// step's status so a wrong path or header shows up as text. Returns
  /// the report as the engine printed it (`steps`, `ok`).
  Future<Map<String, dynamic>> check(String provider);

  /// What each role is pointed at now.
  Future<Map<ModelRole, ModelChoice>> current();

  /// The seats one task chose for itself (`.mizpah/config.json`), or
  /// nothing where it rides on the default.
  Future<Map<ModelRole, ModelChoice>> taskChoices(String projectPath);
}

/// The real thing: spawns the `mizpah-provider` console script.
class ProcessProviderClient implements ProviderClient {
  ProcessProviderClient({required this.executable, required this.config});

  /// Path to the `mizpah-provider` script inside the engine's venv.
  final String executable;

  /// The engine config; `use` rewrites the harness config it names.
  final String config;

  Future<Map<String, dynamic>> _one(List<String> args) async {
    final r = await runTool(executable, ['--config', config, ...args]);
    final line = (r.stdout as String).trim().split('\n').last;
    if (line.isEmpty) {
      throw ProviderClientException(
        'mizpah-provider ${args.join(' ')} printed nothing: ${r.stderr}',
      );
    }
    final j = jsonDecode(line) as Map<String, dynamic>;
    if (j['event'] == 'error') throw ProviderClientException('${j['error']}');
    return j;
  }

  @override
  Future<List<ProviderStatus>> list() async {
    final j = await _one(['list']);
    return [
      for (final p in j['providers'] as List)
        ProviderStatus.fromJson((p as Map).cast<String, dynamic>()),
    ];
  }

  @override
  Future<ProviderStatus> status(String provider) async =>
      ProviderStatus.fromJson(await _one(['status', provider]));

  @override
  Future<ProviderModels> models(String provider) async =>
      ProviderModels.fromJson(await _one(['models', provider]));

  @override
  Stream<LoginEvent> login(String provider, {String? apiKey}) {
    late StreamController<LoginEvent> out;
    Process? process;
    out = StreamController<LoginEvent>(
      onListen: () async {
        process = await Process.start(executable, [
          '--config',
          config,
          'login',
          provider,
          if (apiKey != null) ...['--api-key', apiKey],
        ]);
        process!.stdout
            .transform(utf8.decoder)
            .transform(const LineSplitter())
            .listen(
              (line) {
                if (line.trim().isEmpty) return;
                try {
                  out.add(
                    LoginEvent.fromJson(
                      jsonDecode(line) as Map<String, dynamic>,
                    ),
                  );
                } catch (e) {
                  out.add(LoginFailed('unreadable engine line: $line'));
                }
              },
              onDone: () async {
                final code = await process!.exitCode;
                if (code != 0 && !out.isClosed) {
                  final err = await process!.stderr
                      .transform(utf8.decoder)
                      .join();
                  if (err.trim().isNotEmpty) {
                    out.add(LoginFailed(err.trim().split('\n').last));
                  }
                }
                await out.close();
              },
            );
      },
      onCancel: () {
        process?.kill();
      },
    );
    return out.stream;
  }

  @override
  Future<void> logout(String provider) => _one(['logout', provider]);

  @override
  Future<void> use(String provider, String model, ModelRole? role,
      {String? effort, String? project}) => _one([
    'use',
    provider,
    model,
    '--role',
    role?.name ?? 'all',
    if (effort != null) ...['--effort', effort],
    if (project != null) ...['--project', project],
  ]);

  @override
  Future<ProviderStatus> configure(
    String provider,
    Map<String, String> set,
  ) async {
    await _one([
      'configure',
      provider,
      for (final e in set.entries) ...['--set', '${e.key}=${e.value}'],
    ]);
    return status(provider);
  }

  @override
  Future<ProviderStatus> addLocal(
    String name, {
    required String baseUrl,
    String? displayName,
  }) async => ProviderStatus.fromJson(
    await _one([
      'add-local',
      name,
      '--base-url',
      baseUrl,
      if (displayName != null) ...['--display-name', displayName],
    ]),
  );

  @override
  Future<void> remove(String provider) => _one(['remove', provider]);

  @override
  Future<Map<String, dynamic>> check(String provider) async {
    // Exit code 1 is a failed check, still a report; only a missing line is an error.
    // Talks to the provider: give the network a minute.
    final r = await runTool(executable, ['--config', config, 'check', provider], timeout: const Duration(minutes: 1));
    final line = (r.stdout as String).trim().split('\n').last;
    if (line.isEmpty) throw ProviderClientException('check printed nothing: ${r.stderr}');
    return jsonDecode(line) as Map<String, dynamic>;
  }

  @override
  Future<Map<ModelRole, ModelChoice>> current() async {
    // The seats a run would use on this machine: the repository's defaults
    // with the user's choices (~/.config/mizpah/config.json) over them. The
    // engine does the layering; the app never reads a config file for this.
    final seats =
        ((await _one(['current']))['seats'] as Map?)?.cast<String, dynamic>() ??
        const {};
    return {
      for (final role in ModelRole.values)
        role: _choice((seats[role.name] as Map?)?.cast<String, dynamic>()),
    };
  }

  /// The seats a task has chosen for itself, from its `.mizpah/config.json`
  /// (`models.<role>`), or nothing where it rides on the user's default.
  @override
  Future<Map<ModelRole, ModelChoice>> taskChoices(String projectPath) async {
    final out = <ModelRole, ModelChoice>{};
    for (final dirName in const ['.mizpah', '.terra']) {
      final f = File('$projectPath/$dirName/config.json');
      if (!f.existsSync()) continue;
      try {
        final j = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
        final models = (j['models'] as Map?)?.cast<String, dynamic>() ?? const {};
        for (final role in ModelRole.values) {
          final spec = (models[role.name] as Map?)?.cast<String, dynamic>();
          if (spec == null) continue;
          final gen = (spec['generation'] as Map?)?.cast<String, dynamic>() ?? const {};
          out[role] = ModelChoice(
            provider: spec['subscription'] as String? ?? spec['provider'] as String?,
            model: gen['model'] as String?,
            effort: gen['reasoning_effort'] as String?,
          );
        }
      } catch (_) {}
      break;
    }
    return out;
  }

  static ModelChoice _choice(Map<String, dynamic>? spec) {
    if (spec == null) return ModelChoice.none;
    final generation = (spec['generation'] as Map?)?.cast<String, dynamic>();
    final endpoint = (spec['endpoint'] as Map?)?.cast<String, dynamic>();
    return ModelChoice(
      provider: switch (spec['provider']) {
        'subscription' => spec['subscription'] as String?,
        'llama_client' => 'local_llama',
        _ => 'local (${spec['provider'] ?? 'direct_json'} '
            '${endpoint?['base_url'] ?? ''})',
      },
      model: generation?['model'] as String?,
      effort: generation?['reasoning_effort'] as String?,
    );
  }
}

class ProviderClientException implements Exception {
  ProviderClientException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// In-memory client for tests and the style lab: sign-in completes as
/// soon as something listens, after the prompt.
class FakeProviderClient implements ProviderClient {
  FakeProviderClient({List<ProviderStatus>? providers})
    : _providers = {
        for (final p in providers ?? _defaults) p.name: p,
      };

  final Map<String, ProviderStatus> _providers;
  final Map<ModelRole, ModelChoice> _current = {
    ModelRole.worker: ModelChoice.none,
    ModelRole.controller: ModelChoice.none,
  };

  static const _defaults = [
    ProviderStatus(
      name: 'openai_chatgpt',
      displayName: 'ChatGPT (Plus/Pro/Team subscription)',
      authKind: 'oauth_pkce',
      signedIn: false,
    ),
    ProviderStatus(
      name: 'xai_grok',
      displayName: 'Grok (SuperGrok / X Premium+ subscription)',
      authKind: 'device_code',
      signedIn: false,
    ),
    ProviderStatus(
      name: 'openai_api',
      displayName: 'OpenAI API key',
      authKind: 'api_key',
      signedIn: false,
    ),
  ];

  @override
  Future<List<ProviderStatus>> list() async => _providers.values.toList();

  @override
  Future<ProviderStatus> status(String provider) async => _providers[provider]!;

  @override
  Future<ProviderModels> models(String provider) async {
    final signedIn = _providers[provider]!.signedIn;
    return ProviderModels(
      provider: provider,
      models: signedIn ? const ['model-large', 'model-small'] : const [],
      defaultModel: 'model-large',
      contextWindow: 200000,
      wire: 'responses',
      source: signedIn ? 'live' : 'signed_out',
      details: signedIn
          ? const {
              'model-large': ModelDetail(
                id: 'model-large',
                efforts: ['low', 'high'],
                defaultEffort: 'high',
                contextWindow: 200000,
              ),
              'model-small': ModelDetail(id: 'model-small'),
            }
          : const {},
    );
  }

  @override
  Stream<LoginEvent> login(String provider, {String? apiKey}) async* {
    final p = _providers[provider]!;
    if (p.authKind != 'api_key') {
      yield LoginPrompt(
        kind: p.authKind == 'device_code' ? 'device' : 'browser',
        url: 'https://auth.example.org/activate',
        userCode: p.authKind == 'device_code' ? 'WXYZ-1234' : null,
      );
      await Future<void>.delayed(const Duration(milliseconds: 300));
    }
    final done = ProviderStatus(
      name: p.name,
      displayName: p.displayName,
      authKind: p.authKind,
      signedIn: true,
      account: const {'email': 'user@example.org'},
    );
    _providers[provider] = done;
    yield LoginDone(done);
  }

  @override
  Future<void> logout(String provider) async {
    final p = _providers[provider]!;
    _providers[provider] = ProviderStatus(
      name: p.name,
      displayName: p.displayName,
      authKind: p.authKind,
      signedIn: false,
    );
  }

  @override
  Future<void> use(String provider, String model, ModelRole? role,
      {String? effort, String? project}) async {
    for (final r in role == null ? ModelRole.values : [role]) {
      _current[r] = ModelChoice(
        provider: provider,
        model: model,
        effort: effort == 'none' ? null : effort,
      );
    }
  }

  @override
  Future<ProviderStatus> configure(
    String provider,
    Map<String, String> set,
  ) async {
    final p = _providers[provider]!;
    final endpoint = Map<String, dynamic>.of(p.endpoint)..addAll(set);
    return _providers[provider] = ProviderStatus(
      name: p.name,
      displayName: p.displayName,
      authKind: p.authKind,
      signedIn: p.signedIn,
      reachable: p.reachable,
      baseUrl: set['api_base_url'] ?? p.baseUrl,
      custom: p.custom,
      endpoint: endpoint,
    );
  }

  @override
  Future<ProviderStatus> addLocal(
    String name, {
    required String baseUrl,
    String? displayName,
  }) async => _providers[name] = ProviderStatus(
    name: name,
    displayName: displayName ?? name,
    authKind: 'none',
    signedIn: false,
    reachable: false,
    reason: 'fake: nothing listening',
    baseUrl: baseUrl,
    custom: true,
  );

  @override
  Future<void> remove(String provider) async {
    _providers.remove(provider);
  }

  @override
  Future<Map<String, dynamic>> check(String provider) async => {
    'ok': true,
    'steps': [
      {'step': 'models', 'ok': true, 'count': 2},
      {'step': 'completion', 'ok': true, 'model': 'model-large', 'content': 'OK', 'seconds': 0.1},
    ],
  };

  @override
  Future<Map<ModelRole, ModelChoice>> current() async => Map.of(_current);
  @override
  Future<Map<ModelRole, ModelChoice>> taskChoices(String projectPath) async => const {};
}
