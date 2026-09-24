import 'package:flutter/material.dart';

import '../../models/brief.dart';
import '../../models/route.dart';
import '../../state/brief_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/inline_text.dart';
import '../../widgets/ops.dart';
import 'close_phase_dialog.dart';

/// Phases as a track: one cell per phase, a thick state bar across its top
/// (closed solid, current accented, upcoming faint), title / description /
/// decision beneath. Three fit; more scroll, centred on the current phase.
///
/// The current phase is computed — the first one not closed — exactly as
/// Terra does it. You advance by closing, with a stated reason.
class PhaseTrack extends StatefulWidget {
  const PhaseTrack(this.phases, this.manager, {super.key, this.onOpenRoute});
  final List<Phase> phases;
  final BriefManager manager;

  /// Open the route board scoped to a phase id.
  final ValueChanged<String>? onOpenRoute;

  /// How many phases fit before the track scrolls.
  static const visible = 3;

  @override
  State<PhaseTrack> createState() => _PhaseTrackState();
}

class _PhaseTrackState extends State<PhaseTrack> {
  final _scroll = ScrollController();

  /// (load generation, current index) the window was last positioned for.
  /// Repositions on load, save, revert and close/reopen; in between the
  /// user's own scrolling is left alone.
  (int, int)? _positioned;

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _follow((int, int) key, double offset) {
    if (_positioned == key) return;
    _positioned = key;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      final target = offset.clamp(0.0, _scroll.position.maxScrollExtent);
      if (_scroll.position.pixels == 0 && target == 0) return;
      _scroll.animateTo(
        target,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final phases = widget.phases;
    final manager = widget.manager;
    final cs = Theme.of(context).colorScheme;
    final current = phases.indexWhere((p) => p.status == 'open');
    final open = phases.where((p) => p.status == 'open').length;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          OpsLabel(
            'Phases',
            trailing: phases.isEmpty
                ? null
                : Text(
                    '$open / ${phases.length} OPEN',
                    style: opsLabelStyle(context),
                  ),
          ),
          const SizedBox(height: 12),
          LayoutBuilder(
            builder: (context, c) {
              const addWidth = 36.0;
              // Fewer than [visible] phases share the width; more get a
              // fixed third each and scroll.
              final slots = phases.length.clamp(1, PhaseTrack.visible);
              final cellWidth = (c.maxWidth - addWidth) / slots;
              // Centre the current phase; the clamp keeps the first phase
              // flush left and the last flush right.
              final firstShown = (current - PhaseTrack.visible ~/ 2).clamp(
                0,
                (phases.length - PhaseTrack.visible).clamp(0, 1 << 30),
              );
              _follow((manager.generation, current), firstShown * cellWidth);
              final scrolls = phases.length > PhaseTrack.visible;

              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (phases.isNotEmpty)
                    Expanded(
                      child: Scrollbar(
                        thumbVisibility: scrolls,
                        controller: _scroll,
                        child: SingleChildScrollView(
                          controller: _scroll,
                          scrollDirection: Axis.horizontal,
                          padding: EdgeInsets.only(bottom: scrolls ? 10 : 0),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              for (final (i, ph) in phases.indexed)
                                SizedBox(
                                  width: cellWidth,
                                  child: _PhaseCell(
                                    key: ValueKey(ph.key),
                                    phase: ph,
                                    state: ph.status == 'closed'
                                        ? PhaseState.closed
                                        : i == current
                                        ? PhaseState.current
                                        : PhaseState.upcoming,
                                    manager: manager,
                                    onOpenRoute: widget.onOpenRoute,
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  _AddPhaseCell(
                    wide: phases.isEmpty,
                    onTap: () {
                      final ph = Phase(id: '', title: '', status: 'open');
                      manager.add(ph.key, (_) => phases.add(ph));
                    },
                  ),
                ],
              );
            },
          ),
          const SizedBox(height: 4),
          Container(height: 1, color: cs.outlineVariant),
        ],
      ),
    );
  }
}

enum PhaseState { closed, current, upcoming }

class _PhaseCell extends StatelessWidget {
  const _PhaseCell({
    super.key,
    required this.phase,
    required this.state,
    required this.manager,
    this.onOpenRoute,
  });
  final Phase phase;
  final PhaseState state;
  final BriefManager manager;
  final ValueChanged<String>? onOpenRoute;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ph = phase;
    final barColor = switch (state) {
      PhaseState.closed => cs.onSurface,
      PhaseState.current => cs.primary,
      PhaseState.upcoming => cs.outlineVariant,
    };
    final dim = state == PhaseState.closed;

    return Tooltip(
      message: ph.id.isEmpty ? '' : 'id: ${ph.id}',
      waitDuration: const Duration(milliseconds: 900),
      child: Container(
        margin: const EdgeInsets.only(right: 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(height: 5, color: barColor),
            const SizedBox(height: 8),
            InlineText(
              value: ph.title,
              resetKey: manager.generation,
              autofocus: manager.focusKey == ph.key,
              compact: true,
              placeholder: 'Phase title',
              style: theme.textTheme.titleMedium!.copyWith(
                fontWeight: FontWeight.w700,
                color: dim ? cs.onSurfaceVariant : null,
              ),
              onChanged: (v) => manager.edit((_) => ph.title = v),
            ),
            InlineText(
              value: ph.description,
              resetKey: manager.generation,
              compact: true,
              placeholder: 'What this phase delivers',
              style: theme.textTheme.bodyMedium!.copyWith(
                color: dim ? cs.onSurfaceVariant : null,
                height: 1.35,
              ),
              onChanged: (v) => manager.edit((_) => ph.description = v),
            ),
            const SizedBox(height: 6),
            _PhaseTasks(
              row: manager.route.phase(ph.id),
              onTap: () => onOpenRoute?.call(ph.id),
            ),
            _PhaseDecision(phase: ph, state: state, manager: manager),
            const SizedBox(height: 10),
          ],
        ),
      ),
    );
  }
}

/// The one action a phase offers, by state: the current phase can be
/// closed (with a reason — Terra records it); a closed phase shows the
/// basis and can be reopened; an upcoming phase waits its turn.
class _PhaseDecision extends StatelessWidget {
  const _PhaseDecision({
    required this.phase,
    required this.state,
    required this.manager,
  });
  final Phase phase;
  final PhaseState state;
  final BriefManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final key = opsKeyStyle(context);
    // Right beside the decision, not pushed to the far edge.
    final remove = Padding(
      padding: const EdgeInsets.only(left: 6),
      child: OpsRemove(
        tooltip: 'Remove phase',
        onPressed: () async {
          final yes = await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
              title: Text('REMOVE PHASE ${phase.title.toUpperCase()}', style: opsHeadingStyle(ctx)),
              content: const Text('The phase goes from the draft; the entries it owned stay on the brief. Nothing is saved until you save.'),
              actions: [
                TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
                TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('REMOVE')),
              ],
            ),
          );
          if (yes == true) manager.edit((b) => b.phases.remove(phase));
        },
      ),
    );
    switch (state) {
      case PhaseState.current:
        return Row(
          children: [
            OutlinedButton(
              onPressed: () async {
                final reason = await askCloseReason(context, phase.title);
                if (reason != null) manager.edit((_) => phase.close(reason));
              },
              style: OutlinedButton.styleFrom(
                visualDensity: VisualDensity.compact,
                side: BorderSide(color: cs.primary),
                foregroundColor: cs.primary,
                textStyle: key.copyWith(fontSize: 12),
              ),
              child: const Text('CLOSE PHASE'),
            ),
            remove,
          ],
        );
      case PhaseState.closed:
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('CLOSED', style: key.copyWith(color: cs.onSurface)),
                const SizedBox(width: 8),
                TextButton(
                  onPressed: () => manager.edit((_) => phase.reopen()),
                  style: TextButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                    textStyle: key,
                  ),
                  child: const Text('REOPEN'),
                ),
                remove,
              ],
            ),
            if ((phase.closedReason ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8),
                child: _ClampedText(
                  phase.closedReason!,
                  style: TextStyle(
                    fontSize: 13,
                    color: cs.onSurfaceVariant,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ),
          ],
        );
      case PhaseState.upcoming:
        return Row(
          children: [
            Text('UPCOMING', style: key),
            remove,
          ],
        );
    }
  }
}

/// Narrow "+" cell at the end of the track; full width when there are
/// no phases yet so the affordance is obvious.
class _AddPhaseCell extends StatelessWidget {
  const _AddPhaseCell({required this.onTap, required this.wide});
  final VoidCallback onTap;
  final bool wide;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final cell = InkWell(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(height: 5, color: cs.outlineVariant.withValues(alpha: 0.5)),
          const SizedBox(height: 10),
          Row(
            mainAxisAlignment: wide
                ? MainAxisAlignment.start
                : MainAxisAlignment.center,
            children: [
              Icon(Icons.add, size: 14, color: cs.onSurfaceVariant),
              if (wide) ...[
                const SizedBox(width: 6),
                Text(
                  'ADD PHASE',
                  style: opsLabelStyle(context).copyWith(letterSpacing: 1.2),
                ),
              ],
            ],
          ),
          const SizedBox(height: 10),
        ],
      ),
    );
    return wide ? Expanded(child: cell) : SizedBox(width: 36, child: cell);
  }
}

/// Text clamped to three lines with a MORE / LESS toggle when it overflows.
class _ClampedText extends StatefulWidget {
  const _ClampedText(this.text, {required this.style});
  final String text;
  final TextStyle style;
  static const maxLines = 3;

  @override
  State<_ClampedText> createState() => _ClampedTextState();
}

class _ClampedTextState extends State<_ClampedText> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LayoutBuilder(
      builder: (context, c) {
        final painter = TextPainter(
          text: TextSpan(text: widget.text, style: widget.style),
          maxLines: _ClampedText.maxLines,
          textDirection: Directionality.of(context),
        )..layout(maxWidth: c.maxWidth);
        final overflows = painter.didExceedMaxLines;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.text,
              style: widget.style,
              maxLines: _expanded ? null : _ClampedText.maxLines,
              overflow: _expanded ? null : TextOverflow.ellipsis,
            ),
            if (overflows)
              InkWell(
                onTap: () => setState(() => _expanded = !_expanded),
                child: Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    _expanded ? 'LESS' : 'MORE',
                    style: opsKeyStyle(context).copyWith(color: cs.primary),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }
}

/// What the route holds for this phase: `n done · m left ↗`, or the exit
/// warning. Click to open the board scoped to the phase.
class _PhaseTasks extends StatelessWidget {
  const _PhaseTasks({required this.row, required this.onTap});
  final PhaseRow? row;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final r = row;
    if (r == null || r.total == 0) return const SizedBox(height: 4);
    final text = r.exitReady
        ? '${r.done} TASKS · EXIT READY'
        : '${r.done} DONE · ${r.open} LEFT'
              '${r.blocked > 0 ? ' · ${r.blocked} BLOCKED' : ''}';
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 2, 8, 6),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              text,
              style: opsKeyStyle(context).copyWith(
                color: r.exitReady
                    ? AppTheme.added(context)
                    : r.blocked > 0
                    ? cs.error
                    : cs.onSurface,
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
