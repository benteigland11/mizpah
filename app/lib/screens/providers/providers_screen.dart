import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/provider.dart';
import '../../state/provider_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/inline_text.dart';
import '../../widgets/ops.dart';

/// Providers as accounts: a list on the left, the selected one's sheet on
/// the right — sign in or out, and choose which model each role runs on.
class ProvidersScreen extends StatefulWidget {
  const ProvidersScreen({super.key, required this.manager});
  final ProviderManager manager;

  @override
  State<ProvidersScreen> createState() => _ProvidersScreenState();
}

class _ProvidersScreenState extends State<ProvidersScreen> {
  @override
  void initState() {
    super.initState();
    widget.manager.load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: widget.manager,
      builder: (context, _) => Row(
        children: [
          SizedBox(width: 320, child: _ProviderList(manager: widget.manager)),
          VerticalDivider(width: 1, color: cs.outlineVariant),
          Expanded(child: _ProviderSheet(manager: widget.manager)),
        ],
      ),
    );
  }
}

/// One row per provider; the badge says where its credential stands.
class _ProviderList extends StatelessWidget {
  const _ProviderList({required this.manager});
  final ProviderManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final local = manager.providers.where((p) => p.local);
    final subs = manager.providers.where(
      (p) => !p.local && p.authKind != 'api_key',
    );
    final keys = manager.providers.where((p) => p.authKind == 'api_key');
    return Material(
      color: cs.surfaceContainerLow,
      child: ListView(
        padding: const EdgeInsets.symmetric(vertical: Sp.s),
        children: [
          const _ListHeading('On this machine'),
          for (final p in local) _ProviderRow(status: p, manager: manager),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: Sp.s),
            child: OpsAddLine(
              label: 'add local server',
              onTap: () => _addLocal(context, manager),
            ),
          ),
          const _ListHeading('Subscriptions'),
          for (final p in subs) _ProviderRow(status: p, manager: manager),
          const _ListHeading('API keys'),
          for (final p in keys) _ProviderRow(status: p, manager: manager),
        ],
      ),
    );
  }
}

/// Removing a server drops its endpoints and any role pointed at it.
Future<void> _confirmRemove(
  BuildContext context,
  ProviderManager manager,
  ProviderStatus status,
) async {
  final used = manager.current.entries
      .where((e) => e.value.provider == status.name)
      .map((e) => e.key.label.toLowerCase())
      .join(' and ');
  final ok = await showOpsDialog<bool>(
    context,
    tag: 'REMOVE',
    title: 'Remove ${status.displayName}?',
    body: used.isEmpty
        ? 'Its address and endpoint settings go with it. The server itself '
              'is untouched.'
        : 'The $used ${used.contains(' and ') ? 'are' : 'is'} running on '
              'it; they keep the last harness config until you choose '
              'another model. Its endpoint settings go with it. The server '
              'itself is untouched.',
    actions: (ctx) => Row(
      mainAxisAlignment: MainAxisAlignment.end,
      children: [
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(false),
          child: const Text('KEEP'),
        ),
        const SizedBox(width: Sp.s),
        OutlinedButton(
          style: OutlinedButton.styleFrom(
            foregroundColor: AppTheme.removed(ctx),
            side: BorderSide(color: AppTheme.removed(ctx)),
          ),
          onPressed: () => Navigator.of(ctx).pop(true),
          child: const Text('REMOVE'),
        ),
      ],
    ),
  );
  if (ok == true) await manager.remove(status.name);
}

/// Name, address, kind — then the engine probes it and lists its models.
Future<void> _addLocal(BuildContext context, ProviderManager manager) async {
  final name = TextEditingController();
  final address = TextEditingController(text: 'http://127.0.0.1:');
  final ok = await showOpsDialog<bool>(
    context,
    tag: 'LOCAL SERVER',
    title: 'Add a server on this machine',
    body:
        'Any server that answers /v1/chat/completions without a key: '
        'llama.cpp, Ollama, LM Studio, vLLM. The engine asks the server '
        'which it is.',
    field: (ctx) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            controller: name,
            autofocus: true,
            style: AppTheme.mono,
            decoration: const InputDecoration(hintText: 'Name, e.g. Ollama'),
          ),
          const SizedBox(height: Sp.m),
          TextField(
            controller: address,
            style: AppTheme.mono,
            decoration: const InputDecoration(
              hintText: 'http://127.0.0.1:11434/v1',
            ),
            onSubmitted: (_) => Navigator.of(ctx).pop(true),
          ),
        ],
      ),
    actions: (ctx) => Row(
      mainAxisAlignment: MainAxisAlignment.end,
      children: [
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(false),
          child: const Text('CANCEL'),
        ),
        const SizedBox(width: Sp.s),
        FilledButton(
          onPressed: () => Navigator.of(ctx).pop(true),
          child: const Text('ADD'),
        ),
      ],
    ),
  );
  if (ok == true && name.text.trim().isNotEmpty) {
    await manager.addLocal(name.text, address.text);
  }
}

class _ListHeading extends StatelessWidget {
  const _ListHeading(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(Sp.l, Sp.m, Sp.l, Sp.xs),
    child: Text(text.toUpperCase(), style: opsLabelStyle(context)),
  );
}

class _ProviderRow extends StatelessWidget {
  const _ProviderRow({required this.status, required this.manager});
  final ProviderStatus status;
  final ProviderManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final selected = manager.selected == status.name;
    final dot = switch (status.badge) {
      'SIGNED IN' || 'ANSWERING' => AppTheme.added(context),
      'REAUTH' => cs.error,
      'BLOCKED' => cs.error,
      _ => cs.outline,
    };
    final used = manager.current.entries
        .where((e) => e.value.provider == status.name)
        .map((e) => e.key.label)
        .join(' · ');
    return InkWell(
      onTap: () => manager.select(status.name),
      child: Container(
        padding: const EdgeInsets.fromLTRB(Sp.m, Sp.m, Sp.l, Sp.m),
        decoration: BoxDecoration(
          color: selected ? cs.surfaceContainer : null,
          border: Border(
            left: BorderSide(
              color: selected ? cs.primary : Colors.transparent,
              width: 3,
            ),
          ),
        ),
        child: Row(
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
            ),
            const SizedBox(width: Sp.m),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    status.displayName,
                    overflow: TextOverflow.ellipsis,
                    style: AppTheme.mono.copyWith(
                      fontSize: 14,
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                      color: selected ? AppTheme.ink(context) : cs.onSurface,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    used.isEmpty ? status.badge : '${status.badge} · $used',
                    style: AppTheme.mono.copyWith(
                      fontSize: 12,
                      color: cs.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            if (status.custom)
              OpsRemove(
                tooltip: 'Remove',
                onPressed: () => _confirmRemove(context, manager, status),
              ),
          ],
        ),
      ),
    );
  }
}

/// The selected provider as a held sheet: its state, the sign-in control,
/// then the models with a USE FOR line under each.
class _ProviderSheet extends StatelessWidget {
  const _ProviderSheet({required this.manager});
  final ProviderManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final s = manager.selectedStatus;
    if (s == null) {
      return Center(
        child: Text(
          manager.error ?? 'No providers reported by the engine.',
          style: AppTheme.mono.copyWith(color: cs.onSurfaceVariant),
        ),
      );
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(Sp.xxl, Sp.xl, Sp.xxl, Sp.xxl),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 760),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _Header(status: s, manager: manager),
            const SizedBox(height: Sp.xl),
            if (s.local)
              OpsStrip(
                children: [
                  OpsStat('Sign-in', _mono(context, s.authBlurb)),
                  OpsStat(
                    'Answering',
                    _mono(context, s.reachable == true ? 'yes' : 'no'),
                  ),
                  OpsStat(
                    'Counting',
                    _mono(
                      context,
                      s.endpoint['local_kind'] == 'llama'
                          ? 'exact (/tokenize)'
                          : 'calibrated from usage',
                    ),
                  ),
                ],
              )
            else
              OpsStrip(
                children: [
                  OpsStat('Sign-in', _mono(context, s.authBlurb)),
                  OpsStat(
                    'Account',
                    _mono(
                      context,
                      (s.account['email'] ?? s.account['account_id'] ?? '—')
                          .toString(),
                    ),
                  ),
                  OpsStat(
                    'Context',
                    _mono(context, _tokens(manager.models?.contextWindow)),
                  ),
                ],
              ),
            if (s.local) ...[
              const SizedBox(height: Sp.xl),
              const OpsLabel('Endpoints'),
              const SizedBox(height: Sp.s),
              _EndpointTable(status: s, manager: manager),
            ] else if (s.settings.isNotEmpty) ...[
              const SizedBox(height: Sp.xl),
              const OpsLabel('Settings'),
              const SizedBox(height: Sp.s),
              _SettingsTable(status: s, manager: manager),
            ],
            if (s.notes.isNotEmpty) ...[
              const SizedBox(height: Sp.l),
              Text(
                s.notes,
                style: Theme.of(context).textTheme.bodyMedium!.copyWith(
                  color: cs.onSurfaceVariant,
                  height: 1.4,
                ),
              ),
            ],
            if (s.local && s.reachable != true && s.reason != null) ...[
              const SizedBox(height: Sp.l),
              _Note(text: 'Not answering: ${s.reason}', warn: true),
            ],
            if (s.blocked != null) ...[
              const SizedBox(height: Sp.l),
              _Note(text: s.blocked!, warn: true),
            ],
            if (s.quarantined) ...[
              const SizedBox(height: Sp.l),
              _Note(
                text:
                    'The stored credential stopped working: '
                    '${s.quarantineReason ?? 'unknown reason'}. Sign in again.',
                warn: true,
              ),
            ],
            if (manager.error != null) ...[
              const SizedBox(height: Sp.l),
              OpsError(manager.error!),
            ],
            if (manager.signingIn) ...[
              const SizedBox(height: Sp.xl),
              _LoginMemo(manager: manager, status: s),
            ],
            if (manager.checkReport != null) ...[
              const SizedBox(height: Sp.l),
              _CheckReport(report: manager.checkReport!),
            ],
            const SizedBox(height: Sp.xxl),
            OpsLabel(
              'Models',
              trailing: manager.models == null
                  ? null
                  : Text(
                      manager.models!.live
                          ? 'LISTED BY THE ACCOUNT'
                          : manager.models!.unverified
                          ? 'UNVERIFIED'
                          : '',
                      style: opsKeyStyle(context),
                    ),
            ),
            const SizedBox(height: Sp.m),
            if (manager.models == null)
              _mono(context, 'Reading the profile…')
            else if (manager.models!.signedOut)
              const _Note(
                text:
                    'Models are listed from the account. '
                    'Sign in to see what it can run.',
              )
            else if (manager.models!.unreachable)
              const _Note(
                text:
                    'Models are listed by the server. '
                    'Start it, or point the address at it.',
              )
            else ...[
              if (manager.models!.models.isEmpty) ...[
                _Note(
                  text: manager.models!.unverified
                      ? 'The account did not list its models '
                            '(${manager.models!.source.replaceFirst('list_failed: ', '')}). '
                            'Type the model id the provider documents.'
                      : 'This provider publishes no model list. '
                            'Type the model id it documents.',
                ),
                const SizedBox(height: Sp.m),
                _TypedModel(
                  manager: manager,
                  enabled: s.signedIn && !manager.signingIn,
                ),
              ],
              for (final m in manager.models!.models)
                _ModelRow(
                  model: m,
                  detail: manager.models!.details[m],
                  effort: manager.effortFor(m),
                  isDefault: m == manager.models!.defaultModel,
                  current: manager.current,
                  provider: s.name,
                  enabled: s.signedIn && !manager.signingIn,
                  onUse: (role) => manager.use(m, role),
                  onEffort: (e) => manager.pickEffort(m, e),
                ),
            ],
          ],
        ),
      ),
    );
  }

  static Widget _mono(BuildContext context, String text) => Text(
    text,
    style: AppTheme.mono.copyWith(
      fontSize: 14,
      color: Theme.of(context).colorScheme.onSurface,
    ),
  );

  static String _tokens(int? n) =>
      n == null ? '—' : '${(n / 1000).round()}K tokens';
}

/// The {fields} of a hosted provider's address — a project, a region, a
/// resource — as labelled lines. The engine builds the URL.
class _SettingsTable extends StatelessWidget {
  const _SettingsTable({required this.status, required this.manager});
  final ProviderStatus status;
  final ProviderManager manager;

  static const _labels = {
    'project': 'Project',
    'region': 'Region',
    'resource': 'Resource',
  };
  static const _hints = {
    'project': 'Google Cloud project id',
    'region': 'us-central1',
    'resource': 'the subdomain of your endpoint',
  };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final needs = status.needs.toSet();
    return Column(
      children: [
        for (final e in status.settings.entries)
          Container(
            padding: const EdgeInsets.symmetric(vertical: Sp.xs),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: cs.outlineVariant)),
            ),
            child: Row(
              children: [
                SizedBox(
                  width: 120,
                  child: Text(
                    (_labels[e.key] ?? e.key).toUpperCase(),
                    style: opsKeyStyle(context).copyWith(
                      color: needs.contains(e.key) ? cs.error : null,
                    ),
                  ),
                ),
                Expanded(
                  child: InlineText(
                    value: e.value,
                    resetKey: '${e.key}:${e.value}',
                    compact: true,
                    style: AppTheme.mono.copyWith(fontSize: 14),
                    placeholder: _hints[e.key] ?? e.key,
                    onChanged: (v) => manager.setSetting(e.key, v),
                  ),
                ),
              ],
            ),
          ),
        Padding(
          padding: const EdgeInsets.only(top: Sp.s),
          child: Row(
            children: [
              const SizedBox(width: 120),
              Expanded(
                child: Text(
                  status.baseUrl ?? '',
                  style: AppTheme.mono.copyWith(
                    fontSize: 12,
                    color: cs.onSurfaceVariant,
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Every route the engine will call on a local server, each click-to-edit.
/// Nothing here assumes llama.cpp or Ollama: the paths are whatever the
/// server answers on.
class _EndpointTable extends StatelessWidget {
  const _EndpointTable({required this.status, required this.manager});
  final ProviderStatus status;
  final ProviderManager manager;

  @override
  Widget build(BuildContext context) {
    final e = status.endpoint;
    final exact = e['local_kind'] == 'llama';
    final rows = <(String, String, String, String)>[
      ('Address', 'api_base_url', '${e['api_base_url'] ?? ''}', 'http://127.0.0.1:8080'),
      ('Completions', 'completion_path', '${e['completion_path'] ?? ''}', '/v1/chat/completions'),
      ('Model list', 'models_path', '${e['models_path'] ?? ''}', '/v1/models'),
      if (exact) ...[
        ('Tokenize', 'tokenize_path', '${e['tokenize_path'] ?? ''}', '/tokenize'),
        ('Template', 'template_path', '${e['template_path'] ?? ''}', '/apply-template'),
      ],
      ('Wire', 'wire', '${e['wire'] ?? ''}', 'chat_completions | responses'),
      ('Context', 'context_window', e['context_window'] == null ? '' : '${e['context_window']}', 'tokens, e.g. 32768'),
      ('Timeout', 'timeout_seconds', e['timeout_seconds'] == null ? '' : '${e['timeout_seconds']}', 'seconds'),
      ('Headers', 'static_headers', (e['static_headers'] as Map?)?.isEmpty ?? true ? '' : _json(e['static_headers']), '{"X-Header": "value"}'),
    ];
    return Column(
      children: [
        for (final (label, key, value, hint) in rows)
          Container(
            padding: const EdgeInsets.symmetric(vertical: Sp.xs),
            decoration: BoxDecoration(
              border: Border(
                bottom: BorderSide(
                  color: Theme.of(context).colorScheme.outlineVariant,
                ),
              ),
            ),
            child: Row(
              children: [
                SizedBox(
                  width: 120,
                  child: Text(label.toUpperCase(), style: opsKeyStyle(context)),
                ),
                Expanded(
                  child: InlineText(
                    value: value,
                    resetKey: '$key:$value',
                    compact: true,
                    style: AppTheme.mono.copyWith(fontSize: 14),
                    placeholder: hint,
                    onChanged: (v) => manager.setField(key, v),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  static String _json(Object? v) => v is Map
      ? '{${v.entries.map((e) => '"${e.key}": "${e.value}"').join(', ')}}'
      : '$v';
}

/// A model id typed by the person, for a provider that lists none. The
/// same USE FOR dial as a listed row; the id is whatever they typed.
class _TypedModel extends StatefulWidget {
  const _TypedModel({required this.manager, required this.enabled});
  final ProviderManager manager;
  final bool enabled;

  @override
  State<_TypedModel> createState() => _TypedModelState();
}

class _TypedModelState extends State<_TypedModel> {
  late final TextEditingController _id = TextEditingController(
    text: widget.manager.current.values
            .where((c) => c.provider == widget.manager.selected)
            .map((c) => c.model)
            .firstOrNull ??
        '',
  );

  @override
  void dispose() {
    _id.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final provider = widget.manager.selected;
    bool on(ModelRole r) =>
        widget.manager.current[r]?.provider == provider &&
        widget.manager.current[r]?.model == _id.text.trim();
    return Container(
      padding: const EdgeInsets.symmetric(vertical: Sp.m),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _id,
              enabled: widget.enabled,
              style: AppTheme.mono.copyWith(
                fontSize: 15,
                color: AppTheme.ink(context),
              ),
              decoration: const InputDecoration(
                hintText: 'model id',
                isDense: true,
              ),
              onChanged: (_) => setState(() {}),
            ),
          ),
          const SizedBox(width: Sp.l),
          Text('USE FOR', style: opsKeyStyle(context)),
          const SizedBox(width: Sp.m),
          _RoleDial(
            on: {for (final r in ModelRole.values) r: on(r)},
            enabled: widget.enabled && _id.text.trim().isNotEmpty,
            onTap: (role) => widget.manager.use(_id.text.trim(), role),
          ),
        ],
      ),
    );
  }
}

/// Title on the left, the sign-in / sign-out control on the right.
class _Header extends StatelessWidget {
  const _Header({required this.status, required this.manager});
  final ProviderStatus status;
  final ProviderManager manager;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                status.name,
                style: opsLabelStyle(context).copyWith(color: cs.primary),
              ),
              const SizedBox(height: Sp.s),
              Text(
                status.displayName,
                style: theme.textTheme.headlineSmall!.copyWith(
                  fontWeight: FontWeight.w700,
                  color: AppTheme.ink(context),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(width: Sp.l),
        OpsChip(
          value: status.badge,
          options: const [],
          hot: const {'SIGNED IN', 'ANSWERING'},
          onSelected: (_) {},
        ),
        const SizedBox(width: Sp.l),
        if (status.local)
          OutlinedButton(
            onPressed: manager.load,
            child: const Text('RECHECK'),
          )
        else if (manager.signingIn)
          OutlinedButton(
            onPressed: manager.cancelLogin,
            child: const Text('CANCEL'),
          )
        else if (status.signedIn) ...[
          OutlinedButton(
            onPressed: manager.checking ? null : manager.check,
            child: Text(manager.checking ? 'CHECKING…' : 'CHECK'),
          ),
          const SizedBox(width: Sp.s),
          OutlinedButton(
            onPressed: manager.logout,
            child: const Text('SIGN OUT'),
          ),
        ]
        else
          FilledButton(
            onPressed: status.blocked != null
                ? null
                : () => status.authKind == 'api_key'
                      ? _askKey(context)
                      : manager.login(),
            child: const Text('SIGN IN'),
          ),
      ],
    );
  }

  Future<void> _askKey(BuildContext context) async {
    final controller = TextEditingController();
    final key = await showOpsDialog<String>(
      context,
      tag: status.name,
      title: 'API key',
      body:
          'Stored owner-only in the engine\'s credential file; '
          'never shown again.',
      field: (ctx) => TextField(
        controller: controller,
        autofocus: true,
        obscureText: true,
        style: AppTheme.mono,
        decoration: const InputDecoration(hintText: 'sk-…'),
        onSubmitted: (v) => Navigator.of(ctx).pop(v),
      ),
      actions: (ctx) => Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('CANCEL'),
          ),
          const SizedBox(width: Sp.s),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(controller.text),
            child: const Text('SAVE'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (key != null && key.trim().isNotEmpty) {
      manager.login(apiKey: key.trim());
    }
  }
}

/// The sign-in in progress, as a memo: the code to type or the URL to
/// open, with copy. The engine already opened the browser if it could.
class _LoginMemo extends StatelessWidget {
  const _LoginMemo({required this.manager, required this.status});
  final ProviderManager manager;
  final ProviderStatus status;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final p = manager.prompt;
    return OpsSheet(
      padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.l),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'SIGN-IN IN PROGRESS',
            style: opsLabelStyle(context).copyWith(color: cs.primary),
          ),
          const SizedBox(height: Sp.m),
          if (p == null)
            Text(
              'Asking ${status.displayName} for a sign-in…',
              style: theme.textTheme.bodyLarge!.copyWith(
                color: cs.onSurfaceVariant,
              ),
            )
          else ...[
            if (p.userCode != null) ...[
              Text(
                'Enter this code at the address below.',
                style: theme.textTheme.bodyLarge!.copyWith(
                  color: cs.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: Sp.m),
              _Copyable(
                text: p.userCode!,
                style: AppTheme.mono.copyWith(
                  fontSize: 32,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 4,
                  color: AppTheme.ink(context),
                ),
              ),
              const SizedBox(height: Sp.m),
            ] else
              Text(
                p.browserOpened
                    ? 'Finish in the browser window that just opened.'
                    : 'Open this address in a browser and finish there.',
                style: theme.textTheme.bodyLarge!.copyWith(
                  color: cs.onSurfaceVariant,
                ),
              ),
            const SizedBox(height: Sp.s),
            _Copyable(
              text: p.url,
              style: AppTheme.mono.copyWith(
                fontSize: 13,
                color: cs.onSurface,
              ),
            ),
            if (!p.browserOpened && p.browserNote.isNotEmpty) ...[
              const SizedBox(height: Sp.s),
              Text(
                p.browserNote,
                style: AppTheme.mono.copyWith(
                  fontSize: 12,
                  color: cs.onSurfaceVariant,
                ),
              ),
            ],
          ],
        ],
      ),
    );
  }
}

/// Text with a copy affordance; the whole line is the hit area.
class _Copyable extends StatefulWidget {
  const _Copyable({required this.text, required this.style});
  final String text;
  final TextStyle style;

  @override
  State<_Copyable> createState() => _CopyableState();
}

class _CopyableState extends State<_Copyable> {
  bool _copied = false;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: () async {
        await Clipboard.setData(ClipboardData(text: widget.text));
        if (!mounted) return;
        setState(() => _copied = true);
        Future<void>.delayed(const Duration(seconds: 2), () {
          if (mounted) setState(() => _copied = false);
        });
      },
      child: Row(
        children: [
          Flexible(child: SelectableText(widget.text, style: widget.style)),
          const SizedBox(width: Sp.m),
          Text(
            _copied ? 'COPIED' : 'COPY',
            style: opsLabelStyle(context).copyWith(
              color: _copied ? cs.primary : cs.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }
}

/// One model: its id, an effort chip when it takes one, then USE FOR
/// worker / controller / both. The role already on this model reads as
/// the accent.
class _ModelRow extends StatelessWidget {
  const _ModelRow({
    required this.model,
    required this.detail,
    required this.effort,
    required this.isDefault,
    required this.current,
    required this.provider,
    required this.enabled,
    required this.onUse,
    required this.onEffort,
  });
  final String model;
  final ModelDetail? detail;
  final String? effort;
  final bool isDefault;
  final Map<ModelRole, ModelChoice> current;
  final String provider;
  final bool enabled;
  final ValueChanged<ModelRole?> onUse;
  final ValueChanged<String> onEffort;

  bool _on(ModelRole r) =>
      current[r]?.provider == provider && current[r]?.model == model;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final efforts = detail?.efforts ?? const [];
    final lit = ModelRole.values.any(_on);
    return Container(
      padding: const EdgeInsets.symmetric(vertical: Sp.m),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Row(
              children: [
                Text(
                  model,
                  style: AppTheme.mono.copyWith(
                    fontSize: 15,
                    fontWeight: lit ? FontWeight.w700 : FontWeight.w500,
                    color: AppTheme.ink(context),
                  ),
                ),
                if (isDefault) ...[
                  const SizedBox(width: Sp.m),
                  Text('DEFAULT', style: opsKeyStyle(context)),
                ],
                if (detail?.contextWindow != null) ...[
                  const SizedBox(width: Sp.m),
                  Text(
                    '${(detail!.contextWindow! / 1000).round()}K',
                    style: opsKeyStyle(context),
                  ),
                ],
              ],
            ),
          ),
          if (efforts.isNotEmpty) ...[
            Text('EFFORT', style: opsKeyStyle(context)),
            const SizedBox(width: Sp.s),
            OpsChip(
              value: (effort ?? detail?.defaultEffort ?? efforts.first)
                  .toUpperCase(),
              options: [for (final e in efforts) e],
              hot: {if (lit) (effort ?? '').toUpperCase()},
              onSelected: onEffort,
            ),
            const SizedBox(width: Sp.l),
          ],
          Text('USE FOR', style: opsKeyStyle(context)),
          const SizedBox(width: Sp.m),
          _RoleDial(
            on: {for (final r in ModelRole.values) r: _on(r)},
            enabled: enabled,
            onTap: onUse,
          ),
        ],
      ),
    );
  }
}

/// WORKER · CONTROLLER · DEPUTY · ALL as joined square buttons, like the mode dial.
class _RoleDial extends StatelessWidget {
  const _RoleDial({
    required this.on,
    required this.enabled,
    required this.onTap,
  });
  final Map<ModelRole, bool> on;
  final bool enabled;
  final ValueChanged<ModelRole?> onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final cells = <(String, ModelRole?, bool)>[
      for (final r in ModelRole.values) (r.label, r, on[r]!),
      ('ALL', null, on.values.every((v) => v)),
    ];
    return Opacity(
      opacity: enabled ? 1 : 0.45,
      child: Container(
        decoration: BoxDecoration(
          border: Border.all(color: cs.outline),
          borderRadius: BorderRadius.circular(AppTheme.radius),
        ),
        clipBehavior: Clip.antiAlias,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (final (i, (label, role, lit)) in cells.indexed)
              InkWell(
                onTap: enabled ? () => onTap(role) : null,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: Sp.m,
                    vertical: Sp.xs + 2,
                  ),
                  decoration: BoxDecoration(
                    color: lit ? cs.primary : null,
                    border: Border(
                      left: i == 0
                          ? BorderSide.none
                          : BorderSide(color: cs.outline),
                    ),
                  ),
                  child: Text(
                    label,
                    style: AppTheme.mono.copyWith(
                      fontSize: 12,
                      letterSpacing: 1.2,
                      fontWeight: FontWeight.w600,
                      color: lit ? cs.onPrimary : cs.onSurface,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// What the engine found when it listed models and ran one completion:
/// one line per step, green when the shape matched, amber with the
/// provider's own words when it did not.
class _CheckReport extends StatelessWidget {
  const _CheckReport({required this.report});
  final Map<String, dynamic> report;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final steps = (report['steps'] as List?)?.cast<Map>() ?? const [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final step in steps)
          Padding(
            padding: const EdgeInsets.only(bottom: Sp.xs),
            child: _Note(
              warn: step['ok'] != true,
              text: switch (step['step']) {
                'models' => step['ok'] == true
                    ? 'Models: the account listed ${step['count']}.'
                    : 'Models: ${step['source'] ?? step['detail']}.',
                'completion' => step['ok'] == true
                    ? 'Completion on ${step['model']}: "${step['content']}" '
                          'in ${step['seconds']} s, ${(step['usage'] as Map?)?['prompt_tokens'] ?? '?'} '
                          'prompt tokens.'
                    : 'Completion on ${step['model'] ?? '?'}: '
                          'HTTP ${step['status'] ?? '—'} · ${step['detail'] ?? ''} '
                          '${step['body'] ?? ''}',
                _ => '${step['step']}: ${step['detail'] ?? ''}',
              },
            ),
          ),
        Text(
          report['ok'] == true
              ? 'Shape matches. Ready to run.'
              : 'Something is off; the provider\'s own message is above.',
          style: AppTheme.mono.copyWith(
            fontSize: 12,
            color: report['ok'] == true ? AppTheme.added(context) : cs.error,
          ),
        ),
      ],
    );
  }
}

/// Amber for anything that needs the person; never the red accent.
class _Note extends StatelessWidget {
  const _Note({required this.text, this.warn = false});
  final String text;
  final bool warn;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final color = warn ? cs.error : cs.onSurfaceVariant;
    return Container(
      padding: const EdgeInsets.fromLTRB(Sp.m, Sp.s, Sp.m, Sp.s),
      decoration: BoxDecoration(
        border: Border(left: BorderSide(color: color, width: 3)),
      ),
      child: Text(
        text,
        style: AppTheme.mono.copyWith(fontSize: 13, color: color),
      ),
    );
  }
}
