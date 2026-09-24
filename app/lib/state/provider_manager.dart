import 'dart:async';

import 'package:flutter/foundation.dart';

import '../engine/provider_client.dart';
import '../models/provider.dart';

/// The provider desk: every provider's sign-in state, the one selected,
/// a sign-in in flight, and which model each role runs on.
class ProviderManager extends ChangeNotifier {
  ProviderManager(this._client);

  final ProviderClient _client;

  /// Told each time a model is chosen for a role; the settings keep the
  /// recent list a short picker offers.
  void Function(String provider, String model)? onUsed;

  List<ProviderStatus> providers = const [];
  String? selected;
  ProviderModels? models;
  Map<ModelRole, ModelChoice> current = const {};

  /// Set while a sign-in runs: the prompt to show, once the engine has it.
  LoginPrompt? prompt;
  bool signingIn = false;
  String? error;
  StreamSubscription<LoginEvent>? _login;

  ProviderStatus? get selectedStatus =>
      providers.where((p) => p.name == selected).firstOrNull;

  Future<void> load() async {
    try {
      providers = await _client.list();
      current = await _client.current();
      error = null;
    } on Exception catch (e) {
      error = '$e';
    }
    selected ??= providers.firstOrNull?.name;
    notifyListeners();
    await _loadModels();
  }

  Future<void> select(String name) async {
    if (name == selected) return;
    cancelLogin();
    selected = name;
    models = null;
    checkReport = null;
    error = null;
    notifyListeners();
    await _loadModels();
  }

  Future<void> _loadModels() async {
    final name = selected;
    if (name == null) return;
    try {
      final m = await _client.models(name);
      _catalog[name] = m;
      if (selected == name) models = m;
    } on Exception catch (e) {
      error = '$e';
    }
    notifyListeners();
  }

  /// Every provider's model list read so far, whichever is selected: what
  /// an effort chip on a seat needs to know which levels its model takes.
  final Map<String, ProviderModels> _catalog = {};
  final Set<String> _cataloguing = {};

  /// The effort levels `provider`/`model` takes: empty for a model that
  /// takes none, and empty (then a repaint) until the provider's list has
  /// been read, which this starts.
  List<String> effortsOf(String? provider, String? model) {
    if (provider == null || model == null) return const [];
    final known = _catalog[provider];
    if (known != null) return known.details[model]?.efforts ?? const [];
    if (_cataloguing.add(provider)) {
      _client.models(provider).then((m) {
        _catalog[provider] = m;
        notifyListeners();
      }).catchError((_) {}).whenComplete(() => _cataloguing.remove(provider));
    }
    return const [];
  }

  /// Set a seat's effort, the model staying as it is: the default seat
  /// (harness config) or one task's own (`projectPath`).
  Future<void> setEffort(ModelRole role, ModelChoice choice, String effort, {String? projectPath}) async {
    final provider = choice.provider;
    final model = choice.model;
    if (provider == null || model == null) return;
    try {
      await _client.use(provider, model, role, effort: effort, project: projectPath);
      if (projectPath == null) current = await _client.current();
      error = null;
    } on Exception catch (e) {
      error = '$e';
    }
    notifyListeners();
  }

  /// Start the selected provider's flow. The prompt arrives on the stream;
  /// the screen shows it until [LoginDone] / [LoginFailed].
  void login({String? apiKey}) {
    final name = selected;
    if (name == null || signingIn) return;
    signingIn = true;
    prompt = null;
    error = null;
    notifyListeners();
    _login = _client
        .login(name, apiKey: apiKey)
        .listen(
          (event) {
            switch (event) {
              case LoginPrompt():
                prompt = event;
              case LoginDone():
                _replace(event.status);
                _finish();
                _loadModels();
              case LoginFailed():
                error = event.error;
                _finish();
            }
            notifyListeners();
          },
          onError: (Object e) {
            error = '$e';
            _finish();
            notifyListeners();
          },
          onDone: () {
            if (signingIn) {
              error ??= 'sign-in ended without a result';
              _finish();
              notifyListeners();
            }
          },
        );
  }

  void cancelLogin() {
    if (!signingIn) return;
    _finish();
    notifyListeners();
  }

  void _finish() {
    _login?.cancel();
    _login = null;
    signingIn = false;
    prompt = null;
  }

  Future<void> logout() async {
    final name = selected;
    if (name == null) return;
    try {
      await _client.logout(name);
      _replace(await _client.status(name));
      error = null;
      await _loadModels();
    } on Exception catch (e) {
      error = '$e';
    }
    notifyListeners();
  }

  /// The effort the person picked on a model's row before choosing a
  /// role for it; falls back to the model's default.
  final Map<String, String> pickedEffort = {};

  String? effortFor(String model) {
    final detail = models?.details[model];
    if (detail == null || detail.efforts.isEmpty) return null;
    for (final choice in current.values) {
      if (choice.provider == selected && choice.model == model) {
        return pickedEffort[model] ?? choice.effort ?? detail.defaultEffort;
      }
    }
    return pickedEffort[model] ?? detail.defaultEffort;
  }

  /// Pick an effort on a row; roles already on that model follow it.
  Future<void> pickEffort(String model, String effort) async {
    pickedEffort[model] = effort;
    notifyListeners();
    final name = selected;
    if (name == null) return;
    final on = current.entries
        .where((e) => e.value.provider == name && e.value.model == model)
        .map((e) => e.key)
        .toList();
    if (on.length == ModelRole.values.length) {
      await use(model, null);
    } else {
      for (final role in on) {
        await use(model, role);
      }
    }
  }

  String? baseUrlOf(String provider) =>
      providers.where((p) => p.name == provider).firstOrNull?.baseUrl;

  Future<void> addLocal(String name, String baseUrl) async {
    final id = name.trim().toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '_');
    if (id.isEmpty) return;
    try {
      await _client.addLocal(id, baseUrl: baseUrl.trim(),
          displayName: name.trim());
      error = null;
    } on Exception catch (e) {
      error = '$e';
      notifyListeners();
      return;
    }
    await load();
    await select(id);
  }

  Future<void> remove(String name) async {
    try {
      await _client.remove(name);
      error = null;
    } on Exception catch (e) {
      error = '$e';
    }
    if (selected == name) selected = null;
    await load();
  }

  /// The last check report for the selected provider, while it is shown.
  Map<String, dynamic>? checkReport;
  bool checking = false;

  Future<void> check() async {
    final name = selected;
    if (name == null || checking) return;
    checking = true;
    checkReport = null;
    notifyListeners();
    try {
      checkReport = await _client.check(name);
      error = null;
    } on Exception catch (e) {
      error = '$e';
    }
    checking = false;
    notifyListeners();
  }

  Future<void> setBaseUrl(String url) => setField('api_base_url', url);

  /// A setting that fills a {field} in the address: project, region, …
  Future<void> setSetting(String name, String value) =>
      setField('template_values.$name', value);

  /// Override one endpoint field on the selected provider. An empty value
  /// clears the override so the profile's own value shows again.
  Future<void> setField(String key, String value) async {
    final name = selected;
    if (name == null) return;
    try {
      _replace(await _client.configure(name, {key: value.trim()}));
      error = null;
    } on Exception catch (e) {
      error = '$e';
    }
    notifyListeners();
    await _loadModels();
  }

  /// The seats a task has chosen for itself, or nothing where it rides
  /// on the user's default. The client reads the task's config where it is.
  Future<Map<ModelRole, ModelChoice>> taskChoices(String projectPath) => _client.taskChoices(projectPath);


  /// Choose a model for one role of one task only; the user's default is
  /// untouched.
  Future<void> useForTask(String projectPath, String model, ModelRole role) async {
    final name = selected;
    if (name == null) return;
    try {
      await _client.use(name, model, role, effort: effortFor(model), project: projectPath);
      onUsed?.call(name, model);
      error = null;
    } on Exception catch (e) {
      error = '$e';
    }
    notifyListeners();
  }

  Future<void> use(String model, ModelRole? role) async {
    final name = selected;
    if (name == null) return;
    try {
      await _client.use(name, model, role, effort: effortFor(model));
      current = await _client.current();
      onUsed?.call(name, model);
      error = null;
    } on Exception catch (e) {
      error = '$e';
    }
    notifyListeners();
  }

  /// Choose `provider`/`model` for one role from outside the Providers
  /// page: the provider is selected (its model list loads, so the effort
  /// is the one picked before or the model's default) and then used.
  Future<void> pick(String provider, String model, ModelRole role) async {
    if (selected != provider) {
      cancelLogin();
      selected = provider;
      models = null;
      checkReport = null;
      error = null;
      notifyListeners();
      await _loadModels();
    }
    await use(model, role);
  }

  void _replace(ProviderStatus s) {
    providers = [for (final p in providers) p.name == s.name ? s : p];
  }

  @override
  void dispose() {
    _login?.cancel();
    super.dispose();
  }
}
