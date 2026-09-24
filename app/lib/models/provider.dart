/// Hosted model providers as the app sees them: what `mizpah-provider`
/// prints, one JSON object per line.
library;

/// A provider's credential state. `signedIn` is the one field a screen
/// usually needs; the rest explains it.
class ProviderStatus {
  const ProviderStatus({
    required this.name,
    required this.displayName,
    required this.authKind,
    required this.signedIn,
    this.quarantined = false,
    this.quarantineReason,
    this.expired = false,
    this.updatedAt,
    this.account = const {},
    this.blocked,
    this.reachable,
    this.reason,
    this.baseUrl,
    this.custom = false,
    this.endpoint = const {},
    this.notes = '',
  });

  final String name;
  final String displayName;

  /// oauth_pkce (browser), device_code (code at a URL), api_key.
  final String authKind;
  final bool signedIn;
  final bool quarantined;
  final String? quarantineReason;
  /// Inside the refresh window; the engine handles it, the page does not show it.
  final bool expired;
  final String? updatedAt;

  /// Safe metadata the engine kept beside the secret: email, account id.
  final Map<String, dynamic> account;

  /// Why sign-in cannot start (a missing client id), or null.
  final String? blocked;

  /// For a no-auth (local) endpoint: whether it answered, and how.
  final bool? reachable;
  final String? reason;

  /// Where completions go; editable for a local endpoint.
  final String? baseUrl;

  /// Added by the person (a local server), so it can be removed.
  final bool custom;

  /// The editable shape of the endpoint: api_base_url, completion_path,
  /// models_path, wire, context_window, timeout_seconds, static_headers,
  /// tokenize_path, template_path, local_kind, plus `settings` (a project,
  /// a region, a resource name — the {fields} of the address) and `needs`
  /// (the settings still empty).
  final Map<String, dynamic> endpoint;

  /// What a person should know before using it; shown quietly.
  final String notes;

  /// Address {fields} that are settings, with their current values.
  Map<String, String> get settings =>
      ((endpoint['settings'] as Map?)?.cast<String, dynamic>() ?? const {})
          .map((k, v) => MapEntry(k, '${v ?? ''}'));

  List<String> get needs =>
      (endpoint['needs'] as List?)?.cast<String>() ?? const [];

  bool get local => authKind == 'none';

  static ProviderStatus fromJson(Map<String, dynamic> j) {
    final meta = (j['metadata'] as Map?)?.cast<String, dynamic>() ?? const {};
    return ProviderStatus(
      name: j['provider'] as String,
      displayName: j['display_name'] as String? ?? j['provider'] as String,
      authKind: j['auth_kind'] as String? ?? 'api_key',
      signedIn: j['signed_in'] == true,
      quarantined: j['quarantined'] == true,
      quarantineReason: j['quarantine_reason'] as String?,
      expired: j['expired'] == true,
      updatedAt: j['updated_at'] as String?,
      account: meta,
      blocked: j['blocked'] as String?,
      reachable: j['reachable'] as bool?,
      reason: j['reason'] as String?,
      baseUrl: j['api_base_url'] as String?,
      custom: j['custom'] == true,
      endpoint: (j['endpoint'] as Map?)?.cast<String, dynamic>() ?? const {},
      notes: j['notes'] as String? ?? '',
    );
  }

  /// One word for the badge.
  String get badge {
    if (local) return reachable == true ? 'ANSWERING' : 'NOT ANSWERING';
    if (blocked != null) return 'BLOCKED';
    if (quarantined) return 'REAUTH';
    if (signedIn) return 'SIGNED IN';
    return 'SIGNED OUT';
  }

  String get authBlurb => switch (authKind) {
    'oauth_pkce' => 'Browser sign-in with your subscription',
    'device_code' => 'Code at a URL, with your subscription',
    'none' => 'No credential; a server on this machine',
    _ => 'API key',
  };
}

/// One listed model: its effort ladder when it takes one, and context.
class ModelDetail {
  const ModelDetail({
    required this.id,
    this.efforts = const [],
    this.defaultEffort,
    this.contextWindow,
  });
  final String id;
  final List<String> efforts;
  final String? defaultEffort;
  final int? contextWindow;

  static ModelDetail fromJson(Map<String, dynamic> j) => ModelDetail(
    id: j['id'] as String,
    efforts: (j['efforts'] as List?)?.cast<String>() ?? const [],
    defaultEffort: j['default_effort'] as String?,
    contextWindow: j['context_window'] as int?,
  );
}

/// The models a provider offers, as the engine's profile lists them.
class ProviderModels {
  const ProviderModels({
    required this.provider,
    required this.models,
    required this.defaultModel,
    required this.contextWindow,
    required this.wire,
    this.source = 'live',
    this.details = const {},
  });
  final String provider;
  final List<String> models;

  /// By model id; absent for a model the engine knows nothing more about.
  final Map<String, ModelDetail> details;
  final String? defaultModel;
  final int? contextWindow;
  final String wire;

  /// Where [models] came from: `live` (the account listed them),
  /// `signed_out` (nothing shown until a credential exists),
  /// `no_list_endpoint` (the profile's catalogue), or `list_failed: …`
  /// (signed in, but the list call failed; the catalogue as a hint).
  final String source;

  bool get live => source == 'live';
  bool get signedOut => source == 'signed_out';
  bool get unreachable => source == 'unreachable';
  bool get unverified => source.startsWith('list_failed');

  static ProviderModels fromJson(Map<String, dynamic> j) => ProviderModels(
    provider: j['provider'] as String,
    models: (j['models'] as List).cast<String>(),
    defaultModel: j['default_model'] as String?,
    contextWindow: j['context_window'] as int?,
    wire: j['wire'] as String? ?? 'chat_completions',
    source: j['source'] as String? ?? 'live',
    details: {
      for (final d in (j['details'] as List?) ?? const [])
        (d as Map)['id'] as String: ModelDetail.fromJson(d.cast<String, dynamic>()),
    },
  );
}

/// One line of a login: the prompt to show, then the outcome.
sealed class LoginEvent {
  const LoginEvent();

  static LoginEvent fromJson(Map<String, dynamic> j) => switch (j['event']) {
    'prompt' => LoginPrompt(
      kind: j['kind'] as String,
      url: j['url'] as String,
      userCode: j['user_code'] as String?,
      browserOpened: j['browser_opened'] == true,
      browserNote: j['browser_note'] as String? ?? '',
    ),
    'signed_in' => LoginDone(ProviderStatus.fromJson(j)),
    _ => LoginFailed(j['error'] as String? ?? 'sign-in failed'),
  };
}

class LoginPrompt extends LoginEvent {
  const LoginPrompt({
    required this.kind,
    required this.url,
    this.userCode,
    this.browserOpened = false,
    this.browserNote = '',
  });

  /// 'browser' (open the URL) or 'device' (enter [userCode] at the URL).
  final String kind;
  final String url;
  final String? userCode;
  final bool browserOpened;
  final String browserNote;
}

class LoginDone extends LoginEvent {
  const LoginDone(this.status);
  final ProviderStatus status;
}

class LoginFailed extends LoginEvent {
  const LoginFailed(this.error);
  final String error;
}

/// Which session a model is chosen for.
enum ModelRole {
  worker,
  controller,
  deputy;

  String get label => name.toUpperCase();
}

/// What the harness config currently points each role at.
class ModelChoice {
  const ModelChoice({required this.provider, required this.model, this.effort});
  final String? provider;
  final String? model;

  /// reasoning_effort in the harness generation block, when set.
  final String? effort;
  static const none = ModelChoice(provider: null, model: null);

  @override
  bool operator ==(Object other) =>
      other is ModelChoice &&
      other.provider == provider &&
      other.model == model &&
      other.effort == effort;

  @override
  int get hashCode => Object.hash(provider, model, effort);
}
