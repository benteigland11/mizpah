import 'package:flutter/material.dart';

import '../../cg/segmented_capacity_bar/segmented_capacity_bar.dart';
import '../../models/route.dart';
import '../../state/app_nav.dart';
import '../../state/loop_manager.dart';
import '../../state/route_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/ops.dart';

/// The route as a board: one column per declared phase, tasks as cards,
/// pickable first then by priority. A selected task opens on the right
/// with its record, evidence and history, and a link into its session.
class RouteScreen extends StatelessWidget {
  const RouteScreen({
    super.key,
    required this.manager,
    required this.loop,
    required this.nav,
  });
  final RouteManager manager;
  final LoopManager loop;
  final AppNav nav;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _TopBar(manager: manager),
          Divider(height: 1, color: cs.outlineVariant),
          if (manager.status.attention.isNotEmpty)
            _AttentionBanner(items: manager.status.attention, manager: manager),
          Expanded(
            child: Row(
              children: [
                Expanded(child: _Board(manager: manager)),
                if (manager.selected != null) ...[
                  VerticalDivider(width: 1, color: cs.outlineVariant),
                  SizedBox(
                    width: 420,
                    child: _TaskDetail(
                      task: manager.selected!,
                      manager: manager,
                      onOpenSession: () {
                        loop.select(manager.selected!.id);
                        nav.go(AppNav.agents);
                      },
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Counts and the budget on one line, with the scope chip when set.
class _TopBar extends StatelessWidget {
  const _TopBar({required this.manager});
  final RouteManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final s = manager.status;
    final b = s.budget;
    final mono = AppTheme.mono.copyWith(
      fontSize: 13,
      color: cs.onSurfaceVariant,
    );
    int n(String st) => s.tasks.where((t) => t.status == st).length;
    final readout = CapacityReadout.of([
      CapacitySegment(b.pointsDone, cs.primary),
      CapacitySegment(b.pointsReserved, cs.onSurfaceVariant),
    ], b.budgetPoints);
    return Padding(
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.m, Sp.l, Sp.m),
      child: Row(
        children: [
          Text('${n('done')} DONE', style: mono.copyWith(color: cs.onSurface)),
          const SizedBox(width: Sp.l),
          Text(
            '${s.tasks.where((t) => t.pickable).length} PICKABLE',
            style: mono,
          ),
          const SizedBox(width: Sp.l),
          Text('${n('in_progress')} IN PROGRESS', style: mono),
          const SizedBox(width: Sp.l),
          Text(
            '${n('blocked')} BLOCKED',
            style: mono.copyWith(color: n('blocked') > 0 ? cs.error : null),
          ),
          if (manager.phase != null || manager.enabler != null) ...[
            const SizedBox(width: Sp.xl),
            InputChip(
              label: Text(
                manager.phase != null
                    ? 'PHASE ${manager.phase}'
                    : 'ENABLER ${manager.enabler}',
                style: opsLabelStyle(context).copyWith(color: cs.primary),
              ),
              onDeleted: () => manager.scopeToPhase(null),
              deleteIcon: const Icon(Icons.close, size: 14),
              side: BorderSide(color: cs.primary),
              backgroundColor: Colors.transparent,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(AppTheme.radius),
              ),
            ),
          ],
          const Spacer(),
          SizedBox(
            width: 220,
            child: SegmentedCapacityBar(
              capacity: b.budgetPoints,
              outlineColor: cs.outline,
              overrunColor: cs.error,
              segments: [
                CapacitySegment(b.pointsDone, cs.primary),
                CapacitySegment(b.pointsReserved, cs.onSurfaceVariant),
              ],
            ),
          ),
          const SizedBox(width: Sp.m),
          Text(
            b.budgetPoints == null
                ? '${b.pointsActual} PTS · NO BUDGET'
                : readout.over
                ? 'OVER BY ${readout.overBy}'
                : '${b.pointsDone} / ${b.pointsActual} / ${b.budgetPoints} PTS',
            style: mono.copyWith(color: readout.over ? cs.error : cs.onSurface),
          ),
        ],
      ),
    );
  }
}

/// Things the route wants a person to look at, in Terra's words.
class _AttentionBanner extends StatelessWidget {
  const _AttentionBanner({required this.items, required this.manager});
  final List<Attention> items;
  final RouteManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.s, Sp.l, Sp.s),
      decoration: BoxDecoration(
        color: cs.surfaceContainer,
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Wrap(
        spacing: Sp.xl,
        runSpacing: Sp.xs,
        children: [
          for (final a in items)
            InkWell(
              onTap: () => manager.select(a.id),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  OpsChip(
                    value: a.severity,
                    options: const [],
                    hot: const {'block', 'high'},
                    onSelected: (_) {},
                  ),
                  const SizedBox(width: Sp.s),
                  Text(
                    a.id,
                    style: AppTheme.mono.copyWith(
                      fontSize: 13,
                      color: AppTheme.ink(context),
                    ),
                  ),
                  const SizedBox(width: Sp.s),
                  Text(
                    a.why,
                    style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Columns per declared phase; unphased tasks get a trailing column.
class _Board extends StatelessWidget {
  const _Board({required this.manager});
  final RouteManager manager;

  static const columnWidth = 340.0;

  @override
  Widget build(BuildContext context) {
    final s = manager.status;
    final tasks = manager.scoped;
    final phases = manager.phase == null
        ? s.phases
        : s.phases.where((p) => p.id == manager.phase).toList();
    final unphased = tasks
        .where((t) => !s.phases.any((p) => p.id == t.phase))
        .toList();
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.l, Sp.l, Sp.xl),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final p in phases)
            _Column(
              row: p,
              current: p.id == s.currentPhase,
              tasks: _ordered(tasks.where((t) => t.phase == p.id)),
              manager: manager,
            ),
          if (unphased.isNotEmpty && manager.phase == null)
            _Column(
              row: null,
              current: false,
              tasks: _ordered(unphased),
              manager: manager,
            ),
        ],
      ),
    );
  }

  /// Pickable first, then in progress, blocked, other open, done, cancelled;
  /// priority within each.
  static List<RouteTask> _ordered(Iterable<RouteTask> ts) {
    int rank(RouteTask t) => t.pickable
        ? 0
        : switch (t.status) {
            'in_progress' => 1,
            'blocked' => 2,
            'ready' => 3,
            'done' => 4,
            _ => 5,
          };
    final list = ts.toList()
      ..sort((a, b) {
        final r = rank(a).compareTo(rank(b));
        return r != 0 ? r : a.priority.compareTo(b.priority);
      });
    return list;
  }
}

class _Column extends StatelessWidget {
  const _Column({
    required this.row,
    required this.current,
    required this.tasks,
    required this.manager,
  });
  final PhaseRow? row;
  final bool current;
  final List<RouteTask> tasks;
  final RouteManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final barColor = row == null
        ? cs.outlineVariant
        : row!.closed
        ? cs.onSurface
        : current
        ? cs.primary
        : cs.outlineVariant;
    final left = row?.open;
    return Container(
      width: _Board.columnWidth,
      margin: const EdgeInsets.only(right: Sp.l),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(height: 5, color: barColor),
          const SizedBox(height: Sp.s),
          Row(
            children: [
              Text(
                (row?.title ?? 'Unphased').toUpperCase(),
                style: opsHeadingStyle(context).copyWith(
                  fontSize: 15,
                  color: current ? cs.primary : cs.onSurfaceVariant,
                ),
              ),
              const Spacer(),
              if (row != null)
                Text(
                  row!.exitReady
                      ? 'EXIT READY'
                      : '${row!.done} DONE · $left LEFT',
                  style: opsKeyStyle(context).copyWith(
                    color: row!.exitReady ? AppTheme.added(context) : null,
                  ),
                ),
            ],
          ),
          const SizedBox(height: Sp.m),
          if (tasks.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: Sp.m),
              child: Text(
                row == null ? '' : 'No tasks — unplanned, not complete.',
                style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
              ),
            ),
          for (final t in tasks)
            _Card(
              task: t,
              selected: manager.selectedTask == t.id,
              onTap: () =>
                  manager.select(manager.selectedTask == t.id ? null : t.id),
            ),
        ],
      ),
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({
    required this.task,
    required this.selected,
    required this.onTap,
  });
  final RouteTask task;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final t = task;
    final closed = !t.open;
    final edge = switch (t.status) {
      'in_progress' => cs.primary,
      'blocked' => cs.error,
      'done' => AppTheme.added(context),
      'cancelled' => cs.outlineVariant,
      _ => t.pickable ? cs.onSurface : cs.outlineVariant,
    };
    final mono = AppTheme.mono.copyWith(
      fontSize: 12,
      color: cs.onSurfaceVariant,
    );
    return InkWell(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: Sp.s),
        padding: const EdgeInsets.fromLTRB(Sp.m, Sp.s, Sp.m, Sp.s),
        decoration: BoxDecoration(
          color: selected ? cs.surfaceContainerHigh : cs.surfaceContainer,
          border: Border(
            left: BorderSide(color: edge, width: 3),
            top: BorderSide(color: selected ? cs.outline : cs.outlineVariant),
            right: BorderSide(color: selected ? cs.outline : cs.outlineVariant),
            bottom: BorderSide(
              color: selected ? cs.outline : cs.outlineVariant,
            ),
          ),
        ),
        child: Opacity(
          opacity: closed ? 0.6 : 1,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      t.id,
                      overflow: TextOverflow.ellipsis,
                      style: AppTheme.mono.copyWith(
                        fontSize: 13,
                        fontWeight: FontWeight.w700,
                        color: AppTheme.ink(context),
                      ),
                    ),
                  ),
                  if (t.priority == 'p0' || t.priority == 'p1')
                    Padding(
                      padding: const EdgeInsets.only(left: Sp.s),
                      child: Text(
                        t.priority.toUpperCase(),
                        style: opsKeyStyle(context).copyWith(
                          color: t.priority == 'p0' ? cs.primary : cs.onSurface,
                        ),
                      ),
                    ),
                  const SizedBox(width: Sp.s),
                  Text(
                    '${t.bucket ?? '—'} ${t.points ?? ''}'.toUpperCase(),
                    style: mono,
                  ),
                ],
              ),
              const SizedBox(height: 2),
              Text(
                t.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyMedium!.copyWith(
                  color: cs.onSurface,
                ),
              ),
              const SizedBox(height: Sp.xs),
              Row(
                children: [
                  OpsChip(
                    value: t.status.replaceAll('_', ' '),
                    options: const [],
                    hot: const {'in progress'},
                    onSelected: (_) {},
                  ),
                  const SizedBox(width: Sp.s),
                  Expanded(
                    child: Text(
                      t.status == 'blocked'
                          ? (t.blockedReason ?? '')
                          : t.status == 'done' && t.evidenceKnowns.isNotEmpty
                          ? '→ known ${t.evidenceKnowns.join(', ')}'
                          : t.unknownId != null
                          ? 'resolves ${t.unknownId}'
                          : t.enablerId != null
                          ? 'enabler ${t.enablerId}'
                          : '',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: mono.copyWith(
                        color: t.status == 'blocked' ? cs.error : null,
                      ),
                    ),
                  ),
                ],
              ),
              if (t.deps.isNotEmpty && t.open)
                Padding(
                  padding: const EdgeInsets.only(top: Sp.xs),
                  child: Text('after ${t.deps.join(', ')}', style: mono),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The selected task: record, evidence, its history, and the actions a
/// person can take on it.
class _TaskDetail extends StatelessWidget {
  const _TaskDetail({
    required this.task,
    required this.manager,
    required this.onOpenSession,
  });
  final RouteTask task;
  final RouteManager manager;
  final VoidCallback onOpenSession;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final t = task;
    final key = opsKeyStyle(context);
    final mono = AppTheme.mono.copyWith(
      fontSize: 13,
      color: AppTheme.ink(context),
    );
    Widget row(String k, String v, {Color? color}) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 96, child: Text(k, style: key)),
          Expanded(
            child: Text(v, style: mono.copyWith(color: color)),
          ),
        ],
      ),
    );
    final events = manager.eventsFor(t.id);
    final hasSession =
        t.status == 'in_progress' ||
        t.status == 'blocked' ||
        t.status == 'done';

    return Material(
      color: cs.surfaceContainerLow,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(Sp.l, Sp.l, Sp.l, Sp.xl),
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  t.id,
                  style: AppTheme.mono.copyWith(
                    fontSize: 17,
                    fontWeight: FontWeight.w700,
                    color: AppTheme.ink(context),
                  ),
                ),
              ),
              OpsRemove(
                tooltip: 'Close',
                onPressed: () => manager.select(null),
              ),
            ],
          ),
          const SizedBox(height: Sp.xs),
          Text(
            t.title,
            style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurface),
          ),
          const SizedBox(height: Sp.m),
          row(
            'STATUS',
            t.status.replaceAll('_', ' ').toUpperCase(),
            color: t.status == 'blocked' ? cs.error : null,
          ),
          if (t.blockedReason != null)
            row('REASON', t.blockedReason!, color: cs.error),
          row('PHASE', t.phase.isEmpty ? '—' : t.phase),
          row('EFFORT', '${t.bucket ?? '—'} · ${t.points ?? '—'} pts'),
          row('PRIORITY', t.priority),
          row('SKILL', '${t.skill} · ${t.role}'),
          if (t.unknownId != null) row('RESOLVES', t.unknownId!),
          if (t.enablerId != null) row('ENABLER', t.enablerId!),
          if (t.deps.isNotEmpty) row('AFTER', t.deps.join(', ')),
          if (t.ownerAgent != null) row('OWNER', t.ownerAgent!),
          if (t.evidenceRuns.isNotEmpty) row('RUNS', t.evidenceRuns.join('\n')),
          if (t.evidenceKnowns.isNotEmpty)
            row('KNOWNS', t.evidenceKnowns.join(', ')),
          const SizedBox(height: Sp.l),
          Wrap(
            spacing: Sp.s,
            runSpacing: Sp.s,
            children: [
              if (hasSession)
                FilledButton(
                  onPressed: onOpenSession,
                  child: const Text('OPEN SESSION'),
                ),
              if (t.open)
                PopupMenuButton<String>(
                  tooltip: '',
                  onSelected: (p) =>
                      manager.setPriority(t.id, p, 'set from the board'),
                  itemBuilder: (_) => [
                    for (final p in const ['p0', 'p1', 'p2', 'p3'])
                      PopupMenuItem(
                        value: p,
                        child: Text(
                          '$p  ${_priorityMeaning[p]}'.toUpperCase(),
                          style: AppTheme.mono.copyWith(fontSize: 12),
                        ),
                      ),
                  ],
                  child: OutlinedButton(
                    onPressed: null,
                    child: Text('PRIORITY ${t.priority.toUpperCase()}'),
                  ),
                ),
              if (t.status == 'blocked')
                OutlinedButton(
                  onPressed: () => manager.unblock(t.id),
                  child: const Text('UNBLOCK'),
                ),
              if (t.open)
                OutlinedButton(
                  onPressed: () => _cancel(context),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppTheme.removed(context),
                    side: BorderSide(color: AppTheme.removed(context)),
                  ),
                  child: const Text('CANCEL'),
                ),
            ],
          ),
          if (events.isNotEmpty) ...[
            const OpsLabel('History', centered: false),
            for (final e in events)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: Sp.xs),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          e.kind.toUpperCase(),
                          style: key.copyWith(color: cs.onSurface),
                        ),
                        const SizedBox(width: Sp.s),
                        Text(
                          e.at.replaceFirst('T', ' ').replaceFirst('Z', ''),
                          style: AppTheme.mono.copyWith(
                            fontSize: 11,
                            color: cs.outline,
                          ),
                        ),
                      ],
                    ),
                    if (e.detail.isNotEmpty)
                      Text(
                        e.detail,
                        style: TextStyle(
                          fontSize: 13,
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                    if (e.runs.isNotEmpty || e.knowns.isNotEmpty)
                      Text(
                        [
                          ...e.runs,
                          ...e.knowns.map((k) => 'known $k'),
                        ].join('\n'),
                        style: AppTheme.mono.copyWith(
                          fontSize: 12,
                          color: cs.onSurfaceVariant,
                        ),
                      ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }

  static const _priorityMeaning = {
    'p0': 'spine',
    'p1': 'this phase',
    'p2': 'backlog',
    'p3': 'deferred',
  };

  Future<void> _cancel(BuildContext context) async {
    final stranded = manager.status.tasks
        .where((o) => o.open && o.deps.contains(task.id))
        .map((o) => o.id)
        .toList();
    final c = TextEditingController();
    final reason = await showOpsDialog<String>(
      context,
      tag: 'CANCEL TASK',
      title: task.id,
      body: stranded.isEmpty
          ? 'Cancel asserts this should never be worked — a dead premise, not '
                '"not now" (that is p3). Say why.'
          : 'Cancelling strands ${stranded.join(', ')}: a cancelled dependency '
                'never turns done, so they can never become pickable. Re-point '
                'them or cancel them too. Say why.',
      field: (ctx) => TextField(
        controller: c,
        autofocus: true,
        maxLines: 3,
        minLines: 2,
        style: TextStyle(color: AppTheme.ink(ctx)),
        decoration: const InputDecoration(
          hintText: 'Why is the premise dead?',
          border: OutlineInputBorder(),
        ),
      ),
      actions: (ctx) => ValueListenableBuilder(
        valueListenable: c,
        builder: (ctx, v, _) => Row(
          children: [
            FilledButton(
              onPressed: v.text.trim().isEmpty
                  ? null
                  : () => Navigator.pop(ctx, v.text),
              style: FilledButton.styleFrom(
                backgroundColor: AppTheme.removed(ctx),
              ),
              child: const Text('CANCEL TASK'),
            ),
            const Spacer(),
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('KEEP'),
            ),
          ],
        ),
      ),
    );
    if (reason != null) await manager.cancel(task.id, reason);
  }
}
