import 'dart:async';

import '../models/loop.dart';

/// A scripted loop so the LOOP surface can be designed before the engine
/// streams a real one. One worker ticks a turn a second, gets a check-in
/// every 20 turns, goes green near its budget and completes; one sits
/// blocked on budget; the controller evals after the completion.
class FakeLoop {
  FakeLoop() {
    _emit();
  }

  final _controller = StreamController<LoopState>.broadcast();
  Timer? _timer;
  LoopMode _mode = LoopMode.hold;
  int _turns = 31;
  int _tasksRun = 2;
  bool _done = false;
  final _recent = <ControllerAction>[
    ControllerAction(
      kind: 'eval',
      at: DateTime.now().subtract(const Duration(minutes: 2)),
      summary: 'minted 1 unknown, 1 task · nothing proposed',
    ),
    ControllerAction(
      kind: 'checkin',
      at: DateTime.now().subtract(const Duration(minutes: 4)),
      summary: 'measure_latency at turn 20 · held (aligned)',
    ),
    ControllerAction(
      kind: 'route',
      at: DateTime.now().subtract(const Duration(minutes: 9)),
      summary: 'minted 2 unknowns, 2 tasks',
      refused: [
        'unknown latency_p95: quantity must be a snake_case measure name',
      ],
    ),
  ];

  Stream<LoopState> get stream => _controller.stream;

  final _transcripts = StreamController<(String, List<Turn>)>.broadcast();

  Stream<List<Turn>> transcript(String session) async* {
    yield _turnsFor(session);
    await for (final (s, turns) in _transcripts.stream) {
      if (s == session) yield turns;
    }
  }

  List<Turn> _turnsFor(String session) {
    final now = DateTime.now();
    DateTime t(int secondsAgo) => now.subtract(Duration(seconds: secondsAgo));
    switch (session) {
      case 'controller':
        return [
          for (final (i, a) in _recent.reversed.indexed)
            Turn(
              n: i + 1,
              kind: 'step',
              at: a.at,
              title: '${a.kind}  ·  ${a.summary}',
              body: a.refused.isEmpty
                  ? '{"unknowns": [], "tasks": [], "proposals": [], "why": "…"}'
                  : 'refused:\n  - ${a.refused.join('\n  - ')}',
              ok: a.refused.isEmpty,
            ),
        ];
      case 'measure_latency':
        final base = <Turn>[
          Turn(
            n: 1,
            kind: 'tool',
            at: t(1800),
            title: 'bash  playbook search "p95 latency"',
            body: 'measure-latency-percentile  (0.82)\nmeasure-mean-from-file  (0.41)',
          ),
          Turn(
            n: 2,
            kind: 'tool',
            at: t(1740),
            title: 'bash  playbook start measure-latency-percentile --title "Create the probe"',
            body: 'step 1/4: terra probe create <id> --purpose … --kind run',
          ),
          Turn(
            n: 3,
            kind: 'tool',
            at: t(1700),
            title: 'bash  terra probe create latency_p95 --purpose "p95 latency on fixture" --kind run',
            body: '{"status":"success","data":{"id":"latency_p95","path":".terra/map/probes/latency_p95/probe.py"}}',
          ),
          Turn(
            n: 4,
            kind: 'tool',
            at: t(1650),
            title: 'read  .terra/map/probes/latency_p95/probe.py 1-40',
          ),
          Turn(
            n: 5,
            kind: 'tool',
            at: t(1600),
            title: 'edit  probe.py  raise NotImplementedError → return {"latency_p95": p95}',
            body: 'edit applied (1 match)',
          ),
          Turn(
            n: 6,
            kind: 'tool',
            at: t(1560),
            title: 'bash  terra probe validate latency_p95',
            body: '{"status":"error","error":"measure() returned no numeric latency_p95"}',
            ok: false,
          ),
          Turn(
            n: 7,
            kind: 'tool',
            at: t(1500),
            title: 'edit  probe.py  add helper percentile()',
            body: 'edit applied (1 match)',
          ),
          Turn(
            n: 8,
            kind: 'tool',
            at: t(1450),
            title: 'bash  terra probe validate latency_p95',
            body: '{"status":"success","data":{"validated":true}}',
          ),
          Turn(
            n: 20,
            kind: 'checkin',
            at: t(900),
            title: 'CHECK-IN  turn 20 — held (aligned)',
            body: 'Probe measures the quantity the unknown names; reading builds toward need:1. No correction.',
          ),
          Turn(
            n: 21,
            kind: 'tool',
            at: t(880),
            title: 'bash  terra probe run latency_p95 --to \'{"fixture":"data.txt"}\'',
            body: '{"status":"success","data":{"run_id":"r_0k3m","measures":{"latency_p95":212.4}}}',
          ),
          Turn(
            n: 22,
            kind: 'tool',
            at: t(840),
            title: 'bash  terra unknown link-run latency_p95 r_0k3m',
            body: '{"status":"success","data":{"n":1}}',
          ),
        ];
        final extra = <Turn>[
          for (var n = 23; n <= _turns; n++)
            if (n == 40)
              Turn(
                n: 40,
                kind: 'checkin',
                at: t((_turns - n) * 1),
                title: 'CHECK-IN  turn 40 — held (aligned)',
                body: 'Runs accumulate on the named quantity. No correction.',
              )
            else if (n == 41)
              Turn(
                n: 41,
                kind: 'handoff',
                at: t((_turns - n) * 1),
                title: 'HANDOFF  context rolled over at 60k tokens',
                body: 'Current objective: promote latency_p95 to med and adopt.\nEstablished: probe validated; runs r_0k3m, r_1p7q, r_2ab2 linked.\nUnfinished: promote, adopt, route complete.',
              )
            else
              Turn(
                n: n,
                kind: 'tool',
                at: t((_turns - n) * 1),
                title: n % 3 == 0
                    ? 'bash  terra probe run latency_p95 --to \'{"fixture":"data.txt"}\''
                    : n % 3 == 1
                    ? 'bash  terra unknown link-run latency_p95 r_${n}xq'
                    : 'read  .tool-output/run-$n.txt 1-20',
                body: n % 3 == 0
                    ? '{"status":"success","data":{"run_id":"r_${n}xq"}}'
                    : '',
              ),
        ];
        return [...base, ...extra];
      case 'size_dataset':
        return [
          Turn(
            n: 1,
            kind: 'tool',
            at: t(4000),
            title: 'bash  playbook search "count rows"',
            body: 'count-rows-in-file  (0.91)',
          ),
          Turn(
            n: 2,
            kind: 'tool',
            at: t(3900),
            title: 'bash  terra probe create dataset_rows --purpose "rows in fixture" --kind run',
          ),
          Turn(
            n: 9,
            kind: 'tool',
            at: t(3500),
            title: 'bash  terra probe run dataset_rows --to \'{"fixture":"data.txt"}\'',
            body: '{"status":"success","data":{"run_id":"r_9a1b","measures":{"dataset_rows":1024}}}',
          ),
          Turn(
            n: 20,
            kind: 'checkin',
            at: t(3000),
            title: 'CHECK-IN  turn 20 — held (aligned)',
          ),
          Turn(
            n: 21,
            kind: 'tool',
            at: t(2950),
            title: 'bash  terra known adopt dataset_rows --from t_size_dataset',
            body: '{"status":"error","error":"refused: confidence low (n=1); med needs n>=3"}',
            ok: false,
          ),
          Turn(
            n: 21,
            kind: 'note',
            at: t(2940),
            title: 'BUDGET EXHAUSTED  21 / 21 turns — task blocked',
            body: 'worker budget exhausted at 21 turns; gate: known dataset_rows below med bar (n=1)',
            ok: false,
          ),
        ];
      default:
        return [
          Turn(
            n: 1,
            kind: 'note',
            at: t(9000),
            title: 'Session archived',
            body: 'Transcript in root/tasks/$session/.session-history',
          ),
        ];
    }
  }

  void setMode(LoopMode mode) {
    _mode = mode;
    if (mode == LoopMode.run && !_done) {
      _timer ??= Timer.periodic(const Duration(seconds: 1), (_) => _tick());
    } else {
      _timer?.cancel();
      _timer = null;
    }
    _emit();
  }

  void _tick() {
    _turns++;
    if (_turns % 20 == 0) {
      _recent.insert(
        0,
        ControllerAction(
          kind: 'checkin',
          at: DateTime.now(),
          summary: 'measure_latency at turn $_turns · held (aligned)',
        ),
      );
    }
    if (_turns >= 58) {
      _done = true;
      _tasksRun++;
      _recent.insert(
        0,
        ControllerAction(
          kind: 'eval',
          at: DateTime.now(),
          summary: 'rebucketed size_dataset → high · minted 0',
        ),
      );
      _timer?.cancel();
      _timer = null;
    }
    _emit();
  }

  void _emit() {
    _transcripts.add(('measure_latency', _turnsFor('measure_latency')));
    _transcripts.add(('controller', _turnsFor('controller')));
    final w1 = WorkerSession(
      taskId: 'measure_latency',
      taskTitle: 'Measure p95 request latency on the fixture',
      unknownId: 'latency_p95',
      bucket: 'medium',
      status: _done ? 'done' : 'in_progress',
      turns: _turns,
      budgetTurns: 60,
      handoffs: _turns > 40 ? 1 : 0,
      gateOk: _done || _turns > 52,
      gateProblems: _done || _turns > 52
          ? const []
          : const ['known latency_p95 not adopted', 'route task not complete'],
      note: _done
          ? 'complete · known latency_p95 adopted at med · method written to playbook'
          : _turns > 52
          ? 'gate green · writing method to playbook'
          : _turns > 44
          ? 'probe validated · 3 runs · known at low, promoting'
          : 'probe validated · 1 run · known not yet adopted',
      lastCheckinTurn: (_turns ~/ 20) * 20,
      lastCheckinVerdict: 'held',
    );
    const w2 = WorkerSession(
      taskId: 'size_dataset',
      taskTitle: 'Count rows in the fixture dataset',
      unknownId: 'dataset_rows',
      bucket: 'low',
      status: 'blocked',
      turns: 21,
      budgetTurns: 21,
      handoffs: 0,
      gateOk: false,
      gateProblems: ['known dataset_rows below med bar (n=1)'],
      note: 'awaiting controller rebucket',
      lastCheckinTurn: 20,
      lastCheckinVerdict: 'held',
      blockedReason: 'worker budget exhausted at 21 turns; gate: known dataset_rows below med bar (n=1)',
    );
    const done = [
      WorkerSession(
        taskId: 'mean_of_file',
        taskTitle: 'Mean of data.txt',
        unknownId: 'data_mean',
        bucket: 'medium',
        status: 'done',
        turns: 46,
        budgetTurns: 60,
        handoffs: 1,
        gateOk: true,
        gateProblems: [],
        note: 'complete',
      ),
      WorkerSession(
        taskId: 'count_rows',
        taskTitle: 'Row count of data.txt',
        unknownId: 'data_rows',
        bucket: 'low',
        status: 'done',
        turns: 19,
        budgetTurns: 21,
        handoffs: 0,
        gateOk: true,
        gateProblems: [],
        note: 'complete',
      ),
    ];
    _controller.add(
      LoopState(
        mode: _mode,
        running: _timer != null,
        cycle: 3,
        tasksRun: _tasksRun,
        maxTasks: 8,
        maxCycles: 6,
        stopReason: _mode == LoopMode.hold
            ? 'held'
            : (_done ? 'blocked' : null),
        controller: ControllerState(
          model: 'gemma-4-26b',
          endpoint: '127.0.0.1:8080',
          recent: List.unmodifiable(_recent),
          next: _done
              ? 'route, when size_dataset resumes'
              : 'eval, after measure_latency completes',
          heldGuidance: null,
        ),
        workers: [w1, w2, ...done],
      ),
    );
  }

  void dispose() {
    _timer?.cancel();
    _controller.close();
    _transcripts.close();
  }
}
