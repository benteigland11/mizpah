import 'package:flutter/material.dart';

import '../../models/loop.dart';
import '../../state/loop_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/ops.dart';

/// Agents as sessions: a list on the left (the controller first, then one
/// per worker task), the selected session's transcript on the right. The
/// mode dial is the one control, top right.
class LoopScreen extends StatelessWidget {
  const LoopScreen({super.key, required this.manager});
  final LoopManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        final s = manager.state;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _TopBar(manager: manager, state: s),
            Divider(height: 1, color: cs.outlineVariant),
            Expanded(
              child: Row(
                children: [
                  SizedBox(width: 300, child: _SessionList(manager: manager)),
                  VerticalDivider(width: 1, color: cs.outlineVariant),
                  Expanded(child: _Transcript(manager: manager)),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}

/// Driver state on the left, the mode dial on the right.
class _TopBar extends StatelessWidget {
  const _TopBar({required this.manager, required this.state});
  final LoopManager manager;
  final LoopState state;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final s = state;
    final status = s.running
        ? 'RUNNING'
        : (s.stopReason ?? 'IDLE').toUpperCase().replaceAll('_', ' ');
    final mono = AppTheme.mono.copyWith(
      fontSize: 13,
      color: cs.onSurfaceVariant,
    );
    return Padding(
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.m, Sp.l, Sp.m),
      child: Row(
        children: [
          OpsChip(
            value: status,
            options: const [],
            hot: const {'RUNNING'},
            onSelected: (_) {},
          ),
          const SizedBox(width: Sp.l),
          Text('CYCLE ${s.cycle} / ${s.maxCycles}', style: mono),
          const SizedBox(width: Sp.l),
          Text('TASKS ${s.tasksRun} / ${s.maxTasks}', style: mono),
          const Spacer(),
          Text(s.mode.blurb, style: mono),
          const SizedBox(width: Sp.l),
          _ModeDial(mode: s.mode, onChanged: manager.setMode),
        ],
      ),
    );
  }
}

/// HOLD · PROPOSE · RUN as three joined square buttons.
class _ModeDial extends StatelessWidget {
  const _ModeDial({required this.mode, required this.onChanged});
  final LoopMode mode;
  final ValueChanged<LoopMode> onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      decoration: BoxDecoration(
        border: Border.all(color: cs.outline),
        borderRadius: BorderRadius.circular(AppTheme.radius),
      ),
      clipBehavior: Clip.antiAlias,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final (i, m) in LoopMode.values.indexed)
            InkWell(
              onTap: () => onChanged(m),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: Sp.l,
                  vertical: Sp.s,
                ),
                decoration: BoxDecoration(
                  color: m == mode ? cs.primary : null,
                  border: Border(
                    left: i == 0
                        ? BorderSide.none
                        : BorderSide(color: cs.outline),
                  ),
                ),
                child: Text(
                  m.name.toUpperCase(),
                  style: AppTheme.mono.copyWith(
                    fontSize: 13,
                    letterSpacing: 1.2,
                    fontWeight: FontWeight.w600,
                    color: m == mode ? cs.onPrimary : cs.onSurface,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// One row per session. The controller is a session like any other.
class _SessionList extends StatelessWidget {
  const _SessionList({required this.manager});
  final LoopManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final s = manager.state;
    final c = s.controller;
    final last = c.recent.isEmpty ? null : c.recent.first;
    return Material(
      color: cs.surfaceContainerLow,
      child: ListView(
        padding: const EdgeInsets.symmetric(vertical: Sp.s),
        children: [
          _SessionRow(
            id: 'controller',
            title: 'Controller',
            subtitle: last == null
                ? c.model
                : '${last.kind} · ${_ago(last.at)}',
            state: s.running ? _Dot.live : _Dot.idle,
            selected: manager.selected == 'controller',
            onTap: () => manager.select('controller'),
          ),
          for (final w in s.workers)
            _SessionRow(
              id: w.taskId,
              title: w.taskId,
              subtitle: switch (w.status) {
                'in_progress' => 'turn ${w.turns} / ${w.budgetTurns}',
                'blocked' => 'blocked · ${w.turns} / ${w.budgetTurns}',
                _ => 'done · ${w.turns} turns',
              },
              state: switch (w.status) {
                'in_progress' => _Dot.live,
                'blocked' => _Dot.blocked,
                _ => _Dot.done,
              },
              selected: manager.selected == w.taskId,
              onTap: () => manager.select(w.taskId),
            ),
        ],
      ),
    );
  }
}

enum _Dot { live, blocked, done, idle }

class _SessionRow extends StatelessWidget {
  const _SessionRow({
    required this.id,
    required this.title,
    required this.subtitle,
    required this.state,
    required this.selected,
    required this.onTap,
  });
  final String id;
  final String title;
  final String subtitle;
  final _Dot state;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dot = switch (state) {
      _Dot.live => cs.primary,
      _Dot.blocked => cs.error,
      _Dot.done => AppTheme.added(context),
      _Dot.idle => cs.outline,
    };
    return InkWell(
      onTap: onTap,
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
                    title,
                    overflow: TextOverflow.ellipsis,
                    style: AppTheme.mono.copyWith(
                      fontSize: 14,
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                      color: selected ? AppTheme.ink(context) : cs.onSurface,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    subtitle,
                    style: AppTheme.mono.copyWith(
                      fontSize: 12,
                      color: cs.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// The selected session, read top to bottom: its header, then every turn.
class _Transcript extends StatelessWidget {
  const _Transcript({required this.manager});
  final LoopManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final s = manager.state;
    final id = manager.selected;
    final worker = s.workers.where((w) => w.taskId == id).firstOrNull;
    final turns = manager.transcript;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        worker == null
            ? _ControllerHeader(state: s.controller)
            : _WorkerHeader(w: worker),
        Divider(height: 1, color: cs.outlineVariant),
        Expanded(
          child: turns.isEmpty
              ? Center(
                  child: Text(
                    'No turns yet.',
                    style: TextStyle(color: cs.onSurfaceVariant),
                  ),
                )
              : ListView.builder(
                  padding: const EdgeInsets.fromLTRB(Sp.l, Sp.s, Sp.l, Sp.xl),
                  itemCount: turns.length,
                  itemBuilder: (context, i) => _TurnRow(turns[i]),
                ),
        ),
      ],
    );
  }
}

class _WorkerHeader extends StatelessWidget {
  const _WorkerHeader({required this.w});
  final WorkerSession w;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final blocked = w.status == 'blocked';
    final mono = AppTheme.mono.copyWith(
      fontSize: 13,
      color: cs.onSurfaceVariant,
    );
    return Padding(
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.l, Sp.l, Sp.m),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                w.taskId,
                style: AppTheme.mono.copyWith(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                  color: AppTheme.ink(context),
                ),
              ),
              const SizedBox(width: Sp.m),
              OpsChip(
                value: w.status.replaceAll('_', ' '),
                options: const [],
                hot: const {'in progress'},
                onSelected: (_) {},
              ),
              const Spacer(),
              Text(
                'TURN ${w.turns} / ${w.budgetTurns}',
                style: opsLabelStyle(context)
                    .copyWith(color: blocked ? cs.error : cs.onSurface),
              ),
            ],
          ),
          const SizedBox(height: Sp.xs),
          Text(
            w.taskTitle,
            style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurface),
          ),
          const SizedBox(height: Sp.xs),
          Wrap(
            spacing: Sp.xl,
            children: [
              Text('resolves ${w.unknownId}', style: mono),
              Text('bucket ${w.bucket}', style: mono),
              Text('handoffs ${w.handoffs}', style: mono),
              Text(
                w.gateOk ? 'gate GREEN' : 'gate RED (${w.gateProblems.length})',
                style: mono.copyWith(
                  color: w.gateOk ? AppTheme.added(context) : cs.error,
                ),
              ),
            ],
          ),
          if (blocked && w.blockedReason != null)
            Padding(
              padding: const EdgeInsets.only(top: Sp.s),
              child: Text(
                w.blockedReason!,
                style: theme.textTheme.bodyMedium!.copyWith(
                  color: cs.error,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _ControllerHeader extends StatelessWidget {
  const _ControllerHeader({required this.state});
  final ControllerState state;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final mono = AppTheme.mono.copyWith(
      fontSize: 13,
      color: cs.onSurfaceVariant,
    );
    return Padding(
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.l, Sp.l, Sp.m),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Controller',
            style: AppTheme.mono.copyWith(
              fontSize: 18,
              fontWeight: FontWeight.w700,
              color: AppTheme.ink(context),
            ),
          ),
          const SizedBox(height: Sp.xs),
          Text(
            'Reads the brief against the map; mints unknowns and tasks, '
            'checks in on workers, proposes brief changes.',
            style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurface),
          ),
          const SizedBox(height: Sp.xs),
          Wrap(
            spacing: Sp.xl,
            children: [
              Text('${state.model} · ${state.endpoint}', style: mono),
              Text('next: ${state.next}', style: mono),
              Text(
                'held guidance: ${state.heldGuidance ?? 'none'}',
                style: mono,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// One turn: a line you can expand for its body. Check-ins, handoffs and
/// notes are callouts; failed calls are marked.
class _TurnRow extends StatefulWidget {
  const _TurnRow(this.t);
  final Turn t;

  @override
  State<_TurnRow> createState() => _TurnRowState();
}

class _TurnRowState extends State<_TurnRow> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = widget.t;
    final callout = t.kind != 'tool';
    final color = switch (t.kind) {
      'checkin' => cs.primary,
      'handoff' => cs.onSurfaceVariant,
      'note' => cs.error,
      'step' => t.ok ? cs.onSurface : cs.error,
      _ => t.ok ? cs.onSurface : cs.error,
    };
    final hasBody = t.body.isNotEmpty;
    return InkWell(
      onTap: hasBody ? () => setState(() => _open = !_open) : null,
      child: Container(
        margin: EdgeInsets.symmetric(vertical: callout ? Sp.s : 1),
        padding: EdgeInsets.symmetric(
          vertical: callout ? Sp.s : Sp.xs,
          horizontal: Sp.s,
        ),
        decoration: callout
            ? BoxDecoration(
                color: cs.surfaceContainer,
                border: Border(left: BorderSide(color: color, width: 3)),
              )
            : null,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 44,
                  child: Text(
                    t.n.toString().padLeft(2, '0'),
                    style: AppTheme.mono.copyWith(
                      fontSize: 12,
                      color: cs.onSurfaceVariant,
                    ),
                  ),
                ),
                SizedBox(
                  width: 18,
                  child: Text(
                    hasBody ? (_open ? '▾' : '▸') : (t.ok ? '' : '✗'),
                    style: AppTheme.mono.copyWith(
                      fontSize: 12,
                      color: t.ok ? cs.onSurfaceVariant : cs.error,
                    ),
                  ),
                ),
                Expanded(
                  child: Text(
                    t.title,
                    style: AppTheme.mono.copyWith(
                      fontSize: 13,
                      color: callout
                          ? color
                          : (t.ok ? AppTheme.ink(context) : cs.error),
                      fontWeight: callout ? FontWeight.w600 : FontWeight.w400,
                    ),
                  ),
                ),
                const SizedBox(width: Sp.m),
                Text(
                  _ago(t.at),
                  style: AppTheme.mono.copyWith(
                    fontSize: 11,
                    color: cs.outline,
                  ),
                ),
              ],
            ),
            if (_open && hasBody)
              Padding(
                padding: const EdgeInsets.fromLTRB(62, Sp.xs, 0, Sp.xs),
                child: SelectableText(
                  t.body,
                  style: AppTheme.mono.copyWith(
                    fontSize: 12,
                    color: cs.onSurfaceVariant,
                    height: 1.5,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

String _ago(DateTime t) {
  final d = DateTime.now().difference(t);
  if (d.inSeconds < 60) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes} min ago';
  return '${d.inHours} h ago';
}
