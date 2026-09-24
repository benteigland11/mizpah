import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:gpt_markdown/gpt_markdown.dart';

import '../../engine/deputy_plain.dart';
import '../../engine/engine.dart';
import '../../models/provider.dart';
import '../../state/app_nav.dart';
import '../../state/app_settings.dart';
import '../../state/deputy_manager.dart';
import '../../state/provider_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/model_pick.dart';
import '../../widgets/ops.dart';
import '../brief/document.dart';

/// Home: the Deputy's office. The conversation runs down the left; the
/// paper the Deputy has pulled up sits on the right for the Administrator
/// to read and mark. A draft brief there is editable in place and ends in
/// the signature line that makes it a project.
class DeputyScreen extends StatefulWidget {
  const DeputyScreen({
    super.key,
    required this.manager,
    required this.settings,
    required this.providers,
    required this.nav,
  });
  final DeputyManager manager;
  final AppSettings settings;

  /// For the model chip in the header: which model the seat runs on.
  final ProviderManager providers;
  final AppNav nav;

  @override
  State<DeputyScreen> createState() => _DeputyScreenState();
}

class _DeputyScreenState extends State<DeputyScreen> {
  final _scroll = ScrollController();
  int _seen = 0;

  @override
  void initState() {
    super.initState();
    // The chip needs the harness config read; the Providers page does it
    // on entry, and this is the first surface to show a model.
    if (widget.providers.current.isEmpty) widget.providers.load();
    widget.nav.addListener(_onNav);
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    widget.nav.removeListener(_onNav);
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  /// Arriving at Home: the foot of the record, whatever was left.
  void _onNav() {
    if (widget.nav.tab == AppNav.chat && !widget.manager.busy) _toEnd();
  }

  /// Near the top with more on record: the page before comes in above,
  /// and the view holds where it was.
  void _onScroll() {
    if (!_scroll.hasClients) return;
    final m = widget.manager;
    if (_scroll.position.pixels <= 80 && m.hasEarlier && !_loadingEarlier) {
      _loadingEarlier = true;
      final before = _scroll.position.maxScrollExtent;
      m.loadEarlier();
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _scroll.hasClients) {
          _scroll.jumpTo(_scroll.position.pixels + (_scroll.position.maxScrollExtent - before));
        }
        _loadingEarlier = false;
      });
    }
  }

  bool _loadingEarlier = false;

  /// To the foot. A list of text rows settles its extent over a frame or
  /// two, so one jump lands short: jump, then again once layout caught up.
  void _toEnd([int passes = 3]) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      final end = _scroll.position.maxScrollExtent;
      if (_scroll.position.pixels != end) _scroll.jumpTo(end);
      if (passes > 1) _toEnd(passes - 1);
    });
  }

  /// The message you just sent, to anchor the view on.
  final _sent = GlobalKey();

  /// How the view moves. The record opens at its foot. When you send, your
  /// message goes to the top of the window (a small margin above it, so the
  /// tail of the previous exchange still shows) and the Deputy's work and
  /// reply fill in below it. While the work fits under the message the view
  /// stays put; once it overflows, the foot follows the newest step — unless
  /// you scrolled. Nothing moves when the reply lands: you are already
  /// reading where it arrives. A trailing space under the list, always
  /// there, is what lets the last message reach the top.
  void _follow(DeputyManager m) {
    final count = m.turns.length + (m.busy ? 1 : 0);
    if (count == _seen) return;
    final first = _seen == 0;
    _seen = count;
    final justSent = m.busy && m.turns.isNotEmpty && m.turns.last.role == 'user';
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      if (justSent && _sent.currentContext != null) {
        Scrollable.ensureVisible(_sent.currentContext!, alignment: 0, duration: const Duration(milliseconds: 220))
            .then((_) {
          if (!mounted || !_scroll.hasClients) return;
          final p = _scroll.position;
          p.jumpTo((p.pixels - topMargin).clamp(0.0, p.maxScrollExtent));
          _anchorPixels = p.pixels;
          _autoPixels = null;
        });
        _anchored = true;
      } else if (first) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
        _toEnd();
      } else if (!m.busy) {
        // The reply landed under the anchored message: stay. Anything else
        // new while nothing was anchored (a reply read from another window)
        // brings the foot into view.
        if (!_anchored) _scroll.jumpTo(_scroll.position.maxScrollExtent);
        _anchored = false;
      }
    });
  }

  /// Above the sent message when it is pinned. Zero: the bubble's own
  /// margin is the gap, and nothing of the previous exchange shows above
  /// it — a sliver of it (the descenders of its last line) read as sloppy.
  static const topMargin = 0.0;

  /// Index of the last line the person sent: the tail starts there.
  static int _tailStart(DeputyManager m) {
    final i = m.visible.lastIndexWhere((t) => t.role == 'user');
    return i < 0 ? m.visible.length : i;
  }

  bool _anchored = false;

  /// Where the anchor put the view, and where the follow last left it: a
  /// hand on the wheel (the view moved from where we left it) ends the follow.
  double? _anchorPixels;
  double? _autoPixels;

  /// While the Deputy works under your anchored message, its steps grow
  /// below. Until they fill the window the message stays at the top; once
  /// they overflow, the foot follows the newest step — unless you scrolled.
  void _followWork(DeputyManager m) {
    if (!m.busy || !_anchored) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients || !m.busy) return;
      final p = _scroll.position;
      _anchorPixels ??= p.pixels;
      final left = _autoPixels ?? _anchorPixels!;
      if ((p.pixels - left).abs() > 40) return; // the person moved it
      // At rest the tail fills the window exactly, so the anchor is the
      // foot; the extent growing past it means the work overflowed.
      if (p.maxScrollExtent > _anchorPixels! + 1 && (p.pixels - p.maxScrollExtent).abs() > 1) {
        p.jumpTo(p.maxScrollExtent);
        _autoPixels = p.pixels;
      }
    });
  }

  /// Clear everything: the Deputy's memory and the conversation. The
  /// record goes aside on disk, readable; the seat starts with nothing.
  Future<void> _confirmClear(BuildContext context, DeputyManager m) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('CLEAR THE DEPUTY', style: opsHeadingStyle(ctx)),
        content: const Text(
          'Its memory and this whole conversation go. The Deputy starts with nothing — '
          'what you told it before, it will not know. The record is kept aside on disk, '
          'not shown here again.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('CLEAR')),
        ],
      ),
    );
    if (yes == true) await m.reset();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final m = widget.manager;
    return ListenableBuilder(
      listenable: Listenable.merge([m, m.desk]),
      builder: (context, _) {
        _follow(m);
        _followWork(m);
        final onDesk = m.desk.selectedId != null && m.desk.draft != null;
        // The conversation yields to the paper when there is paper; with
        // the desk clear it takes the room, at a reading width.
        final conversation = Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
                    child: Row(
                      children: [
                        Text('DEPUTY', style: opsHeadingStyle(context)),
                        const SizedBox(width: Sp.l),
                        ModelPick(
                          role: ModelRole.deputy,
                          providers: widget.providers,
                          settings: widget.settings,
                          nav: widget.nav,
                          enabled: !m.busy,
                        ),
                        const Spacer(),
                        // The record count yields first when the pane is narrow: the
                        // model chip and the heading keep their width, this line
                        // clips (it overflowed the row by 17 px beside the paper).
                        Flexible(
                          child: Text(
                            m.loading
                                ? 'READING…'
                                : m.busy
                                ? 'WORKING'
                                : m.turns.isEmpty
                                ? 'NO RECORD'
                                : '${m.turns.length} ON RECORD',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            softWrap: false,
                            style: opsLabelStyle(context).copyWith(color: m.busy ? cs.primary : null),
                          ),
                        ),
                        if (m.turns.isNotEmpty && !m.busy) ...[
                          const SizedBox(width: Sp.m),
                          OpsRemove(onPressed: () => _confirmClear(context, m), tooltip: 'Clear the conversation'),
                        ],
                      ],
                    ),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  Expanded(
                    child: m.turns.isEmpty && !m.loading
                        ? Padding(
                            padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.xxl, Sp.xl, 0),
                            child: Text(
                              'Nothing on record. Say what you want done — a page, a '
                              'measurement, a report — and the Deputy drafts the brief '
                              'here for your signature.',
                              style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant, height: 1.5),
                            ),
                          )
                        : LayoutBuilder(
                            builder: (context, box) => ListView(
                            controller: _scroll,
                            padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.l),
                            children: [
                              // Everything before the last message you sent, then that
                              // message and all that follows it — the tail — in a box at
                              // least a window tall (less the margin above the message).
                              // So the message can reach the top, the work and reply fill
                              // in below it, and there is nothing past the foot to scroll
                              // into: the box only grows once its content overflows.
                              if (m.hasEarlier)
                                Padding(
                                  padding: const EdgeInsets.only(bottom: Sp.m),
                                  child: Center(
                                    child: TextButton(
                                      onPressed: m.loadEarlier,
                                      child: Text('EARLIER · ${m.turns.length - m.shown} MORE ON RECORD', style: opsKeyStyle(context)),
                                    ),
                                  ),
                                ),
                              for (final (i, t) in m.visible.indexed)
                                if (i < _tailStart(m)) _TurnLine(turn: t),
                              ConstrainedBox(
                                key: _sent,
                                constraints: BoxConstraints(minHeight: (box.maxHeight - topMargin - Sp.l).clamp(0.0, double.infinity)),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.stretch,
                                  children: [
                                    for (final (i, t) in m.visible.indexed)
                                      if (i >= _tailStart(m)) ...[
                                        _TurnLine(turn: t),
                                        // The finished turn's steps sit under its reply, folded.
                                        if (!m.busy && i == m.visible.length - 1 && t.role == 'deputy' && m.steps.isNotEmpty)
                                          _Steps(steps: m.steps, live: false),
                                      ],
                                    if (m.busy) ...[
                                      _Working(activity: m.activity, stopping: m.stopping),
                                      if (m.steps.isNotEmpty) _Steps(steps: m.steps, live: true),
                                    ],
                                    if (m.error != null)
                                      Padding(
                                        padding: const EdgeInsets.only(top: Sp.s),
                                        child: OpsError(m.error!),
                                      ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                          ),
                  ),
                  _SayLine(enabled: !m.busy, stopping: m.stopping, onSend: m.send, onStop: m.stop, unsent: m.unsent),
                ],
              );
        if (!onDesk) {
          return Center(
            child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 820), child: conversation),
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(width: 460, child: conversation),
            VerticalDivider(width: 1, color: cs.outlineVariant),
            Expanded(
              child: onDesk
                  ? Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Padding(
                          padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.l, Sp.s),
                          child: Row(
                            children: [
                              Text('ON THE DESK', style: opsLabelStyle(context)),
                              const SizedBox(width: Sp.m),
                              Text(
                                m.desk.selectedId!.toUpperCase(),
                                style: opsKeyStyle(context).copyWith(color: cs.primary),
                              ),
                              const Spacer(),
                              if (m.desk.dirty)
                                Text('UNSAVED · CTRL+S', style: opsKeyStyle(context).copyWith(color: cs.primary)),
                              const SizedBox(width: Sp.m),
                              OpsRemove(onPressed: m.clearDesk, tooltip: 'Clear the desk'),
                            ],
                          ),
                        ),
                        Divider(height: 1, color: cs.outlineVariant),
                        Expanded(child: BriefDocument(manager: m.desk, settings: widget.settings, providers: widget.providers)),
                      ],
                    )
                  : Center(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 48),
                        child: Text(
                          'The desk is clear. When the Deputy pulls a draft up it appears here.',
                          textAlign: TextAlign.center,
                          style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
                        ),
                      ),
                    ),
            ),
          ],
        );
      },
    );
  }
}

/// One line of the record. The Deputy's turns lead with a mark; the
/// Administrator's are in ink; the engine's notes are set small.
class _TurnLine extends StatelessWidget {
  const _TurnLine({required this.turn});
  final DeputyTurn turn;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final body = theme.textTheme.bodyLarge!.copyWith(height: 1.45);
    if (turn.role == 'system') {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: Sp.s),
        child: Text(turn.text, style: opsKeyStyle(context).copyWith(color: turn.error ? cs.error : null)),
      );
    }
    final mine = turn.role == 'user';
    if (mine) {
      // Yours: set apart as a message, on a surface, to the right — the
      // same box you typed it in. The Deputy's replies stay plain text.
      return Semantics(
        label: 'You said',
        child: Padding(
        padding: const EdgeInsets.fromLTRB(48, Sp.l, 0, Sp.s),
        child: Align(
          alignment: Alignment.centerRight,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: cs.surfaceContainerHigh,
              borderRadius: const BorderRadius.only(
                topLeft: Radius.circular(12),
                topRight: Radius.circular(12),
                bottomLeft: Radius.circular(12),
                bottomRight: Radius.circular(3),
              ),
            ),
            child: SelectableText(turn.text, style: body.copyWith(color: AppTheme.ink(context))),
          ),
        ),
      ),
      );
    }
    return Semantics(
      label: 'The Deputy said',
      child: Padding(
      padding: const EdgeInsets.symmetric(vertical: Sp.s),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 22,
            child: ExcludeSemantics(child: Text(
              mine ? '' : '▸',
              style: body.copyWith(color: cs.primary),
            )),
          ),
          Expanded(
            // The Deputy writes prose with paths and commands in backticks,
            // the odd list, and now and then a formula: rendered as such.
            // \( \) and \[ \] set maths; $…$ is left alone, since a budget
            // line says "$" and means money.
            child: SelectionArea(
              child: GptMarkdown(
                turn.text,
                style: body.copyWith(color: cs.onSurface),
                // A backticked term is an id, a path, a command: set in mono,
                // in ink, in the line — no chip, the way ids are set everywhere
                // else on the desk.
                inlineCodeBuilder: (context, code, style, _) => TextSpan(
                  text: code,
                  style: AppTheme.mono.copyWith(
                    fontSize: (style.fontSize ?? 15) * 0.9,
                    height: style.height,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.2,
                    color: AppTheme.ink(context),
                    // A hairline under the term, not a box around it: it reads
                    // as a name in the sentence and still stands apart.
                    decoration: TextDecoration.underline,
                    decorationColor: cs.outlineVariant,
                    decorationThickness: 1,
                  ),
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

/// The Deputy's side of the conversation while it works: what it is doing
/// this moment — the command running, or thinking — so a long turn reads
/// as a partner at work, not a spinner.
class _Working extends StatefulWidget {
  const _Working({this.activity, this.stopping = false});
  final Map<String, dynamic>? activity;
  final bool stopping;

  @override
  State<_Working> createState() => _WorkingState();
}

class _WorkingState extends State<_Working> with SingleTickerProviderStateMixin {
  late final _ticker = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return AnimatedBuilder(
      animation: _ticker,
      builder: (context, _) {
        final dots = '·' * (1 + (_ticker.value * 3).floor() % 3);
        final a = widget.activity;
        final command = (a?['command'] as String?)?.replaceAll(RegExp(r'\s+'), ' ').trim();
        final doing = widget.stopping
            ? 'stopping after this step'
            : a?['kind'] == 'tool' && command != null && command.isNotEmpty
                ? command
                : a?['kind'] == 'model'
                    ? 'thinking'
                    : '';
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: Sp.s),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(width: 22, child: Text('▸', style: TextStyle(color: cs.primary))),
              Expanded(
                child: Text(
                  doing.isEmpty ? dots : '$doing $dots',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: (doing.isEmpty || doing == 'thinking' || widget.stopping
                          ? TextStyle(color: cs.primary)
                          : AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurfaceVariant))
                      .copyWith(height: 1.45),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

/// Every step of a turn, in order, with its time: model calls, tool calls
/// and their commands, compactions. Live, the open step counts up; after
/// the reply it folds to one line with the total and opens on a click.
/// What the Deputy did this turn, as a person would say it: "Started a
/// draft", "Added a need: …", "Read the brief". Thinking between calls is
/// not listed (the working line shows it live); a command sits behind
/// its phrase for anyone who wants it. Folded to one line once the turn
/// is over.
class _Steps extends StatefulWidget {
  const _Steps({required this.steps, required this.live});
  final List<DeputyStep> steps;
  final bool live;

  @override
  State<_Steps> createState() => _StepsState();
}

class _StepsState extends State<_Steps> {
  bool open = false;

  @override
  Widget build(BuildContext context) {
    final key = opsKeyStyle(context);
    final steps = widget.steps.where((s) => s.kind != 'model').toList();
    final total = widget.steps.fold<double>(0, (a, s) => a + (s.seconds ?? 0));
    final calls = steps.where((s) => s.kind == 'call').length;
    final memory = steps.where((s) => s.kind == 'memory').length;
    final summary = calls == 0
        ? 'THOUGHT · ${_secs(total)}'
        : '$calls ${calls == 1 ? 'STEP' : 'STEPS'}${memory > 0 ? ' · $memory COMPACTION${memory > 1 ? 'S' : ''}' : ''} · ${_secs(total)}';
    final shown = widget.live || open;
    if (steps.isEmpty && widget.live) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(left: 22, bottom: Sp.s),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!widget.live)
            InkWell(
              onTap: () => setState(() => open = !open),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Text('${open ? '▾' : '▸'} $summary', style: key),
              ),
            ),
          if (shown)
            for (final s in steps) _StepLine(step: s),
        ],
      ),
    );
  }

  static String _secs(double v) => v >= 100 ? '${v.round()} s' : '${v.toStringAsFixed(1)} s';
}

class _StepLine extends StatefulWidget {
  const _StepLine({required this.step});
  final DeputyStep step;

  @override
  State<_StepLine> createState() => _StepLineState();
}

class _StepLineState extends State<_StepLine> {
  bool raw = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final s = widget.step;
    final plain = describeStep(s.kind, s.text);
    final elapsed = s.done ? s.seconds : DateTime.now().difference(s.at).inMilliseconds / 1000;
    final time = elapsed == null ? '' : (elapsed >= 100 ? '${elapsed.round()} s' : '${elapsed.toStringAsFixed(1)} s');
    final mark = !s.done
        ? '…'
        : !s.ok
        ? '×'
        : '✓';
    final said = !s.done ? 'in progress' : !s.ok ? 'failed' : 'done';
    final color = !s.ok
        ? cs.error
        : !s.done
        ? cs.primary
        : cs.onSurfaceVariant;
    final body = theme.textTheme.bodyMedium!.copyWith(color: color, height: 1.4);
    final mono = AppTheme.mono.copyWith(fontSize: 11.5, height: 1.4, color: cs.onSurfaceVariant);
    // A failed step says how; a successful one only what.
    final note = !s.ok && s.detail.isNotEmpty ? s.detail : '';
    return InkWell(
      onTap: plain.raw.isEmpty ? null : () => setState(() => raw = !raw),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(width: 16, child: Semantics(label: said, excludeSemantics: true, child: Text(mark, style: body))),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(plain.what, style: body, maxLines: 2, overflow: TextOverflow.ellipsis),
                  if (note.isNotEmpty) Text(note, style: body.copyWith(color: cs.error), maxLines: 2, overflow: TextOverflow.ellipsis),
                  if (raw && plain.raw.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: SelectableText(plain.raw, style: mono),
                    ),
                ],
              ),
            ),
            const SizedBox(width: Sp.m),
            Text(time, style: opsKeyStyle(context)),
          ],
        ),
      ),
    );
  }
}

/// The line you speak on. Enter sends, Shift+Enter is a newline — the
/// same keys as every inline edit in the app.
class _SayLine extends StatefulWidget {
  const _SayLine({required this.enabled, required this.onSend, required this.onStop, this.stopping = false, this.unsent});
  final bool enabled;
  final bool stopping;
  final ValueChanged<String> onSend;

  /// A line the seat never heard: back in the box, to send again.
  final String? unsent;

  /// While the Deputy works: end the turn at its next step (STOP, or Esc).
  final VoidCallback onStop;

  @override
  State<_SayLine> createState() => _SayLineState();
}

class _SayLineState extends State<_SayLine> {
  final _text = TextEditingController();
  final _focus = FocusNode();

  @override
  void didUpdateWidget(covariant _SayLine old) {
    super.didUpdateWidget(old);
    final back = widget.unsent;
    if (back != null && back != old.unsent && _text.text.trim().isEmpty) {
      _text.text = back;
      _text.selection = TextSelection.collapsed(offset: back.length);
    }
  }

  @override
  void dispose() {
    _text.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _send() {
    final t = _text.text.trim();
    if (t.isEmpty || !widget.enabled) return;
    widget.onSend(t);
    _text.clear();
    _focus.requestFocus();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    // A message box, not a line on a page: contained, rounded, with the
    // send under your thumb. The record above stays plain.
    return Padding(
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.s, Sp.l, Sp.l),
      child: AnimatedBuilder(
        animation: _focus,
        builder: (context, child) => Container(
          decoration: BoxDecoration(
            color: cs.surfaceContainerHigh,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: _focus.hasFocus && widget.enabled ? ink : cs.outlineVariant, width: 1.2),
          ),
          padding: const EdgeInsets.fromLTRB(16, 6, 8, 6),
          child: child,
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: Shortcuts(
                shortcuts: const {
                  SingleActivator(LogicalKeyboardKey.enter): _SendIntent(),
                  SingleActivator(LogicalKeyboardKey.numpadEnter): _SendIntent(),
                  SingleActivator(LogicalKeyboardKey.escape): _StopIntent(),
                },
                child: Actions(
                  actions: {
                    _SendIntent: CallbackAction<_SendIntent>(onInvoke: (_) => _send()),
                    _StopIntent: CallbackAction<_StopIntent>(onInvoke: (_) {
                      if (!widget.enabled) widget.onStop();
                      return null;
                    }),
                  },
                  child: TextField(
                    controller: _text,
                    focusNode: _focus,
                    // Stays focusable while the Deputy works so Esc reaches it;
                    // typing is held until the turn ends.
                    readOnly: !widget.enabled,
                    minLines: 1,
                    maxLines: 8,
                    cursorColor: ink,
                    style: theme.textTheme.bodyLarge!.copyWith(color: ink, height: 1.45),
                    decoration: InputDecoration(
                      isCollapsed: true,
                      contentPadding: const EdgeInsets.symmetric(vertical: 10),
                      border: InputBorder.none,
                      hintText: widget.enabled ? 'Talk to your Deputy' : 'Working — Esc to stop',
                      hintStyle: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(width: Sp.s),
            if (widget.enabled)
              ListenableBuilder(
                listenable: _text,
                builder: (context, _) {
                  final ready = _text.text.trim().isNotEmpty;
                  return IconButton(
                    onPressed: ready ? _send : null,
                    tooltip: 'Send · Enter',
                    icon: const Icon(Icons.arrow_upward, size: 20),
                    style: IconButton.styleFrom(
                      backgroundColor: ready ? ink : cs.surfaceContainerHighest,
                      foregroundColor: ready ? cs.surface : cs.onSurfaceVariant,
                      minimumSize: const Size(36, 36),
                      padding: EdgeInsets.zero,
                    ),
                  );
                },
              )
            else
              IconButton(
                onPressed: widget.stopping ? null : widget.onStop,
                tooltip: widget.stopping ? 'Stopping after this step' : 'Stop · Esc',
                icon: const Icon(Icons.stop, size: 20),
                style: IconButton.styleFrom(
                  backgroundColor: widget.stopping ? cs.surfaceContainerHighest : cs.primary,
                  foregroundColor: widget.stopping ? cs.onSurfaceVariant : cs.surface,
                  minimumSize: const Size(36, 36),
                  padding: EdgeInsets.zero,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _SendIntent extends Intent {
  const _SendIntent();
}

class _StopIntent extends Intent {
  const _StopIntent();
}
