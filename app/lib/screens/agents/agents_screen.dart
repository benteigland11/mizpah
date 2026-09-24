import 'dart:async';

import 'package:flutter/material.dart';

import '../../cg/diff_line_list/diff_line_list.dart';
import '../../cg/syntax_highlighted_code/syntax_highlighted_code.dart';
import '../../engine/engine.dart';
import '../../engine/run_floor.dart';
import '../../models/loop.dart';
import '../../state/agents_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/code_palette.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/document_sheet.dart' show DocStamp, whenLabel;

/// The agents' traces: what the controller and the workers are doing,
/// step by step. Seats on the left — the controller, then each worker by
/// its work order — the chosen seat's transcript on the right, tailing
/// live, newest at the bottom. Checking in at their office, not a
/// dashboard about them.
class AgentsScreen extends StatelessWidget {
  const AgentsScreen({super.key, required this.manager});
  final AgentsManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        final seats = manager.seats;
        final live = seats.where((e) => e.$2.status == 'in_progress').length;
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(
              width: 360,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
                    child: Row(
                      children: [
                        Text('AGENTS', style: opsHeadingStyle(context)),
                        const Spacer(),
                        Text(
                          manager.loading
                              ? 'LOOKING…'
                              : manager.homeWide
                              ? '$live WORKING'
                              : '${seats.length} SEATS · $live WORKING',
                          style: opsLabelStyle(context).copyWith(
                            color: live > 0 ? AppTheme.ink(context) : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  Expanded(child: ListView(children: _seatRows(seats))),
                ],
              ),
            ),
            VerticalDivider(width: 1, color: cs.outlineVariant),
            Expanded(
              child: manager.selected == null
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 48),
                        child: Text(
                          manager.loading
                              ? 'Looking in on the agents…'
                              : manager.homeWide
                              ? 'No agent is working right now. Live tasks show their worker here.'
                              : 'No agent has sat down on this task yet.',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.bodyLarge!.copyWith(
                            color: cs.onSurfaceVariant,
                          ),
                        ),
                      ),
                    )
                  : _Trace(
                      turns: manager.transcript,
                      reading: manager.reading,
                      wire: manager.wire,
                      model: manager.selectedModel,
                      seat: seats
                          .where((e) => (e.$1.id, e.$2.id) == manager.selected)
                          .map((e) => e.$2)
                          .firstOrNull,
                      atBeginning: manager.atBeginning,
                      loadingEarlier: manager.loadingEarlier,
                      onLoadEarlier: manager.loadEarlier,
                      earlierRules: manager.earlierRules,
                      onReachRule: manager.reachRule,
                    ),
            ),
          ],
        );
      },
    );
  }

  /// Seats in order; at Home each task's seats sit under its name once.
  List<Widget> _seatRows(List<(BriefSummary, FloorSession)> seats) {
    final rows = <Widget>[];
    String? lastTask;
    for (final (task, seat) in seats) {
      if (manager.homeWide && task.id != lastTask) {
        rows.add(_TaskLine(task: task));
        lastTask = task.id;
      }
      rows.add(_Seat(
        seat: seat,
        selected: manager.selected == (task.id, seat.id),
        onTap: () => manager.select(task, seat),
      ));
    }
    return rows;
  }
}

class _TaskLine extends StatelessWidget {
  const _TaskLine({required this.task});
  final BriefSummary task;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.m, Sp.l, 2),
    child: Text(
      task.title.toUpperCase(),
      style: opsKeyStyle(context).copyWith(color: AppTheme.ink(context)),
    ),
  );
}

/// One seat: who (controller, or worker on which order), what state, how
/// many turns so far.
class _Seat extends StatelessWidget {
  const _Seat({required this.seat, required this.selected, required this.onTap});
  final FloorSession seat;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final s = seat;
    final stamp = switch (s.status) {
      // A decision in progress works like a worker does: its phase is the live file's.
      'controller' => s.phase.isEmpty ? 'CONTROLLER' : 'WORKING',
      'host' => 'WORKING',
      'in_progress' => 'WORKING',
      'complete' || 'done' => 'GATE GREEN',
      'incomplete' => 'GATE RED',
      'blocked_by_worker' || 'blocked' => 'BLOCKED',
      'stopped' => 'STOPPED',
      _ => s.status.toUpperCase(),
    };
    return InkWell(
      onTap: onTap,
      child: Container(
        decoration: BoxDecoration(
          color: selected ? cs.surfaceContainer : null,
          border: Border(
            left: BorderSide(color: selected ? AppTheme.ink(context) : Colors.transparent, width: 3),
            bottom: BorderSide(color: cs.outlineVariant),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(Sp.xl - 3, Sp.m, Sp.l, Sp.m),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(s.isController ? 'CONTROLLER · DECISION ${s.decision}' : s.isHost ? 'HOST · BETWEEN SEATS' : 'WORKER', style: opsKeyStyle(context)),
                const Spacer(),
                if (stamp == 'WORKING')
                  Text('WORKING', style: opsKeyStyle(context).copyWith(color: AppTheme.ink(context), fontWeight: FontWeight.w700))
                else if (s.isController)
                  Text('${s.turns ?? 0} DRAFT${s.turns == 1 ? '' : 'S'}', style: opsKeyStyle(context))
                else
                  DocStamp(stamp, hot: false, sign: false),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              s.title,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium!.copyWith(
                color: selected ? AppTheme.ink(context) : cs.onSurface,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              s.isController || s.isHost
                  ? [if (s.isHost) 'the loop itself', if (s.startedAt != null) whenLabel(s.startedAt!) else 'latest'].join(' · ')
                  : [s.id, if (s.turns != null) '${s.turns} turns', if (s.startedAt != null) whenLabel(s.startedAt!)].join(' · '),
              style: AppTheme.mono.copyWith(fontSize: 11, color: cs.onSurfaceVariant),
            ),
            // A live worker's phase, in the phase's colour: the route may
            // already have the task while review, a red round or the
            // write-up is still running.
            if (stamp == 'WORKING' && s.phase.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 3),
                child: Text(s.phase.toUpperCase(), style: opsKeyStyle(context).copyWith(color: _phaseColor(cs, s.phase))),
              ),
          ],
        ),
      ),
    );
  }
}

/// The transcript: one line per step, tool calls with their outcome folded
/// under them, the worker's own words as notes, handoffs as rules.
class _Trace extends StatefulWidget {
  const _Trace({
    required this.turns,
    this.reading = false,
    this.wire,
    required this.model,
    required this.seat,
    required this.atBeginning,
    required this.loadingEarlier,
    required this.onLoadEarlier,
    this.earlierRules = const [],
    this.onReachRule,
  });

  /// Rules in the part of the log not loaded yet, and how to load back to one.
  final List<Turn> earlierRules;
  final Future<bool> Function(String key)? onReachRule;
  final List<Turn> turns;
  final bool reading;
  final Wire? wire;
  final String model;
  final FloorSession? seat;
  final bool atBeginning;
  final bool loadingEarlier;
  final Future<void> Function() onLoadEarlier;

  @override
  State<_Trace> createState() => _TraceState();
}

class _TraceState extends State<_Trace> {
  final _scroll = ScrollController();
  final _open = <String>{};
  bool _pinned = true;

  /// The rule rows (handoffs, phase lines, markers), each with a key so a
  /// jump can find it once it is built; and the rule last jumped to, for
  /// the previous/next arrows.
  final _ruleKeys = <String, GlobalKey>{};
  String? _atRule;

  static bool _isRule(Turn t) => t.kind == 'handoff' || t.kind == 'phase' || t.kind == 'milestone';

  List<int> get _rules => [for (final (i, t) in widget.turns.indexed) if (_isRule(t)) i];

  /// Every rule in the session, oldest first: those in the part not loaded
  /// yet, then those in the trace.
  List<Turn> get _allRules {
    final loaded = {for (final t in widget.turns) if (_isRule(t)) t.key};
    return [
      for (final t in widget.earlierRules) if (!loaded.contains(t.key)) t,
      for (final k in _rules) widget.turns[k],
    ];
  }

  /// Go to a rule by key: scroll if it is loaded; else load back to it first.
  Future<void> _goToRule(String key) async {
    var k = widget.turns.indexWhere((t) => t.key == key);
    if (k < 0) {
      _atRule = key;
      if (_pinned) setState(() => _pinned = false);
      final reach = widget.onReachRule;
      if (reach == null || !await reach(key) || !mounted) return;
      // The earlier pages land above and the list holds its place; find the
      // rule in the new transcript once it has been built.
      await WidgetsBinding.instance.endOfFrame;
      if (!mounted) return;
      k = widget.turns.indexWhere((t) => t.key == key);
      if (k < 0) return;
    }
    _jumpToRule(k);
  }

  /// Scroll to the rule at [k]. The list builds rows lazily, so a far row
  /// has no position yet: jump to where it should be by its index, let the
  /// frame build it, then settle on it.
  void _jumpToRule(int k, [int tries = 8]) {
    final t = widget.turns[k];
    _atRule = t.key;
    if (_pinned) setState(() => _pinned = false);
    final ctx = _ruleKeys[t.key]?.currentContext;
    if (ctx != null) {
      Scrollable.ensureVisible(ctx, alignment: 0.15, duration: const Duration(milliseconds: 220), curve: Curves.easeOut);
      return;
    }
    if (tries == 0 || !_scroll.hasClients) return;
    final pos = _scroll.position;
    _scroll.jumpTo((pos.maxScrollExtent * (k + 1) / (widget.turns.length + 1)).clamp(0.0, pos.maxScrollExtent));
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _jumpToRule(k, tries - 1);
    });
  }

  /// The previous (-1) or next (+1) rule from the one last jumped to; with
  /// none yet, the newest (-1) or the oldest (+1).
  void _stepRule(int by) {
    final rules = _allRules;
    if (rules.isEmpty) return;
    final at = _atRule == null ? -1 : rules.indexWhere((t) => t.key == _atRule);
    final next = at < 0 ? (by < 0 ? rules.length - 1 : 0) : (at + by).clamp(0, rules.length - 1);
    _goToRule(rules[next].key);
  }

  @override
  void initState() {
    super.initState();
    _toEnd();
  }

  /// To the foot of the trace. A builder list's extent settles over a
  /// frame or two as rows lay out, so one jump lands short; jump, then
  /// jump again once the layout has caught up.
  void _toEnd([int passes = 3]) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients || !_pinned) return;
      final end = _scroll.position.maxScrollExtent;
      if (_scroll.position.pixels != end) _scroll.jumpTo(end);
      if (passes > 1) _toEnd(passes - 1);
    });
  }

  @override
  void didUpdateWidget(covariant _Trace old) {
    super.didUpdateWidget(old);
    if (old.seat?.id != widget.seat?.id) {
      _open.clear();
      _ruleKeys.clear();
      _atRule = null;
      _pinned = true;
      _toEnd();
    }
    final grew = widget.turns.length != old.turns.length;
    if (!grew) return;
    final prepended = old.turns.isNotEmpty &&
        widget.turns.isNotEmpty &&
        !identical(widget.turns.first, old.turns.first) &&
        widget.turns.length > old.turns.length &&
        old.loadingEarlier && !widget.loadingEarlier;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      if (prepended) {
        // Older steps arrived above: hold the eye where it was.
        final delta = _scroll.position.maxScrollExtent - _extentBefore;
        _scroll.jumpTo(_scroll.position.pixels + delta);
      } else if (_pinned) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
        _toEnd(2);
      }
    });
  }

  double _extentBefore = 0;

  void _maybeLoadEarlier() {
    if (!_scroll.hasClients || widget.atBeginning || widget.loadingEarlier) return;
    if (_scroll.position.pixels <= 80) {
      _extentBefore = _scroll.position.maxScrollExtent;
      widget.onLoadEarlier();
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final turns = widget.turns;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
          child: Row(
            children: [
              Text(
                (widget.seat?.isController ?? false) ? 'DECISIONS' : 'TRACE',
                style: opsLabelStyle(context),
              ),
              if (widget.model.isNotEmpty) ...[
                const SizedBox(width: Sp.m),
                Text(
                  widget.model,
                  style: AppTheme.mono.copyWith(fontSize: 12, color: AppTheme.ink(context), fontWeight: FontWeight.w700),
                ),
              ],
              const SizedBox(width: Sp.m),
              Text(
                turns.isEmpty ? '' : '${turns.length} steps · to turn ${turns.last.n}',
                style: opsKeyStyle(context),
              ),
              const Spacer(),
              if (_allRules.isNotEmpty) ...[
                _RuleMenu(
                  rules: _allRules,
                  current: _atRule,
                  onPick: _goToRule,
                ),
                IconButton(
                  icon: const Icon(Icons.keyboard_arrow_up, size: 16),
                  tooltip: 'Previous event',
                  visualDensity: VisualDensity.compact,
                  onPressed: () => _stepRule(-1),
                ),
                IconButton(
                  icon: const Icon(Icons.keyboard_arrow_down, size: 16),
                  tooltip: 'Next event',
                  visualDensity: VisualDensity.compact,
                  onPressed: () => _stepRule(1),
                ),
                const SizedBox(width: Sp.m),
              ],
              InkWell(
                onTap: () => setState(() => _pinned = !_pinned),
                child: Text(
                  _pinned ? 'FOLLOWING' : 'FOLLOW',
                  style: opsKeyStyle(context).copyWith(
                    color: _pinned ? AppTheme.ink(context) : null,
                    fontWeight: _pinned ? FontWeight.w700 : null,
                  ),
                ),
              ),
            ],
          ),
        ),
        Divider(height: 1, color: cs.outlineVariant),
        Expanded(
          child: turns.isEmpty
              ? Center(
                  child: Text(widget.reading ? 'Reading the trace…' : 'Nothing yet.', style: Theme.of(context).textTheme.bodyMedium!.copyWith(color: cs.onSurfaceVariant)),
                )
              : NotificationListener<ScrollNotification>(
                  onNotification: (n) {
                    if (n is ScrollUpdateNotification && _scroll.hasClients) {
                      final atEnd = _scroll.position.pixels >= _scroll.position.maxScrollExtent - 24;
                      if (_pinned != atEnd) setState(() => _pinned = atEnd);
                      _maybeLoadEarlier();
                    }
                    return false;
                  },
                  child: ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.s, Sp.xl, Sp.xl),
                    itemCount: turns.length + 1,
                    itemBuilder: (context, i) {
                      if (i == 0) return _EarlierLine(atBeginning: widget.atBeginning, loading: widget.loadingEarlier, onTap: widget.onLoadEarlier, start: (widget.seat?.isController ?? false) ? 'START OF DECISION' : (widget.seat?.isHost ?? false) ? 'BETWEEN SEATS' : 'START OF WORK ORDER');
                      final k = i - 1;
                      // The phase this row is in: the last phase line above it.
                      // A reflection ends at its handoff: below that the
                      // worker is back in the phase it was in before.
                      String phase = '', phaseTone = '';
                      var rolled = false;
                      for (var j = k; j >= 0; j--) {
                        if (turns[j].kind == 'handoff') rolled = true;
                        if (turns[j].kind == 'phase') {
                          if (rolled && turns[j].title.startsWith('reflection')) continue;
                          phase = turns[j].title;
                          phaseTone = turns[j].tone;
                          break;
                        }
                      }
                      if (turns[k].kind == 'live') {
                        return turns[k].title == 'controller'
                            ? _ControllerLiveRow(since: turns[k].at, phase: turns[k].body)
                            : turns[k].title == 'host'
                            ? _ControllerLiveRow(since: turns[k].at, phase: turns[k].body, who: 'host')
                            : turns[k].title == 'reviewer'
                            ? const _ReviewingRow()
                            : turns[k].title == 'compacting'
                            ? _ControllerLiveRow(since: turns[k].at, phase: 'compacting — writing the handoff', who: 'worker')
                            : _LiveRow(since: turns[k].at, wire: widget.wire);
                      }
                      final step = _Step(
                        turn: turns[k],
                        phase: phase,
                        phaseTone: phaseTone,
                        controller: widget.seat?.isController ?? false,
                        showTurn: k == 0 || turns[k - 1].n != turns[k].n,
                        open: _open.contains(turns[k].key),
                        onTap: () => setState(() {
                          final h = turns[k].key;
                          _open.contains(h) ? _open.remove(h) : _open.add(h);
                        }),
                      );
                      return _isRule(turns[k])
                          ? KeyedSubtree(key: _ruleKeys.putIfAbsent(turns[k].key, GlobalKey.new), child: step)
                          : step;
                    },
                  ),
                ),
        ),
      ],
    );
  }
}

/// The reviewer's call is out: one word and a pulse. Its reply is the
/// review row that follows; nothing said while it is out stays true.
class _ReviewingRow extends StatefulWidget {
  const _ReviewingRow();

  @override
  State<_ReviewingRow> createState() => _ReviewingRowState();
}

class _ReviewingRowState extends State<_ReviewingRow> {
  Timer? _tick;
  int _dots = 1;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(milliseconds: 450), (_) => setState(() => _dots = _dots % 3 + 1));
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.onSurfaceVariant;
    final style = AppTheme.mono.copyWith(fontSize: 12.5, height: 1.45, color: color);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          Icon(Icons.fact_check_outlined, size: 14, color: color),
          const SizedBox(width: Sp.m),
          Text('Reviewing${'.' * _dots}', style: style),
        ],
      ),
    );
  }
}

/// A worker call out with no reply on the log: a clock, not a sentence. It
/// ticks on its own once a second against the call's time and, when the
/// window's wire says bytes are arriving, against the last of them —
/// replying, or silent, and for how long. Anything worded at read time was
/// stale before the next poll.
class _LiveRow extends StatefulWidget {
  const _LiveRow({required this.since, required this.wire});
  final DateTime since;
  final Wire? wire;

  @override
  State<_LiveRow> createState() => _LiveRowState();
}

class _LiveRowState extends State<_LiveRow> {
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => setState(() {}));
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  static String _clock(Duration d) {
    final s = d.inSeconds < 0 ? 0 : d.inSeconds;
    return s < 60 ? '${s}s' : '${s ~/ 60}m ${(s % 60).toString().padLeft(2, '0')}s';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final now = DateTime.now().toUtc();
    final out = now.difference(widget.since.toUtc());
    // Bytes from this call, not a previous one: the file outlives the reply it reported.
    final w = widget.wire;
    final replying = w != null && !w.at.isBefore(widget.since.toUtc().subtract(const Duration(seconds: 2)));
    final quiet = replying ? now.difference(w.at) : out;
    final silent = quiet.inSeconds >= 20;
    // Three things, told apart: the model thinking or writing (bytes still
    // coming, and which channel they are on — the SSE event names say),
    // bytes that stopped, and no bytes at all. Before the first byte the
    // model is thinking; the proxy answers within seconds, so nothing for
    // 45 s is nothing from them.
    final (String state, Color color, IconData icon) = !replying
        ? (out.inSeconds >= 45 ? 'nothing from them' : 'thinking', out.inSeconds >= 45 ? cs.error : cs.onSurfaceVariant, out.inSeconds >= 45 ? Icons.cloud_off_outlined : Icons.hourglass_empty)
        : silent
        ? ('silent', cs.error, Icons.cloud_off_outlined)
        // Healthy states in ink: the accent is red here, and "replying" in
        // the accent read as an alarm.
        : w.doing == 'thinking'
        ? ('thinking', cs.onSurfaceVariant, Icons.psychology_outlined)
        : w.doing == 'writing'
        ? ('writing', AppTheme.ink(context), Icons.edit_outlined)
        : ('replying', AppTheme.ink(context), Icons.downloading);
    final style = AppTheme.mono.copyWith(fontSize: 12.5, height: 1.45, color: color);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(padding: const EdgeInsets.only(top: 2), child: Icon(icon, size: 14, color: color)),
          const SizedBox(width: Sp.m),
          Expanded(
            child: Text(
              // state, then how long this turn has run (implied), then the wire.
              replying
                  ? 'worker · $state ${_clock(out)} · last byte ${_clock(quiet)} ago'
                  : 'worker · $state ${_clock(out)}',
              style: style,
            ),
          ),
        ],
      ),
    );
  }
}

/// The controller's step in progress: what it is doing (reading, or a call
/// out to the model) and a clock on it. No wire here — the step's model calls
/// are not streamed — and no alarm at 45 s: a local model's first step reads
/// a 27K prompt and thinks for minutes.
class _ControllerLiveRow extends StatefulWidget {
  const _ControllerLiveRow({required this.since, required this.phase, this.who = 'controller'});
  final DateTime since;
  final String phase;

  /// Whose step it is: the controller's, or a worker's compaction.
  final String who;

  @override
  State<_ControllerLiveRow> createState() => _ControllerLiveRowState();
}

class _ControllerLiveRowState extends State<_ControllerLiveRow> {
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => setState(() {}));
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final out = DateTime.now().toUtc().difference(widget.since.toUtc());
    final calling = widget.phase.startsWith('calling');
    final color = calling ? cs.onSurfaceVariant : AppTheme.ink(context);
    final style = AppTheme.mono.copyWith(fontSize: 12.5, height: 1.45, color: color);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(calling ? Icons.psychology_outlined : Icons.menu_book_outlined, size: 14, color: color),
          ),
          const SizedBox(width: Sp.m),
          Expanded(child: Text('${widget.who} · ${widget.phase} ${_LiveRowState._clock(out)}', style: style)),
        ],
      ),
    );
  }
}

/// The RULES menu in the trace's bar: every rule in the session, by turn,
/// in its colour; picking one scrolls there (loading back to it first when
/// it is in the part of the log not read yet).
class _RuleMenu extends StatelessWidget {
  const _RuleMenu({required this.rules, required this.current, required this.onPick});
  final List<Turn> rules;
  final String? current;
  final ValueChanged<String> onPick;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return PopupMenuButton<String>(
      tooltip: 'Go to an event',
      onSelected: onPick,
      position: PopupMenuPosition.under,
      constraints: const BoxConstraints(maxHeight: 480, maxWidth: 560),
      itemBuilder: (context) => [
        for (final t in rules)
          PopupMenuItem<String>(
            value: t.key,
            height: 34,
            child: Row(
              children: [
                SizedBox(width: 64, child: Text('TURN ${t.n}', style: opsKeyStyle(context))),
                Container(width: 3, height: 14, color: _ruleColor(cs, t)),
                const SizedBox(width: Sp.s),
                Flexible(
                  child: Text(
                    t.title.toUpperCase(),
                    overflow: TextOverflow.ellipsis,
                    style: opsKeyStyle(context).copyWith(
                      color: _ruleColor(cs, t),
                      fontWeight: t.key == current ? FontWeight.w700 : null,
                    ),
                  ),
                ),
              ],
            ),
          ),
      ],
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: Sp.s, vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('EVENTS · ${rules.length}', style: opsKeyStyle(context)),
            const Icon(Icons.arrow_drop_down, size: 16),
          ],
        ),
      ),
    );
  }
}

/// A rule row's colour: its own tone, else its kind's.
Color _ruleColor(ColorScheme cs, Turn t) => t.tone.isNotEmpty
    ? _toneColor(cs, t.tone)
    : t.kind == 'handoff'
    ? cs.error
    : t.kind == 'milestone'
    ? _milestoneColor(cs, t.title)
    : _phaseColor(cs, t.title);

/// A rule's colour by name, as the run's reader assigns it
/// (the trace-rules page): system cyan, pass, hold, crimson, rose.
Color _toneColor(ColorScheme cs, String tone) => switch (tone) {
  'system' => AppTheme.system(cs.brightness),
  'pass' => AppTheme.pass(cs.brightness),
  'hold' => AppTheme.hold(cs.brightness),
  'crimson' => AppTheme.crimson(cs.brightness),
  'rose' => AppTheme.rose(cs.brightness),
  'error' => cs.error,
  'accent' => cs.primary,
  'tertiary' => cs.tertiary,
  _ => cs.onSurfaceVariant,
};

/// The colour of a phase by its words — for the seat's phase line, which
/// carries no tone: gate red in crimson, gate green in green, a reflection
/// in yellow, the system (a resume, a message relayed) in cyan.
Color _phaseColor(ColorScheme cs, String phase) {
  final p = phase.toLowerCase();
  if (p.startsWith('gate red')) return AppTheme.crimson(cs.brightness);
  if (p.startsWith('gate green')) return AppTheme.pass(cs.brightness);
  if (p.startsWith('reflect')) return AppTheme.hold(cs.brightness);
  if (p.startsWith('resumed') || p.startsWith('requestor') || p.startsWith('compacting')) return AppTheme.system(cs.brightness);
  if (p.contains('sent back by the reviewer')) return cs.error;
  if (p.startsWith('waiting')) return cs.outline;
  // A claim is not an ending: amber until the reviewer accepts it.
  if (p.startsWith('claimed done')) return AppTheme.hold(cs.brightness);
  return cs.onSurfaceVariant;
}

/// The colour of a milestone. `task done` is a claim, not an ending — the
/// reviewer has still to accept it — so it reads amber: stop and check.
Color _milestoneColor(ColorScheme cs, String title) =>
    title.toLowerCase().startsWith('task done') ? AppTheme.hold(cs.brightness) : cs.primary;

class _Step extends StatelessWidget {
  const _Step({
    required this.turn,
    required this.showTurn,
    required this.open,
    required this.onTap,
    this.controller = false,
    this.phase = '',
    this.phaseTone = '',
  });
  final Turn turn;
  final bool showTurn;

  /// The colour name of the phase line this row falls under, when it has one.
  final String phaseTone;

  /// The host phase this row falls in ('' before any): gate red, the
  /// write-up after green, a merge. Tints the row so the phase reads at a
  /// glance.
  final String phase;

  /// A controller seat counts attempts, not turns.
  final bool controller;
  final bool open;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final t = turn;
    final ink = AppTheme.ink(context);
    final mono = AppTheme.mono.copyWith(fontSize: 12.5, color: cs.onSurface, height: 1.45);
    final (icon, color) = switch (t.kind) {
      'tool' => (t.ok ? Icons.terminal : Icons.block, t.ok ? cs.onSurfaceVariant : cs.primary),
      'thought' => (Icons.psychology_outlined, cs.onSurfaceVariant),
      'prompt' => (Icons.mail_outline, cs.onSurfaceVariant),
      'note' => (Icons.notes, t.ok ? ink : cs.error),
      'handoff' => (Icons.swap_horiz, cs.error),
      'checkin' => (Icons.record_voice_over_outlined, cs.error),
      'review' => (Icons.fact_check_outlined, t.ok ? cs.onSurfaceVariant : cs.error),
      'milestone' => (Icons.flag_outlined, _milestoneColor(cs, t.title)),
      'outage' => (Icons.cloud_off_outlined, cs.error),
      'phase' => (Icons.label_outline, _phaseColor(cs, t.title)),
      'step' => (Icons.gavel, t.ok ? ink : cs.primary),
      _ => (Icons.circle, cs.onSurfaceVariant),
    };
    // A rule drawn with its own colour (the run's reader names it) wins over the kind's.
    final ruleColor = t.tone.isNotEmpty ? _toneColor(cs, t.tone) : color;
    final hasBody = t.body.trim().isNotEmpty && t.body.trim() != t.title.trim() || t.before != null || t.after != null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (showTurn && t.kind != 'step')
          Padding(
            padding: const EdgeInsets.fromLTRB(0, Sp.l, 0, 4),
            child: Row(
              children: [
                Text(controller ? 'ATTEMPT ${t.n}' : 'TURN ${t.n}', style: opsKeyStyle(context)),
                const SizedBox(width: Sp.m),
                Expanded(child: Divider(color: cs.outlineVariant)),
                const SizedBox(width: Sp.m),
                Text(whenLabel(t.at), style: opsKeyStyle(context)),
              ],
            ),
          ),
        if (t.kind == 'handoff' || t.kind == 'milestone' || t.kind == 'phase')
          Padding(
            padding: const EdgeInsets.symmetric(vertical: Sp.s),
            // Where the words sit: centred, or near an edge with a stub of
            // line before (left) or after (right) them.
            // The label takes its own width, capped at 70% of the row so a long
            // one ellipses on a phone; the lines take all the rest. (A flex
            // slot for the label reserved 3/5 of an ultra-wide row and cut the
            // line short.)
            child: LayoutBuilder(
              builder: (context, box) => Row(
                children: [
                  if (t.anchor == 'left') SizedBox(width: 20, child: Divider(color: ruleColor)) else Expanded(child: Divider(color: ruleColor)),
                  ConstrainedBox(
                    constraints: BoxConstraints(maxWidth: box.maxWidth * 0.7),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: Sp.m),
                      child: Text(t.title.toUpperCase(), maxLines: 1, overflow: TextOverflow.ellipsis,
                          style: opsKeyStyle(context).copyWith(color: ruleColor)),
                    ),
                  ),
                  if (t.anchor == 'right') SizedBox(width: 20, child: Divider(color: ruleColor)) else Expanded(child: Divider(color: ruleColor)),
                ],
              ),
            ),
          ),
        // A handoff's row holds the handoff: not there until it is written
        // (the rule goes up when the prompt for it is sent).
        if (!(t.kind == 'handoff' && t.body.trim().isEmpty))
        InkWell(
          onTap: hasBody ? onTap : null,
          child: Container(
            color: phase.isEmpty || t.kind == 'phase'
                ? null
                : (phaseTone.isNotEmpty ? _toneColor(cs, phaseTone) : _phaseColor(cs, phase)).withValues(alpha: 0.07),
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Icon(icon, size: 14, color: ruleColor),
                ),
                const SizedBox(width: Sp.m),
                Expanded(
                  child: Text(
                    // A phase line carries the host's words under its label: the gate-red
                    // list of what is missing is the reason the round happened.
                    // A handoff previews its own words (headings' #s dropped), whole when opened.
                    t.kind == 'handoff'
                        ? t.body.trim().replaceAll(RegExp(r'^#+\s*', multiLine: true), '')
                        : t.kind == 'milestone' || t.kind == 'phase' ? t.body : t.title,
                    maxLines: open ? null : 2,
                    overflow: open ? null : TextOverflow.ellipsis,
                    style: t.kind == 'thought' || t.kind == 'prompt'
                        ? theme.textTheme.bodyMedium!.copyWith(
                            color: cs.onSurfaceVariant, fontStyle: FontStyle.italic, height: 1.4)
                        : t.kind == 'note' || t.kind == 'step'
                        ? theme.textTheme.bodyMedium!.copyWith(color: t.ok ? ink : cs.primary, height: 1.4)
                        : t.kind == 'review'
                        ? theme.textTheme.bodyMedium!.copyWith(color: t.ok ? cs.onSurfaceVariant : cs.error, height: 1.4)
                        : mono.copyWith(color: t.ok ? cs.onSurface : cs.primary),
                  ),
                ),
                if (hasBody)
                  Icon(open ? Icons.expand_less : Icons.expand_more, size: 14, color: cs.onSurfaceVariant),
              ],
            ),
          ),
        ),
        if (open && hasBody && t.kind != 'milestone' && t.kind != 'phase' && t.kind != 'handoff')
          Container(
            margin: const EdgeInsets.fromLTRB(26, 0, 0, Sp.s),
            padding: const EdgeInsets.fromLTRB(Sp.m, Sp.s, Sp.m, Sp.s),
            decoration: BoxDecoration(
              color: cs.surfaceContainer,
              border: Border(left: BorderSide(color: cs.outline, width: 2)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // An edit is a change: draw it as one. A write is all new lines.
                if (t.before != null && t.after != null)
                  ReplaceDiff(before: t.before!, after: t.after!, style: _traceDiff(context))
                else if (t.after != null)
                  for (final (i, l) in t.after!.split('\n').indexed)
                    DiffLine.added(l, style: _traceDiff(context), index: i),
                if (t.body.trim().isNotEmpty) ...[
                  if (t.before != null || t.after != null) const SizedBox(height: Sp.s),
                  if (t.kind == 'thought' || t.kind == 'prompt')
                    SelectableText(
                      _clip(t.body),
                      style: theme.textTheme.bodyMedium!.copyWith(color: cs.onSurface, height: 1.45),
                    )
                  else
                    _CodeBody(turn: t, style: mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant)),
                ],
              ],
            ),
          ),
      ],
    );
  }
}

/// A long body cut to what the trace can hold.
String _clip(String s) => s.length > 6000 ? '${s.substring(0, 6000)}\n…' : s;

/// A step's detail as code: the call (its command, arguments or written
/// file) and, after the arrow, what came back — each coloured in its own
/// language, from the reader's hint or a look at the text.
class _CodeBody extends StatelessWidget {
  const _CodeBody({required this.turn, required this.style});
  final Turn turn;
  final TextStyle style;

  static final _arrow = RegExp(r'(^|\n)→ ');

  @override
  Widget build(BuildContext context) {
    final body = turn.body;
    final m = _arrow.firstMatch(body);
    final call = (m == null ? body : body.substring(0, m.start)).trim();
    final result = m == null ? '' : body.substring(m.end).trimRight();
    final palette = codePalette(context);
    Widget code(String text, String? lang) => HighlightedCode(
          text: _clip(text),
          language: lang ?? guessLanguage(text),
          palette: palette,
          style: style,
        );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (call.isNotEmpty) code(call, turn.lang),
        if (result.isNotEmpty)
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('→ ', style: style),
              Expanded(child: code(result, turn.resultLang)),
            ],
          ),
      ],
    );
  }
}

/// The line above the oldest step you hold: earlier steps load when you
/// scroll to it, or on a click.
class _EarlierLine extends StatelessWidget {
  const _EarlierLine({required this.atBeginning, required this.loading, required this.onTap, this.start = 'START OF WORK ORDER'});
  final bool atBeginning;
  /// What the top of this trace is: a worker's work order, or one controller decision.
  final String start;
  final bool loading;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: Sp.m),
      child: Center(
        child: atBeginning
            ? Text(start, style: opsKeyStyle(context))
            : InkWell(
                onTap: loading ? null : onTap,
                child: Text(
                  loading ? 'LOADING EARLIER…' : 'SCROLL UP OR CLICK FOR EARLIER STEPS',
                  style: opsKeyStyle(context).copyWith(color: loading ? cs.onSurfaceVariant : AppTheme.ink(context)),
                ),
              ),
      ),
    );
  }
}

/// The diff style at trace size: smaller than the memo page's.
DiffStyle _traceDiff(BuildContext context) {
  final base = diffStyle(context);
  return DiffStyle(
    textStyle: AppTheme.mono.copyWith(fontSize: 12, color: AppTheme.ink(context), height: 1.4),
    contextColor: base.contextColor,
    addedColor: base.addedColor,
    removedColor: base.removedColor,
    markStyle: AppTheme.mono.copyWith(fontSize: 12, fontWeight: FontWeight.w700),
    indexStyle: AppTheme.mono.copyWith(fontSize: 11, color: base.contextColor),
    trailingStyle: base.trailingStyle,
  );
}
