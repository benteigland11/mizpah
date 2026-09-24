import 'package:flutter/material.dart';

import '../../cg/segmented_capacity_bar/segmented_capacity_bar.dart';
import '../../models/brief.dart';
import '../../models/route.dart';
import '../../state/app_nav.dart';
import '../../state/app_settings.dart';
import '../../state/brief_manager.dart';
import '../../engine/state_dir.dart';
import '../../widgets/signature.dart';
import '../../state/route_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../models/provider.dart';
import '../../state/provider_manager.dart';
import '../../widgets/crew.dart';
import '../../widgets/inline_text.dart';
import '../../widgets/ops.dart';
import 'phase_track.dart';

/// The brief, laid out as a held document. Click any line to edit it.
class BriefDocument extends StatelessWidget {
  const BriefDocument({super.key, required this.manager, this.route, this.nav, this.settings, this.providers});
  final AppSettings? settings;
  final BriefManager manager;

  /// For the crew at the foot: the models the run will use, chosen here
  /// while the brief is a draft. Without it the crew is read-only.
  final ProviderManager? providers;
  final RouteManager? route;
  final AppNav? nav;

  /// Open the route board scoped to a phase or enabler.
  void _openRoute({String? phase, String? enabler}) {
    if (route == null || nav == null) return;
    if (phase != null) route!.scopeToPhase(phase);
    if (enabler != null) route!.scopeToEnabler(enabler);
    nav!.go(AppNav.route);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final b = manager.draft;
    if (b == null) return const Center(child: Text('Select a brief'));
    final gen = manager.generation;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (manager.error != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: OpsError(manager.error!),
          ),
        Expanded(
          child: Align(
            alignment: Alignment.topCenter,
            child: ConstrainedBox(
              constraints: const BoxConstraints(
                maxWidth: AppTheme.contentWidth,
              ),
              child: ListView(
                padding: const EdgeInsets.fromLTRB(26, 20, 32, 48),
                children: [
                  _TitleLine(manager: manager, brief: b),
                  const SizedBox(height: Sp.m),
                  _StatusStrip(manager: manager, brief: b),
                  PhaseTrack(
                    b.phases,
                    manager,
                    onOpenRoute: (id) => _openRoute(phase: id),
                  ),
                  // Below the track the document runs as rows of pairs, in
                  // reading order (left to right, then down), tops level.
                  _Pair(
                    left: [
                      const OpsLabel('Mission'),
                      InlineText(
                        value: b.mission,
                        resetKey: gen,
                        placeholder:
                            'What this task is for, in a sentence or two.',
                        style: theme.textTheme.titleLarge!.copyWith(
                          fontWeight: FontWeight.w400,
                          height: 1.4,
                        ),
                        onChanged: (v) => manager.edit((b) => b.mission = v),
                      ),
                    ],
                    right: [
                      _EntrySection('Needs', b.needs, manager, noun: 'need'),
                    ],
                  ),
                  _Pair(
                    left: [
                      _EntrySection(
                        'Deliverables',
                        b.deliverables,
                        manager,
                        noun: 'deliverable',
                      ),
                    ],
                    right: [
                      _EntrySection(
                        'Non-goals',
                        b.nonGoals,
                        manager,
                        noun: 'non-goal',
                      ),
                    ],
                  ),
                  _Pair(
                    left: [
                      _EnablerSection(
                        b.enablers,
                        manager,
                        onOpenRoute: (id) => _openRoute(enabler: id),
                      ),
                    ],
                    right: [
                      const OpsLabel('Budget notes'),
                      InlineText(
                        value: b.budgetNotes,
                        resetKey: gen,
                        placeholder: 'Horizon, team, depth — the prose behind the number.',
                        style: theme.textTheme.bodyLarge,
                        onChanged: (v) =>
                            manager.edit((b) => b.budgetNotes = v),
                      ),
                    ],
                  ),
                  // The environment: where it runs — a saved gym environment
                  // the engine binds into every task. Beside the crew: who
                  // runs it and where. Chosen on the draft; the brief carries it.
                  _EnvironmentSection(
                    key: ValueKey('environment/${manager.selectedId}/${b.status}'),
                    manager: manager,
                    environment: b.environment,
                    issued: b.status != 'draft',
                  ),
                  // The crew: who will run it. Chosen on the draft; on issue
                  // it is locked in on the brief with the signature.
                  _CrewSection(
                    key: ValueKey('crew/${manager.selectedId}/${b.status}'),
                    providers: providers,
                    projectPath: manager.selectedPath,
                    issued: b.status != 'draft',
                    issuedCrew: (b.rest['issued_crew'] as Map?)?.cast<String, dynamic>(),
                  ),
                  // Signed and never run: it cannot be signed again, so the
                  // one thing to do is start it (or discard it from its row).
                  if (b.status != 'draft' &&
                      manager.briefs.where((x) => x.id == manager.selectedId).firstOrNull?.state == 'idle')
                    _StartLine(manager: manager),
                  // A draft ends in a signature line. Signing issues it: the
                  // loop starts, and the sidebar carries the state from there.
                  if (b.status == 'draft' && settings != null)
                    _IssueLine(manager: manager, settings: settings!)
                  // Issued: the signature it was issued under, as stored on it.
                  else if ((b.rest['issued_by'] as String? ?? '').isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 40),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Divider(color: Theme.of(context).colorScheme.outline, thickness: 1.5),
                          const SizedBox(height: 20),
                          SignatureBlock.stored(
                            label: 'ISSUED BY',
                            signedBy: b.rest['issued_by'] as String,
                            image: (b.rest['issued_signature'] as String? ?? '').isEmpty
                                ? null
                                : '${stateDir(manager.selectedPath)}/${b.rest['issued_signature']}',
                            signedAt: DateTime.tryParse(b.rest['issued_at'] as String? ?? ''),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// H1, editable in place; the unsaved marker and SAVE sit on its line.
class _TitleLine extends StatelessWidget {
  const _TitleLine({required this.manager, required this.brief});
  final BriefManager manager;
  final Brief brief;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Row(
      children: [
        Expanded(
          child: InlineText(
            value: brief.title,
            resetKey: manager.generation,
            placeholder: 'Untitled task',
            style: theme.textTheme.headlineLarge!.copyWith(
              fontWeight: FontWeight.w700,
            ),
            onChanged: (v) => manager.edit((b) => b.title = v),
          ),
        ),
        const SizedBox(width: 16),
        if (manager.dirty)
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Text(
              '● UNSAVED',
              style: opsLabelStyle(context).copyWith(color: cs.primary),
            ),
          ),
        FilledButton(
          onPressed: manager.dirty && !manager.saving ? manager.save : null,
          child: const Text('SAVE'),
        ),
      ],
    );
  }
}

/// VERSION · PROPOSALS, with the budget anchored right.
class _StatusStrip extends StatelessWidget {
  const _StatusStrip({required this.manager, required this.brief});
  final BriefManager manager;
  final Brief brief;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return OpsStrip(
      // Where this brief lives: the project root the engine works in.
      header: Text(
        manager.selectedPath,
        style: AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurfaceVariant),
      ),
      trailing: _BudgetBlock(manager: manager, brief: brief),
      children: [
        OpsStat('Version', Text('v${brief.version}')),
        OpsStat(
          'Proposals',
          brief.openProposals > 0
              ? Row(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${brief.openProposals} OPEN'),
                    const SizedBox(width: 3),
                    Icon(Icons.north_east, size: 12, color: cs.primary),
                  ],
                )
              : Text('NONE', style: TextStyle(color: cs.onSurfaceVariant)),
          accent: brief.openProposals > 0,
          // The draft stays live behind the memo, so no leave prompt.
          onTap: brief.openProposals > 0 ? manager.openQueue : null,
        ),
      ],
    );
  }
}

/// The dial people tune most, and the first read on how things are going:
/// budget (editable) against points done and reserved on the route.
class _BudgetBlock extends StatelessWidget {
  const _BudgetBlock({required this.manager, required this.brief});
  final BriefManager manager;
  final Brief brief;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final r = manager.route.budget;
    final budget = brief.budgetPoints;
    final segments = [
      CapacitySegment(r.pointsDone, cs.primary),
      CapacitySegment(r.pointsReserved, cs.onSurfaceVariant),
    ];
    final readout = CapacityReadout.of(segments, budget);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.center,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text('POINTS BUDGETED', style: opsKeyStyle(context)),
        // Row gives no width bound; size the field to its content.
        IntrinsicWidth(
          child: ConstrainedBox(
            constraints: const BoxConstraints(minWidth: 72),
            child: InlineText(
              value: budget?.toString() ?? '',
              resetKey: manager.generation,
              compact: true,
              textAlign: TextAlign.center,
              placeholder: '—',
              style: AppTheme.mono.copyWith(
                fontSize: 34,
                fontWeight: FontWeight.w700,
                height: 1.1,
              ),
              onChanged: (v) =>
                  manager.edit((b) => b.budgetPoints = int.tryParse(v.trim())),
            ),
          ),
        ),
        const SizedBox(height: 6),
        SizedBox(
          width: 260,
          child: SegmentedCapacityBar(
            capacity: budget,
            outlineColor: cs.outline,
            overrunColor: cs.error,
            segments: segments,
          ),
        ),
        const SizedBox(height: 6),
        DefaultTextStyle(
          style: AppTheme.mono.copyWith(
            fontSize: 12,
            letterSpacing: 1,
            color: cs.onSurfaceVariant,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('${r.pointsDone} DONE'),
              const Text('  ·  '),
              Text('${r.pointsReserved} RESERVED'),
              const Text('  ·  '),
              if (readout.capacity == null)
                const Text('NO BUDGET SET')
              else if (readout.over)
                Text(
                  'OVER BY ${readout.overBy}',
                  style: TextStyle(
                    color: cs.error,
                    fontWeight: FontWeight.w700,
                  ),
                )
              else
                Text(
                  '${readout.remaining} AVAILABLE',
                  style: TextStyle(color: AppTheme.ink(context)),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

/// A plain string list: needs, deliverables, non-goals.
class _EntrySection extends StatelessWidget {
  const _EntrySection(
    this.title,
    this.entries,
    this.manager, {
    required this.noun,
  });
  final String title;
  final List<Entry> entries;
  final BriefManager manager;
  final String noun;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OpsLabel(title),
        for (final (i, e) in entries.indexed)
          _Row(
            key: ValueKey(e.key),
            index: i,
            onRemove: () => manager.edit((_) => entries.remove(e)),
            child: InlineText(
              value: e.value,
              resetKey: manager.generation,
              autofocus: manager.focusKey == e.key,
              placeholder: 'Describe the $noun',
              style: theme.textTheme.bodyLarge,
              onChanged: (v) => manager.edit((_) => e.value = v),
            ),
          ),
        OpsAddLine(
          label: 'Add $noun',
          onTap: () {
            final e = Entry('');
            manager.add(e.key, (_) => entries.add(e));
          },
        ),
      ],
    );
  }
}

/// Enablers: title and status. The id is slugged from the title; path and
/// widget are engine-reported and stay out of the document.
class _EnablerSection extends StatelessWidget {
  const _EnablerSection(this.enablers, this.manager, {this.onOpenRoute});
  final List<Enabler> enablers;
  final BriefManager manager;
  final ValueChanged<String>? onOpenRoute;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final ready = enablers
        .where((e) => e.status == 'ready' || e.status == 'graduated')
        .length;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OpsLabel(
          'Enablers',
          trailing: enablers.isEmpty
              ? null
              : Text(
                  '$ready / ${enablers.length} READY',
                  style: opsLabelStyle(context),
                ),
        ),
        for (final (i, en) in enablers.indexed)
          _Row(
            key: ValueKey(en.key),
            index: i,
            onRemove: () => manager.edit((_) => enablers.remove(en)),
            child: Row(
              children: [
                Expanded(
                  child: InlineText(
                    value: en.title,
                    resetKey: manager.generation,
                    autofocus: manager.focusKey == en.key,
                    placeholder: 'What this enabler is',
                    style: theme.textTheme.bodyLarge,
                    onChanged: (v) => manager.edit((_) => en.title = v),
                  ),
                ),
                _TaskCount(
                  tasks: manager.route.tasks.where((t) => t.enablerId == en.id),
                  onTap: () => onOpenRoute?.call(en.id),
                ),
                const SizedBox(width: 8),
                OpsChip(
                  value: en.status,
                  options: Enabler.statuses,
                  hot: const {'ready', 'graduated'},
                  onSelected: (v) => manager.edit((_) => en.status = v),
                ),
              ],
            ),
          ),
        OpsAddLine(
          label: 'Add enabler',
          onTap: () {
            final en = Enabler(id: '', title: '', status: 'needed');
            manager.add(en.key, (_) => enablers.add(en));
          },
        ),
      ],
    );
  }
}

/// Index in a gutter on the left, content, × in a mirroring gutter on the
/// right — both centred on the first line of text, so single- and
/// multi-line rows read the same.
class _Row extends StatelessWidget {
  const _Row({
    super.key,
    required this.index,
    required this.child,
    required this.onRemove,
  });
  final int index;
  final Widget child;
  final VoidCallback onRemove;

  /// Height of one text line inside an InlineText (16px body + padding).
  static const _firstLine = 36.0;
  static const _gutter = 40.0;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      decoration: BoxDecoration(
        border: Border(
          bottom: BorderSide(color: cs.outlineVariant.withValues(alpha: 0.5)),
        ),
      ),
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: _gutter,
            height: _firstLine,
            child: Padding(
              padding: const EdgeInsets.only(left: 6),
              child: Align(
                alignment: Alignment.centerLeft,
                child: OpsIndex(index),
              ),
            ),
          ),
          Expanded(child: child),
          SizedBox(
            width: _gutter,
            height: _firstLine,
            child: Center(child: OpsRemove(onPressed: onRemove)),
          ),
        ],
      ),
    );
  }
}

/// Two sections side by side, tops level, with a wide gutter.
class _Pair extends StatelessWidget {
  const _Pair({required this.left, required this.right});
  final List<Widget> left;
  final List<Widget> right;

  @override
  Widget build(BuildContext context) => Row(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: left,
        ),
      ),
      const SizedBox(width: 64),
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: right,
        ),
      ),
    ],
  );
}

/// `n TASKS · m LEFT ↗` — how the route is serving a brief entry. Click
/// to open the board scoped to it. Nothing when no tasks point here.
class _TaskCount extends StatelessWidget {
  const _TaskCount({required this.tasks, required this.onTap});
  final Iterable<RouteTask> tasks;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final all = tasks.toList();
    if (all.isEmpty) return const SizedBox.shrink();
    final left = all.where((t) => t.open).length;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: Sp.s, vertical: Sp.xs),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              '${all.length} TASKS · $left LEFT',
              style: opsKeyStyle(context).copyWith(
                color: left > 0 ? cs.onSurface : AppTheme.added(context),
              ),
            ),
            const SizedBox(width: 3),
            Icon(Icons.north_east, size: 11, color: cs.primary),
          ],
        ),
      ),
    );
  }
}


/// The environment the run happens in: one row, the saved environment's
/// name and what it provides. On a draft CHANGE lists the saved
/// environments with the default marked and lets any be made the default;
/// once issued the row is what the brief was signed with. Every gym has
/// one: `bare` (Python and a shell) exists on every machine and is the
/// default until another is chosen — three gyms once went out with no
/// environment at all and their workers spent their first turns hunting
/// for pip.
class _EnvironmentSection extends StatefulWidget {
  const _EnvironmentSection({super.key, required this.manager, required this.environment, required this.issued});
  final BriefManager manager;
  final String environment;
  final bool issued;

  @override
  State<_EnvironmentSection> createState() => _EnvironmentSectionState();
}

class _EnvironmentSectionState extends State<_EnvironmentSection> {
  List<Map<String, String>> saved = const [];
  bool loaded = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    List<Map<String, String>> found = const [];
    try {
      found = await widget.manager.environments();
    } catch (_) {}
    if (mounted) {
      setState(() {
        saved = found;
        loaded = true;
      });
    }
  }

  String _note(String name) => saved.firstWhere((e) => e['name'] == name, orElse: () => const {})['note'] ?? '';

  Future<void> _change() async {
    final cs = Theme.of(context).colorScheme;
    final picked = await showDialog<String>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.7),
      builder: (ctx) => SimpleDialog(
        backgroundColor: cs.surface,
        shape: RoundedRectangleBorder(side: BorderSide(color: cs.outline), borderRadius: BorderRadius.zero),
        title: Text('ENVIRONMENT', style: opsKeyStyle(ctx)),
        children: [
          // The default is what a gym gets when its brief names none; any
          // row can be made the default from here (`mizpah.bases default`).
          for (final e in saved)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(ctx, e['name']),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Text(e['name'] ?? '', style: AppTheme.mono.copyWith(fontSize: 13, color: AppTheme.ink(ctx))),
                            if (e['default'] == 'true') ...[
                              const SizedBox(width: Sp.m),
                              Text('DEFAULT', style: opsKeyStyle(ctx).copyWith(color: cs.primary)),
                            ],
                          ],
                        ),
                        const SizedBox(height: 2),
                        Text(e['note'] ?? '', style: Theme.of(ctx).textTheme.bodySmall!.copyWith(color: cs.onSurfaceVariant)),
                      ],
                    ),
                  ),
                  if (e['default'] != 'true')
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, '\u0000default:${e['name']}'),
                      child: Text('MAKE DEFAULT', style: opsKeyStyle(ctx)),
                    ),
                ],
              ),
            ),
          if (saved.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 8, 24, 8),
              child: Text('No saved environments yet; the engine makes `bare` on first use.', style: opsKeyStyle(ctx)),
            ),
        ],
      ),
    );
    if (picked == null) return;
    if (picked.startsWith('\u0000default:')) {
      await widget.manager.setDefaultEnvironment(picked.substring('\u0000default:'.length));
      await _load();
      return;
    }
    widget.manager.edit((b) => b.environment = picked);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    final name = widget.environment;
    final note = _note(name);
    final missing = loaded && name.isNotEmpty && note.isEmpty && saved.every((e) => e['name'] != name);
    return Padding(
      padding: const EdgeInsets.only(top: 40),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Divider(color: cs.outline, thickness: 1.5),
          const SizedBox(height: 20),
          Text(widget.issued ? 'ENVIRONMENT · AS SIGNED' : 'ENVIRONMENT', style: opsKeyStyle(context)),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.symmetric(vertical: Sp.m),
            decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outlineVariant))),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(width: 130, child: Text('Runs in', style: opsLabelStyle(context))),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        name.isEmpty ? 'the default environment' : name,
                        style: AppTheme.mono.copyWith(fontSize: 13, color: name.isEmpty ? cs.onSurfaceVariant : ink),
                      ),
                      if (note.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text(note, style: Theme.of(context).textTheme.bodySmall!.copyWith(color: cs.onSurfaceVariant)),
                        ),
                      if (missing)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text('No saved environment by this name; signing will be refused.',
                              style: Theme.of(context).textTheme.bodySmall!.copyWith(color: cs.error)),
                        ),
                    ],
                  ),
                ),
                if (!widget.issued) TextButton(onPressed: _change, child: Text('CHANGE', style: opsLabelStyle(context))),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The crew at the foot of the brief: worker and controller, each with
/// the model it will run on. On a draft each seat shows the task's own
/// choice or your default (YOUR DEFAULT) and CHANGE picks for this task
/// only. Once issued, the seats are what the brief was signed on
/// (`issued_crew`), and nothing here changes them.
class _CrewSection extends StatefulWidget {
  const _CrewSection({super.key, required this.providers, required this.projectPath, required this.issued, this.issuedCrew});
  final ProviderManager? providers;
  final String projectPath;
  final bool issued;
  final Map<String, dynamic>? issuedCrew;

  @override
  State<_CrewSection> createState() => _CrewSectionState();
}

class _CrewSectionState extends State<_CrewSection> {
  Map<ModelRole, ModelChoice> task = const {};

  @override
  void initState() {
    super.initState();
    final pm = widget.providers;
    if (pm != null && !widget.issued) {
      if (pm.providers.isEmpty) pm.load();
      _readTask();
    }
  }

  Future<void> _readTask() async {
    final pm = widget.providers;
    if (pm == null || widget.projectPath.isEmpty) return;
    final t = await pm.taskChoices(widget.projectPath);
    if (mounted) setState(() => task = t);
  }

  Future<void> _change(ModelRole role) async {
    final pm = widget.providers;
    if (pm == null) return;
    await showDialog<void>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.7),
      builder: (_) => ModelPicker(providers: pm, role: role, projectPath: widget.projectPath, current: task[role]),
    );
    await _readTask();
  }

  static ModelChoice _stored(Map<String, dynamic>? c) => c == null
      ? ModelChoice.none
      : ModelChoice(provider: c['provider'] as String?, model: c['model'] as String?, effort: c['effort'] as String?);

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final pm = widget.providers;
    const roles = [ModelRole.worker, ModelRole.controller];
    Widget rows() => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Divider(color: cs.outline, thickness: 1.5),
            const SizedBox(height: 20),
            Text(widget.issued ? 'CREW · AS SIGNED' : 'CREW', style: opsKeyStyle(context)),
            const SizedBox(height: 8),
            if (widget.issued)
              for (final role in roles)
                CrewRoleRow(role: role, choice: _stored(widget.issuedCrew?[role.name] as Map<String, dynamic>?))
            else
              for (final role in roles)
                CrewRoleRow(
                  role: role,
                  choice: task[role] ?? pm?.current[role] ?? ModelChoice.none,
                  isDefault: task[role] == null,
                  onChange: pm == null ? null : () => _change(role),
                  effort: pm == null
                      ? null
                      : EffortChip(
                          providers: pm,
                          role: role,
                          choice: task[role] ?? pm.current[role] ?? ModelChoice.none,
                          projectPath: widget.projectPath,
                          onSet: _readTask,
                        ),
                ),
            if (widget.issued && widget.issuedCrew == null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text('Issued before the crew was kept on the brief.', style: opsKeyStyle(context)),
              ),
            if (!widget.issued && pm?.error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: OpsError(pm!.error!),
              ),
          ],
        );
    return Padding(
      padding: const EdgeInsets.only(top: 40),
      child: pm == null || widget.issued ? rows() : ListenableBuilder(listenable: pm, builder: (context, _) => rows()),
    );
  }
}


/// The signature line at the foot of a draft brief. Your mark goes on the
/// line when you sign; signing issues the brief and the task leaves DRAFT.
class _StartLine extends StatefulWidget {
  const _StartLine({required this.manager});
  final BriefManager manager;

  @override
  State<_StartLine> createState() => _StartLineState();
}

class _StartLineState extends State<_StartLine> {
  bool starting = false;
  String? error;

  Future<void> _start() async {
    setState(() {
      starting = true;
      error = null;
    });
    try {
      await widget.manager.start();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => starting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 40),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Divider(color: Theme.of(context).colorScheme.outline, thickness: 1.5),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: Text('Signed, not started: nothing has run on this brief yet.',
                    style: Theme.of(context).textTheme.bodyMedium),
              ),
              FilledButton.icon(
                onPressed: starting ? null : _start,
                icon: starting
                    ? const SizedBox.square(dimension: 13, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.play_arrow, size: 15),
                label: Text(starting ? 'STARTING' : 'START'),
              ),
            ],
          ),
          if (error != null) ...[
            const SizedBox(height: 8),
            Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
        ],
      ),
    );
  }
}

class _IssueLine extends StatefulWidget {
  const _IssueLine({required this.manager, required this.settings});
  final BriefManager manager;
  final AppSettings settings;

  @override
  State<_IssueLine> createState() => _IssueLineState();
}

class _IssueLineState extends State<_IssueLine> {
  bool signing = false;
  String? error;

  Future<void> _discard() async {
    final b = widget.manager.draft;
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('DISCARD ${(b?.title ?? 'THIS DRAFT').toUpperCase()}', style: opsHeadingStyle(ctx)),
        content: const Text('The draft and everything in its folder go for good. Nothing was issued, so nothing else is touched.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('DISCARD')),
        ],
      ),
    );
    if (yes != true) return;
    try {
      await widget.manager.discard();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> _sign() async {
    setState(() {
      signing = true;
      error = null;
    });
    try {
      await widget.manager.issue();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => signing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final s = widget.settings;
    final m = widget.manager;
    final b = m.draft!;
    final blocked = m.dirty
        ? 'Save the brief first.'
        : b.mission.trim().isEmpty
        ? 'Write the mission first.'
        : !s.canSign
        ? 'Set a name or title under Settings › Signature first.'
        : null;
    return Padding(
      padding: const EdgeInsets.fromLTRB(0, 40, 0, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Divider(color: cs.outline, thickness: 1.5),
          const SizedBox(height: 20),
          Text('ISSUED BY', style: opsKeyStyle(context)),
          const SizedBox(height: 8),
          // The line itself: empty until signed.
          Container(
            width: 300,
            height: 56,
            alignment: Alignment.bottomLeft,
            decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outline))),
            child: signing
                ? Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: SignatureMark(strokes: s.signature, name: s.markName, image: s.signatureImage),
                  )
                : null,
          ),
          const SizedBox(height: 6),
          Text(
            s.canSign ? [for (final p in [s.signerParts.$1, s.signerParts.$2]) if (p.isNotEmpty) p].join(' · ') : 'Name · Title',
            style: opsKeyStyle(context),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              FilledButton(
                onPressed: blocked != null || signing ? null : _sign,
                child: Text(signing ? 'ISSUING…' : 'SIGN AND ISSUE'),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Text(
                  error ?? blocked ?? 'The loop starts on signature. Nothing runs until then.',
                  style: opsKeyStyle(context).copyWith(color: error != null ? cs.error : null),
                ),
              ),
              // The other way out of a draft: nothing was ever issued, so
              // nothing is kept.
              TextButton(
                onPressed: signing ? null : _discard,
                child: Text('DISCARD DRAFT', style: opsLabelStyle(context).copyWith(color: AppTheme.removed(context))),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
