import 'dart:math';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../../state/app_settings.dart';
import '../../engine/desk_share.dart';
import '../../state/procedures_manager.dart';
import '../../state/storage_manager.dart';
import '../../widgets/document_sheet.dart' show whenLabel;
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/ops.dart';
import '../../widgets/signature.dart';

/// The user's settings, in the shape everyone knows: sections down the
/// left, the chosen section's controls in the middle, × top right. Takes
/// the whole window — none of this belongs to a task.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.settings,
    required this.storage,
    required this.onClose,
    required this.onOpenProviders,
    required this.procedures,
    this.share,
    this.closeApp,
    this.openAt = '',
    this.openRequest = 0,
  });
  final AppSettings settings;

  /// The section to open at, when a link asked for one; [openRequest]
  /// changes with each ask so a repeat of the same section still lands.
  final String openAt;
  final int openRequest;
  final StorageManager storage;

  /// Experimental: use it or lose it lives on the playbook's ledger, which
  /// the procedures manager reads and writes.
  final ProceduresManager procedures;
  final VoidCallback onClose;
  final VoidCallback onOpenProviders;

  /// Sharing this desk on the network; null when this is the browser end.
  final DeskShare? share;

  /// The app's own close control, since the window has no frame.
  final Widget? closeApp;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  String section = 'appearance';

  static const _sections = [
    ('appearance', Icons.contrast_outlined, 'Appearance'),
    ('signature', Icons.draw_outlined, 'Signature'),
    ('tasks', Icons.folder_outlined, 'Tasks'),
    ('notifications', Icons.notifications_outlined, 'Notifications'),
    ('storage', Icons.storage_outlined, 'Storage'),
    ('sharing', Icons.devices_outlined, 'Sharing'),
    ('experimental', Icons.science_outlined, 'Experimental'),
    ('about', Icons.info_outlined, 'About'),
  ];

  @override
  void initState() {
    super.initState();
    _openAsked();
  }

  @override
  void didUpdateWidget(SettingsScreen old) {
    super.didUpdateWidget(old);
    if (old.openRequest != widget.openRequest) _openAsked();
  }

  void _openAsked() {
    if (widget.openAt.isNotEmpty && _sections.any((s) => s.$1 == widget.openAt)) _pick(widget.openAt);
  }

  void _pick(String key) {
    setState(() => section = key);
    if (key == 'storage' && widget.storage.report == null) widget.storage.measure();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: Listenable.merge([widget.settings, widget.storage]),
      builder: (context, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            height: 48,
            // Right inset matches the tab strip so the close button never moves.
            padding: const EdgeInsets.fromLTRB(Sp.xl, 0, Sp.l, 0),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: cs.outlineVariant)),
            ),
            child: Row(
              children: [
                InkWell(
                  onTap: widget.onClose,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: Sp.s, vertical: Sp.m),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.arrow_back, size: 16, color: cs.onSurfaceVariant),
                        const SizedBox(width: Sp.s),
                        Text('BACK', style: opsLabelStyle(context)),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: Sp.l),
                Text('SETTINGS', style: opsLabelStyle(context).copyWith(color: AppTheme.ink(context))),
                const Spacer(),
                ?widget.closeApp,
              ],
            ),
          ),
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Container(
                  width: 240,
                  color: cs.surfaceContainerLow,
                  child: ListView(
                    padding: const EdgeInsets.symmetric(vertical: Sp.m),
                    children: [
                      for (final (key, icon, label) in _sections)
                        _SectionTile(
                          icon: icon,
                          label: label,
                          selected: section == key,
                          onTap: () => _pick(key),
                        ),
                    ],
                  ),
                ),
                VerticalDivider(width: 1, color: cs.outlineVariant),
                Expanded(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(48, 36, 48, 48),
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 720),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: switch (section) {
                          'tasks' => _tasks(context),
                          'signature' => _signature(context),
                          'notifications' => _notifications(context),
                          'storage' => _storage(context),
                          'experimental' => _experimental(context),
                          'sharing' => _sharing(context),
                          'about' => _about(context),
                          _ => _appearance(context),
                        },
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _title(BuildContext context, String t, [String? sub]) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: Sp.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(t, style: theme.textTheme.headlineSmall!.copyWith(
            fontWeight: FontWeight.w700, color: AppTheme.ink(context))),
          if (sub != null) ...[
            const SizedBox(height: 6),
            Text(sub, style: theme.textTheme.bodyMedium!.copyWith(color: cs.onSurfaceVariant)),
          ],
        ],
      ),
    );
  }

  Widget _muted(BuildContext context, String t) => Text(
    t,
    style: Theme.of(context).textTheme.bodyMedium!.copyWith(
      color: Theme.of(context).colorScheme.onSurfaceVariant,
      height: 1.5,
    ),
  );

  List<Widget> _appearance(BuildContext context) => [
    _title(context, 'Appearance'),
    _Row(
      label: 'Task list',
      hint: 'How the sidebar opens at launch. You can still fold or unfold it any time.',
      child: SegmentedButton<bool>(
        showSelectedIcon: false,
        segments: const [
          ButtonSegment(value: false, label: Text('Open')),
          ButtonSegment(value: true, label: Text('Collapsed')),
        ],
        selected: {widget.settings.sidebarCollapsed},
        onSelectionChanged: (s) => widget.settings.setSidebarCollapsed(s.first),
      ),
    ),
    _Row(
      label: 'Theme',
      child: SegmentedButton<ThemeMode>(
        showSelectedIcon: false,
        segments: const [
          ButtonSegment(value: ThemeMode.light, label: Text('Light')),
          ButtonSegment(value: ThemeMode.dark, label: Text('Dark')),
          ButtonSegment(value: ThemeMode.system, label: Text('System')),
        ],
        selected: {widget.settings.mode},
        onSelectionChanged: (s) => widget.settings.setMode(s.first),
      ),
    ),
  ];

  List<Widget> _signature(BuildContext context) {
    final s = widget.settings;
    return [
      _title(context, 'Signature', 'What goes on a document when you sign it: issuing a brief, deciding a change request.'),
      _Row(
        label: 'Name',
        child: _PathField(value: s.signerName, onCommit: (v) => s.setSigner(name: v.trim())),
      ),
      _Row(
        label: 'Title',
        child: _PathField(value: s.signerTitle, onCommit: (v) => s.setSigner(title: v.trim())),
      ),
      const SizedBox(height: Sp.l),
      Text('MARK', style: opsKeyStyle(context)),
      const SizedBox(height: Sp.s),
      // Draw one, or bring the one you already have. Either way the mark is
      // filed with each document you sign, so changing it later changes
      // nothing already signed.
      if (s.hasSignatureImage)
        Container(
          height: 160,
          alignment: Alignment.centerLeft,
          padding: const EdgeInsets.symmetric(horizontal: 24),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.surfaceContainerLow,
            border: Border.all(color: Theme.of(context).colorScheme.outline),
            borderRadius: BorderRadius.circular(AppTheme.radius),
          ),
          child: SignatureMark(strokes: const [], name: s.markName, image: s.signatureImage, height: 100),
        )
      else
        SignaturePad(strokes: s.signature, onChanged: s.setSignature),
      const SizedBox(height: 6),
      Row(
        children: [
          Text(
            s.hasSignatureImage ? 'Your uploaded mark.' : 'Or upload an image of your signature: PNG or JPEG, dark ink on white or transparent.',
            style: opsKeyStyle(context),
          ),
          const Spacer(),
          if (s.hasSignatureImage)
            TextButton(onPressed: s.clearSignatureImage, child: Text('REMOVE', style: opsLabelStyle(context))),
          TextButton(
            onPressed: () async {
              final picked = await openFile(
                acceptedTypeGroups: const [XTypeGroup(label: 'Images', extensions: ['png', 'jpg', 'jpeg'])],
                confirmButtonText: 'Use',
              );
              if (picked != null) await s.setSignatureImage(await picked.readAsBytes(), picked.name);
            },
            child: Text(s.hasSignatureImage ? 'REPLACE' : 'UPLOAD', style: opsLabelStyle(context)),
          ),
        ],
      ),
      const SizedBox(height: Sp.xl),
      Text('AS IT WILL APPEAR', style: opsKeyStyle(context)),
      const SizedBox(height: Sp.m),
      if (s.canSign)
        SignatureBlock(strokes: s.signature, name: s.signerParts.$1, title: s.signerParts.$2, image: s.signatureImage, signedAt: DateTime.now())
      else
        Text('Enter a name or title to see it.', style: Theme.of(context).textTheme.bodyMedium!.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant)),
      const SizedBox(height: Sp.s),
      Text(
        s.hasSignatureImage
            ? 'Your uploaded mark.'
            : s.signature.isEmpty
                ? 'No mark yet: your name is set in a hand instead.'
                : 'Your drawn mark.',
        style: opsKeyStyle(context),
      ),
    ];
  }

  /// This desk, on other screens: the app serves itself and the engine to
  /// any browser that can reach this machine. Tailscale is the fence.
  List<Widget> _sharing(BuildContext context) {
    final s = widget.settings;
    final share = widget.share;
    final cs = Theme.of(context).colorScheme;
    if (share == null) {
      return [
        _title(context, 'Sharing', 'You are looking at a shared desk. Sharing is turned on and off from the desk itself.'),
      ];
    }
    return [
      _title(
        context,
        'Sharing',
        'Open this desk in a browser on another device: an iPad on your tailnet, another machine. '
            'Everything is served by this app while it runs.',
      ),
      ListenableBuilder(
        listenable: share,
        builder: (context, _) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _Row(
              label: 'Share this desk',
              hint: 'Serves the engine and the web app on the port below. Only devices that can reach this machine can connect.',
              child: Align(
                alignment: Alignment.centerLeft,
                child: SegmentedButton<bool>(
                  showSelectedIcon: false,
                  segments: const [
                    ButtonSegment(value: false, label: Text('Off')),
                    ButtonSegment(value: true, label: Text('On')),
                  ],
                  selected: {s.shareEnabled},
                  onSelectionChanged: (v) {
                    final on = v.first;
                    // A token is minted the first time, so the desk is never open to whoever guesses the port.
                    s.setShare(enabled: on, token: on && s.shareToken.isEmpty ? _mintToken() : null);
                  },
                ),
              ),
            ),
            _Row(
              label: 'Port',
              child: SizedBox(
                width: 120,
                child: _PathField(
                  value: '${s.sharePort}',
                  onCommit: (v) {
                    final n = int.tryParse(v.trim());
                    if (n != null && n > 0 && n < 65536) s.setShare(port: n);
                  },
                ),
              ),
            ),
            _Row(
              label: 'Token',
              hint: 'Part of the address; anyone with it is you. Mint a new one to lock old links out.',
              child: Row(
                children: [
                  Expanded(
                    child: SelectableText(
                      s.shareToken.isEmpty ? '(minted when sharing is turned on)' : s.shareToken,
                      style: AppTheme.mono.copyWith(fontSize: 13),
                    ),
                  ),
                  TextButton(onPressed: () => s.setShare(token: _mintToken()), child: Text('NEW TOKEN', style: opsLabelStyle(context))),
                ],
              ),
            ),
            _Row(
              label: 'Address',
              hint: share.running ? 'Type one of these on the other device. The 100.x address is Tailscale.' : 'Turn sharing on to get an address.',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (share.error != null)
                    OpsError(share.error!)
                  else if (!share.running)
                    Text('Not sharing.', style: opsKeyStyle(context))
                  else ...[
                    for (final u in share.urls)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 4),
                        child: SelectableText(u, style: AppTheme.mono.copyWith(fontSize: 13)),
                      ),
                    if (!share.hasWebApp)
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Text(
                          'The engine is up but the web app is not built: run flutter build web in app/ and the page is served from build/web.',
                          style: opsKeyStyle(context).copyWith(color: cs.error),
                        ),
                      ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    ];
  }

  static String _mintToken() {
    const chars = 'abcdefghjkmnpqrstuvwxyz23456789';
    final r = Random.secure();
    return List.generate(20, (_) => chars[r.nextInt(chars.length)]).join();
  }

  /// Features still earning their place. Each one says what it does and
  /// what it costs; off by default.
  List<Widget> _experimental(BuildContext context) {
    final m = widget.procedures;
    return [
      _title(
        context,
        'Experimental',
        'Features under trial. They are off until you turn them on, and each says what it will do to your work.',
      ),
      ListenableBuilder(
        listenable: m,
        builder: (context, _) {
          final p = m.policy;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _Row(
                label: 'Use it or lose it',
                hint: 'Procedures nobody touches decay: hidden from workers until you revive them on the Procedures page. '
                    'Pinned and core procedures never decay.',
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: SegmentedButton<bool>(
                    showSelectedIcon: false,
                    segments: const [
                      ButtonSegment(value: false, label: Text('Off')),
                      ButtonSegment(value: true, label: Text('On')),
                    ],
                    selected: {p.enabled},
                    onSelectionChanged: (s) => m.setPolicy(enabled: s.first),
                  ),
                ),
              ),
              _Row(
                label: 'Grace',
                hint: 'Green gates a procedure survives after it last turned up in a search. '
                    'Being used lasts ${p.weights['use'] ?? 2}× as long; being edited ${p.weights['edit'] ?? 3}×. '
                    'Keep it generous: the library serves many unrelated tasks, and a procedure for one kind of work '
                    'is only touched when that kind of work comes round.',
                child: Row(
                  children: [
                    SizedBox(
                      width: 96,
                      child: _PathField(
                        value: '${p.grace}',
                        onCommit: (v) {
                          final n = int.tryParse(v.trim());
                          if (n != null && n > 0 && n != p.grace) m.setPolicy(grace: n);
                        },
                      ),
                    ),
                    const SizedBox(width: Sp.m),
                    Text('gates', style: opsKeyStyle(context)),
                    const SizedBox(width: Sp.xl),
                    Text(
                      'Search ${p.grace} · use ${p.grace * (p.weights['use'] ?? 2)} · edit ${p.grace * (p.weights['edit'] ?? 3)}',
                      style: opsKeyStyle(context),
                    ),
                  ],
                ),
              ),
              _Row(
                label: 'Clock',
                child: Text(
                  '${p.clock} green gate${p.clock == 1 ? '' : 's'} counted · '
                  '${m.retiredCount} decayed · ${m.all.where((x) => x.standing.pinned).length} pinned',
                  style: opsKeyStyle(context),
                ),
              ),
              if (m.error != null) ...[
                const SizedBox(height: Sp.s),
                OpsError(m.error!),
              ],
            ],
          );
        },
      ),
    ];
  }

  List<Widget> _tasks(BuildContext context) => [
    _title(context, 'Tasks'),
    _Row(
      label: 'Tasks folder',
      hint: 'Where your tasks live. Applies at once.',
      child: _PathField(value: widget.settings.runsRoot, onCommit: widget.settings.setRunsRoot),
    ),
    _Row(
      label: 'Model defaults',
      hint: 'Which model plays controller and worker on a new task.',
      child: Align(
        alignment: Alignment.centerLeft,
        child: OutlinedButton(
          onPressed: widget.onOpenProviders,
          child: const Text('SET IN PROVIDERS'),
        ),
      ),
    ),
  ];

  List<Widget> _notifications(BuildContext context) => [
    _title(context, 'Notifications',
        'What reaches you while the app is in the background.'),
    _muted(context,
        'Coming: a change request for signature, a run that stopped or stalled, '
        'a model outage, a task completed — each one on or off.'),
  ];

  List<Widget> _storage(BuildContext context) {
    final st = widget.storage;
    final r = st.report;
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    String h(int b) => StorageManager.human(b);
    return [
      _title(context, 'Storage',
          'Worker transcripts are the raw session logs. They are large and only '
          'needed to replay a work order; briefs, maps, runs and paperwork are kept.'),
      if (r == null || st.measuring)
        _muted(context, 'Measuring the tasks folder…')
      else ...[
        OpsStrip(
          children: [
            OpsStat('IN USE', _big(context, h(r.total)), accent: true),
            OpsStat('KEPT', _big(context, h(r.kept))),
            OpsStat('TRANSCRIPTS', _big(context, h(r.transcripts))),
            OpsStat('CLEARABLE', _big(context, h(r.clearable))),
          ],
        ),
        const SizedBox(height: Sp.xl),
        _Row(
          label: 'Worker transcripts',
          hint: r.liveTranscripts > 0
              ? '${r.transcriptFiles} files; ${h(r.liveTranscripts)} under live runs stay.'
              : '${r.transcriptFiles} files across finished runs.',
          child: Row(
            children: [
              FilledButton(
                onPressed: st.clearing || r.clearable == 0
                    ? null
                    : () => _confirmClear(context, h(r.clearable)),
                child: Text(st.clearing ? 'CLEARING…' : 'CLEAR ${h(r.clearable)}'),
              ),
              const SizedBox(width: Sp.l),
              TextButton(
                onPressed: st.measuring ? null : st.measure,
                child: Text('MEASURE AGAIN', style: opsLabelStyle(context)),
              ),
            ],
          ),
        ),
        if (st.lastAction != null) ...[
          const SizedBox(height: Sp.l),
          Text(st.lastAction!, style: theme.textTheme.bodyMedium!.copyWith(color: AppTheme.added(context))),
        ],
        const SizedBox(height: Sp.l),
        Text('Measured ${whenLabel(r.measuredAt)}', style: opsKeyStyle(context).copyWith(color: cs.onSurfaceVariant)),
      ],
    ];
  }

  Widget _big(BuildContext context, String t) => Text(
    t,
    style: AppTheme.mono.copyWith(
      fontSize: 18,
      fontWeight: FontWeight.w700,
      color: Theme.of(context).colorScheme.onSurface,
    ),
  );

  Future<void> _confirmClear(BuildContext context, String amount) async {
    final ok = await showOpsDialog<bool>(
      context,
      tag: 'STORAGE',
      title: 'Clear $amount of worker transcripts?',
      body: 'Session logs under finished runs are deleted. Briefs, maps, runs, '
          'results and paperwork stay. Live runs are not touched.',
      actions: (ctx) => Row(
        children: [
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('CLEAR')),
          const SizedBox(width: Sp.m),
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
        ],
      ),
    );
    if (ok == true) await widget.storage.clearTranscripts();
  }

  List<Widget> _about(BuildContext context) => [
    _title(context, 'About'),
    const _Row(label: 'Mizpah', child: Text(_appVersion, style: AppTheme.mono)),
    _Row(
      label: 'Settings',
      child: Text(widget.settings.store.describe, style: AppTheme.mono.copyWith(fontSize: 13)),
    ),
  ];
}

const _appVersion = '1.0.0';

/// A section in the left list: icon, label, ink bar when selected.
class _SectionTile extends StatelessWidget {
  const _SectionTile({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    return InkWell(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: Sp.l, vertical: Sp.m),
        decoration: BoxDecoration(
          color: selected ? cs.surfaceContainer : null,
          border: Border(
            left: BorderSide(color: selected ? ink : Colors.transparent, width: 3),
          ),
        ),
        child: Row(
          children: [
            Icon(icon, size: 18, color: selected ? ink : cs.onSurfaceVariant),
            const SizedBox(width: Sp.m),
            Text(
              label,
              style: theme.textTheme.titleMedium!.copyWith(
                color: selected ? ink : cs.onSurface,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// One setting: label and hint on the left, control on the right, a rule
/// beneath. Controls line up because the label column is fixed.
class _Row extends StatelessWidget {
  const _Row({required this.label, required this.child, this.hint});
  final String label;
  final Widget child;
  final String? hint;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(vertical: Sp.l),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          SizedBox(
            width: 180,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: theme.textTheme.titleMedium!.copyWith(color: AppTheme.ink(context))),
                if (hint != null) ...[
                  const SizedBox(height: 3),
                  Text(hint!, style: theme.textTheme.bodySmall!.copyWith(color: cs.onSurfaceVariant)),
                ],
              ],
            ),
          ),
          const SizedBox(width: Sp.xl),
          Expanded(child: Align(alignment: Alignment.centerLeft, child: child)),
        ],
      ),
    );
  }
}

/// A mono text field that commits on Enter or blur and reverts on Esc.
class _PathField extends StatefulWidget {
  const _PathField({required this.value, required this.onCommit});
  final String value;
  final ValueChanged<String> onCommit;

  @override
  State<_PathField> createState() => _PathFieldState();
}

class _PathFieldState extends State<_PathField> {
  late final _c = TextEditingController(text: widget.value);
  final _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _focus.addListener(() {
      if (!_focus.hasFocus && _c.text != widget.value) widget.onCommit(_c.text);
    });
  }

  @override
  void didUpdateWidget(covariant _PathField old) {
    super.didUpdateWidget(old);
    if (old.value != widget.value && !_focus.hasFocus) _c.text = widget.value;
  }

  @override
  void dispose() {
    _c.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final field = TextField(
      controller: _c,
      focusNode: _focus,
      style: AppTheme.mono.copyWith(fontSize: 13, color: AppTheme.ink(context)),
      onSubmitted: widget.onCommit,
      onEditingComplete: () => _focus.unfocus(),
      decoration: InputDecoration(
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppTheme.radius),
          borderSide: BorderSide(color: cs.outlineVariant),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppTheme.radius),
          borderSide: BorderSide(color: cs.outline),
        ),
      ),
    );
    return field;
  }
}
