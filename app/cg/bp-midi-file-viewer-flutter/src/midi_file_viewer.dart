/// Open a Standard MIDI File and see all of it.
///
/// ```dart
/// MidiFileViewer(bytes: await file.readAsBytes());
/// ```
///
/// Reads the file, names each part by its General MIDI instrument (kit
/// pieces on channel 10), and lays out a piano roll, a drum grid, and lanes
/// for tempo, sustain pedal, velocity, pitch bend and every controller that
/// moves, all on one zoom/pan viewport. A parts panel shows and hides parts;
/// a header gives a one-line summary; the time axis switches between
/// bars/beats and seconds (following tempo changes). There is no playback.
///
/// [MidiOverview] gives the parts and summary without the view.
library;

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/widgets.dart';
import 'package:general_midi_names/general_midi_names.dart';
import 'package:piano_roll/piano_roll.dart';
import 'package:standard_midi_file_reader/standard_midi_file_reader.dart';
import 'package:timeline_lanes/timeline_lanes.dart';

export 'package:piano_roll/piano_roll.dart' show PianoRollStyle;
export 'package:timeline_lanes/timeline_lanes.dart' show LaneStyle;

/// The unit of the time axis.
enum MidiAxis {
  /// Bars and beats of the file's meter.
  bars,

  /// Seconds, following tempo changes.
  seconds,
}

/// One part: the notes of one channel within one track.
class MidiPart {
  const MidiPart._({
    required this.index,
    required this.track,
    required this.channel,
    required this.program,
    required this.instrument,
    required this.trackName,
    required this.noteCount,
  });

  /// Position in [MidiOverview.parts], also its colour index.
  final int index;

  /// Track index in the file.
  final int track;

  /// Channel 0-15 (9 is percussion).
  final int channel;

  /// General MIDI program 0-127 (0 for drums).
  final int program;

  /// Instrument name ('Violin', or 'Drums' on the percussion channel).
  final String instrument;

  /// The track's name, if it has one.
  final String? trackName;

  /// Notes in the part.
  final int noteCount;

  /// Whether the part is on the percussion channel.
  bool get isDrums => channel == 9;
}

/// The figures of a file's one-line summary.
class MidiSummary {
  const MidiSummary._({
    required this.duration,
    required this.bars,
    required this.meter,
    required this.meterChanges,
    required this.key,
    required this.tempoMin,
    required this.tempoMax,
    required this.parts,
    required this.notes,
    required this.pedalPresses,
    required this.warnings,
  });

  /// Length of the file.
  final Duration duration;

  /// Bars, counting a partial last bar.
  final int bars;

  /// First time signature, e.g. '3/4'.
  final String meter;

  /// Time-signature changes after the first.
  final int meterChanges;

  /// First key signature ('E♭ major'), or null when none is given.
  final String? key;

  /// Slowest tempo in quarter notes per minute.
  final double tempoMin;

  /// Fastest tempo.
  final double tempoMax;

  /// Parts with notes.
  final int parts;

  /// Notes in all parts.
  final int notes;

  /// Sustain-pedal presses (CC64 crossing 64 upward).
  final int pedalPresses;

  /// Problems the reader read past.
  final List<String> warnings;

  static String _clock(Duration d) {
    final s = d.inSeconds;
    final mm = (s ~/ 60 % 60).toString();
    final ss = (s % 60).toString().padLeft(2, '0');
    return s >= 3600 ? '${s ~/ 3600}:${mm.padLeft(2, '0')}:$ss' : '$mm:$ss';
  }

  static String _count(int n) =>
      n.toString().replaceAllMapped(RegExp(r'\B(?=(\d{3})+$)'), (_) => ',');

  /// The summary as one line.
  String get line {
    final lo = tempoMin.round(), hi = tempoMax.round();
    return [
      _clock(duration),
      '$bars bar${bars == 1 ? '' : 's'}',
      meterChanges > 0 ? '$meter (+$meterChanges)' : meter,
      if (key != null) key!,
      lo == hi ? '$lo bpm' : '$lo–$hi bpm',
      '$parts part${parts == 1 ? '' : 's'}',
      '${_count(notes)} note${notes == 1 ? '' : 's'}',
      if (pedalPresses > 0) '$pedalPresses pedal press${pedalPresses == 1 ? '' : 'es'}',
    ].join(' · ');
  }

  @override
  String toString() => line;
}

class _Lane {
  _Lane.step(this.label, this.points, {this.curve = false, this.min, this.max, this.format})
      : kind = 0,
        spans = const [];
  _Lane.onOff(this.label, this.spans)
      : kind = 1,
        points = const [],
        curve = false,
        min = null,
        max = null,
        format = null;
  final String label;
  final int kind;
  final List<(int, double)> points;
  final List<(int, int)> spans;
  final bool curve;
  final double? min, max;
  final String Function(double)? format;
}

const Map<int, String> _ccNames = {
  1: 'Mod', 2: 'Breath', 4: 'Foot', 5: 'Porta', 7: 'Vol', 8: 'Bal', 10: 'Pan',
  11: 'Expr', 65: 'Porta', 66: 'Sost', 67: 'Soft', 68: 'Legato', 71: 'Reso',
  74: 'Bright', 91: 'Reverb', 93: 'Chorus',
};

// Controllers that are setup or switches, never drawn as a value lane.
const Set<int> _ccSkip = {0, 6, 32, 38, 64, 96, 97, 98, 99, 100, 101, 120, 121, 122, 123, 124, 125, 126, 127};
const Set<int> _ccSwitch = {65, 66, 67, 68, 69};

/// A file read for viewing: its parts, summary and preferred axis.
class MidiOverview {
  MidiOverview._(this._file, this.parts, this.summary, this.preferredAxis, this._partNotes,
      this._lanes, this._keySharps);

  /// Reads [bytes]. The axis defaults to seconds when the tempo varies by
  /// more than [tempoTolerance] (a fraction of the slowest tempo).
  ///
  /// Throws [FormatException] when the bytes are not a MIDI file.
  factory MidiOverview.read(Uint8List bytes, {double tempoTolerance = 0.05}) {
    final MidiFile f;
    try {
      f = readMidi(bytes);
    } on MidiFormatException catch (e) {
      throw FormatException(e.message);
    }
    final length = f.lengthTicks;

    // Parts: one per (track, channel) with notes.
    final parts = <MidiPart>[];
    final partNotes = <List<MidiNote>>[];
    for (final t in f.tracks) {
      final byChannel = <int, List<MidiNote>>{};
      for (final n in t.notes) {
        (byChannel[n.channel] ??= []).add(n);
      }
      for (final ch in byChannel.keys.toList()..sort()) {
        var program = 0;
        final own = t.programs.where((p) => p.channel == ch);
        final any = [for (final o in f.tracks) ...o.programs.where((p) => p.channel == ch)]
          ..sort((a, b) => a.tick.compareTo(b.tick));
        if (own.isNotEmpty) {
          program = own.first.program;
        } else if (any.isNotEmpty) {
          program = any.first.program;
        }
        parts.add(MidiPart._(
          index: parts.length,
          track: t.index,
          channel: ch,
          program: ch == 9 ? 0 : program,
          instrument: isGmPercussionChannel(ch) ? 'Drums' : gmProgram(program).name,
          trackName: t.name?.trim().isEmpty ?? true ? null : t.name!.trim(),
          noteCount: byChannel[ch]!.length,
        ));
        partNotes.add(byChannel[ch]!);
      }
    }

    // Tempo range over the part of the file that plays.
    final tempos = [
      if (f.tempos.isEmpty || f.tempos.first.tick > 0) const MidiTempo(0, 500000),
      ...f.tempos.where((t) => t.tick < math.max(1, length)),
    ];
    final bpms = tempos.map((t) => t.bpm);
    final lo = bpms.reduce(math.min), hi = bpms.reduce(math.max);

    // Pedal presses and spans, per channel.
    final pedal = <int, List<(int, int)>>{};
    var presses = 0;
    final cc = <(int, int), List<(int, double)>>{};
    final bends = <int, List<(int, double)>>{};
    for (final t in f.tracks) {
      for (final c in t.controls) {
        (cc[(c.controller, c.channel)] ??= []).add((c.tick, c.value.toDouble()));
      }
      for (final b in t.pitchBends) {
        (bends[b.channel] ??= []).add((b.tick, b.value.toDouble()));
      }
    }
    List<(int, int)> spansOf(List<(int, double)> pts) {
      pts.sort((a, b) => a.$1.compareTo(b.$1));
      final out = <(int, int)>[];
      int? onAt;
      for (final (tick, v) in pts) {
        if (v >= 64 && onAt == null) {
          onAt = tick;
        } else if (v < 64 && onAt != null) {
          out.add((onAt, tick));
          onAt = null;
        }
      }
      if (onAt != null) out.add((onAt, math.max(onAt, length)));
      return out;
    }

    for (final e in cc.entries.where((e) => e.key.$1 == 64)) {
      final s = spansOf(e.value);
      presses += s.length;
      pedal[e.key.$2] = s;
    }

    // Lanes: tempo, pedal, then each moving controller and bend.
    final lanes = <_Lane>[];
    if (tempos.map((t) => t.microsecondsPerQuarter).toSet().length > 1) {
      lanes.add(_Lane.step('Tempo', [for (final t in tempos) (t.tick, t.bpm)],
          format: (v) => '${v.round()} bpm'));
    }
    final multi = {for (final p in parts) p.channel}.length > 1;
    String suffix(int ch) => multi ? ' ${ch + 1}' : '';
    for (final ch in pedal.keys.toList()..sort()) {
      if (pedal[ch]!.isNotEmpty) lanes.add(_Lane.onOff('Ped${suffix(ch)}', pedal[ch]!));
    }
    final keys = cc.keys.where((k) => !_ccSkip.contains(k.$1)).toList()
      ..sort((a, b) => a.$1 != b.$1 ? a.$1 - b.$1 : a.$2 - b.$2);
    for (final k in keys) {
      final pts = cc[k]!..sort((a, b) => a.$1.compareTo(b.$1));
      if (pts.map((p) => p.$2).toSet().length < 2) continue;
      final name = '${_ccNames[k.$1] ?? 'CC${k.$1}'}${suffix(k.$2)}';
      lanes.add(_ccSwitch.contains(k.$1)
          ? _Lane.onOff(name, spansOf(pts))
          : _Lane.step(name, pts, min: 0, max: 127));
    }
    for (final ch in bends.keys.toList()..sort()) {
      final pts = bends[ch]!..sort((a, b) => a.$1.compareTo(b.$1));
      if (pts.map((p) => p.$2).toSet().length < 2) continue;
      lanes.add(_Lane.step('Bend${suffix(ch)}', pts, curve: true, min: -8192, max: 8191));
    }

    final meters = f.timeSignatures;
    final key = f.keySignatures.isEmpty ? null : f.keySignatures.first;
    final summary = MidiSummary._(
      duration: Duration(microseconds: (f.durationSeconds * 1e6).round()),
      bars: f.timeMap.barCount(length),
      meter: meters.isEmpty ? '4/4' : '${meters.first}',
      meterChanges: math.max(0, meters.length - 1),
      key: key == null ? null : keyName(key.sharps, minor: key.minor),
      tempoMin: lo,
      tempoMax: hi,
      parts: parts.length,
      notes: parts.fold(0, (s, p) => s + p.noteCount),
      pedalPresses: presses,
      warnings: f.warnings,
    );
    final axis = hi > lo * (1 + tempoTolerance) ? MidiAxis.seconds : MidiAxis.bars;
    return MidiOverview._(f, parts, summary, axis, partNotes, lanes, key?.sharps);
  }

  final MidiFile _file;
  final List<List<MidiNote>> _partNotes;
  final List<_Lane> _lanes;
  final int? _keySharps;

  /// Parts with notes, by track then channel.
  final List<MidiPart> parts;

  /// The one-line summary figures.
  final MidiSummary summary;

  /// Seconds when the tempo varies, else bars.
  final MidiAxis preferredAxis;

  /// Names of the lanes the viewer shows under the roll, in order.
  List<String> get laneNames => ['Vel', for (final l in _lanes) l.label];
}

/// Colours, sizes and text of the viewer.
@immutable
class MidiViewerStyle {
  /// Creates a style; the defaults are a dark theme.
  const MidiViewerStyle({
    this.roll = const PianoRollStyle(),
    this.drums = const PianoRollStyle(rulerHeight: 0),
    this.lanes = const LaneStyle(),
    this.background = const Color(0xFF16181C),
    this.panel = const Color(0xFF1C1F24),
    this.divider = const Color(0xFF2E3238),
    this.text = const TextStyle(fontSize: 12, color: Color(0xFFE6E8EB)),
    this.muted = const TextStyle(fontSize: 11, color: Color(0xFF9AA0A8)),
    this.accent = const Color(0xFF5B9BFF),
    this.panelWidth = 200,
    this.drumRowHeight = 16,
    this.laneAreaFraction = 0.4,
  });

  /// The piano roll; its [PianoRollStyle.partColors] colour every part.
  final PianoRollStyle roll;

  /// The drum grid (no ruler of its own by default).
  final PianoRollStyle drums;

  /// Every lane. Keep [LaneStyle.labelWidth] equal to the rolls'
  /// [PianoRollStyle.keyboardWidth] so lanes line up under the notes.
  final LaneStyle lanes;

  /// Behind everything.
  final Color background;

  /// Header and parts panel.
  final Color panel;

  /// Lines between areas.
  final Color divider;

  /// Primary text.
  final TextStyle text;

  /// Secondary text.
  final TextStyle muted;

  /// Selected axis button.
  final Color accent;

  /// Width of the parts panel.
  final double panelWidth;

  /// Height of a drum-grid row.
  final double drumRowHeight;

  /// Most of the height the lanes may take before they scroll.
  final double laneAreaFraction;
}

/// A self-contained MIDI file viewer; see the library comment.
class MidiFileViewer extends StatefulWidget {
  /// Creates a viewer for [bytes].
  const MidiFileViewer({
    super.key,
    required this.bytes,
    this.initialAxis,
    this.style = const MidiViewerStyle(),
    this.tempoTolerance = 0.05,
  });

  /// The file.
  final Uint8List bytes;

  /// Starting axis; null picks [MidiOverview.preferredAxis].
  final MidiAxis? initialAxis;

  /// Colours and sizes.
  final MidiViewerStyle style;

  /// Tempo variation (a fraction) above which seconds is the default axis.
  final double tempoTolerance;

  @override
  State<MidiFileViewer> createState() => _MidiFileViewerState();
}

class _MidiFileViewerState extends State<MidiFileViewer> {
  MidiOverview? _o;
  String? _error;
  late MidiAxis _axis;
  final Set<int> _hidden = {};
  final PianoRollViewport _vp = PianoRollViewport();
  final Map<MidiAxis, List<PianoRollNote>> _notes = {};
  final Map<PianoRollNote, (MidiNote, int)> _source = Map.identity();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(MidiFileViewer old) {
    super.didUpdateWidget(old);
    if (!identical(old.bytes, widget.bytes) || old.tempoTolerance != widget.tempoTolerance) {
      _load();
    }
  }

  @override
  void dispose() {
    _vp.dispose();
    super.dispose();
  }

  void _load() {
    _notes.clear();
    _source.clear();
    _hidden.clear();
    try {
      final o = MidiOverview.read(widget.bytes, tempoTolerance: widget.tempoTolerance);
      _o = o;
      _error = null;
      _axis = widget.initialAxis ?? o.preferredAxis;
      var lo = 127, hi = 0;
      for (final p in o.parts.where((p) => !p.isDrums)) {
        for (final n in o._partNotes[p.index]) {
          lo = math.min(lo, n.pitch);
          hi = math.max(hi, n.pitch);
        }
      }
      if (lo > hi) (lo, hi) = (48, 72);
      _vp.setBounds(
          timeMin: 0,
          timeMax: _end(o),
          pitchMin: math.max(0, lo - 2).toDouble(),
          pitchMax: math.min(128, hi + 3).toDouble());
    } on FormatException catch (e) {
      _o = null;
      _error = e.message;
    }
  }

  double _t(int tick) => _axis == MidiAxis.bars ? tick.toDouble() : _o!._file.timeMap.seconds(tick);

  double _end(MidiOverview o) {
    final len = o._file.lengthTicks;
    return _axis == MidiAxis.bars ? math.max(1, len).toDouble() : math.max(0.001, o._file.durationSeconds);
  }

  List<PianoRollNote> _rollNotes() => _notes.putIfAbsent(_axis, () {
        final o = _o!;
        return [
          for (final p in o.parts)
            for (final n in o._partNotes[p.index])
              () {
                final r = PianoRollNote(
                    start: _t(n.startTick),
                    end: _t(n.endTick),
                    pitch: n.pitch,
                    velocity: n.velocity / 127,
                    part: p.index);
                _source[r] = (n, p.index);
                return r;
              }(),
        ];
      });

  void _setAxis(MidiAxis a) {
    if (a == _axis || _o == null) return;
    final tm = _o!._file.timeMap;
    final (s, e) = (_vp.start, _vp.end);
    final (low, high) = (_vp.pitchLow, _vp.pitchHigh);
    setState(() {
      _axis = a;
      _vp.setBounds(timeMin: 0, timeMax: _end(_o!), pitchMin: _vp.pitchMin, pitchMax: _vp.pitchMax);
      final (ns, ne) = a == MidiAxis.seconds
          ? (tm.seconds(s.round()), tm.seconds(e.round()))
          : (tm.tickAt(s).toDouble(), tm.tickAt(e).toDouble());
      _vp.setTime(ns, ne - ns);
      _vp.setPitch(low, high);
    });
  }

  List<PianoRollGridLine> _grid() {
    final o = _o!;
    if (_axis == MidiAxis.bars) {
      return [
        for (final g in o._file.timeMap.gridLines(o._file.lengthTicks))
          PianoRollGridLine(g.tick.toDouble(), bar: g.isBar, label: g.isBar ? '${g.bar}' : null),
      ];
    }
    final total = o._file.durationSeconds;
    final step = const [1, 5, 10, 30, 60, 300].firstWhere((s) => total / s <= 240, orElse: () => 600);
    return [
      for (var i = 0; i * step <= total; i++)
        PianoRollGridLine((i * step).toDouble(),
            bar: i % 5 == 0, label: MidiSummary._clock(Duration(seconds: i * step))),
    ];
  }

  String _describe(PianoRollNote r) {
    final src = _source[r];
    if (src == null) return '';
    final (n, pi) = src;
    final o = _o!;
    final p = o.parts[pi];
    final tm = o._file.timeMap;
    final name = p.isDrums
        ? (gmDrum(n.pitch) ?? 'Note ${n.pitch}')
        : pitchName(n.pitch, keySharps: o._keySharps);
    final beats = n.durationTicks / math.max(1, o._file.ticksPerQuarter);
    final secs = tm.seconds(n.endTick) - tm.seconds(n.startTick);
    return '$name · ${p.instrument}\n'
        'bar ${tm.barBeat(n.startTick)} · ${tm.seconds(n.startTick).toStringAsFixed(2)} s\n'
        'velocity ${n.velocity} · ${beats.toStringAsFixed(2)} beats · ${secs.toStringAsFixed(2)} s';
  }

  Widget _header(MidiViewerStyle st) {
    final o = _o!;
    Widget button(MidiAxis a, String label) => GestureDetector(
          onTap: () => _setAxis(a),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            color: _axis == a ? st.accent.withValues(alpha: 0.25) : null,
            child: Text(label, style: _axis == a ? st.text : st.muted),
          ),
        );
    final warn = o.summary.warnings;
    return Container(
      color: st.panel,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      child: Row(children: [
        Expanded(
          child: Text(
            warn.isEmpty ? o.summary.line : '${o.summary.line} · ${warn.length} warning${warn.length == 1 ? '' : 's'}',
            style: st.text,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        DecoratedBox(
          decoration: BoxDecoration(border: Border.all(color: st.divider)),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            button(MidiAxis.bars, 'Bars'),
            button(MidiAxis.seconds, 'Seconds'),
          ]),
        ),
      ]),
    );
  }

  Widget _panel(MidiViewerStyle st) {
    final o = _o!;
    return Container(
      width: st.panelWidth,
      color: st.panel,
      child: ListView(padding: const EdgeInsets.symmetric(vertical: 4), children: [
        for (final p in o.parts)
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () => setState(() => _hidden.contains(p.index) ? _hidden.remove(p.index) : _hidden.add(p.index)),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
              child: Row(children: [
                Container(
                  width: 10,
                  height: 10,
                  decoration: BoxDecoration(
                    color: _hidden.contains(p.index) ? null : st.roll.colorOf(p.index),
                    border: Border.all(color: st.roll.colorOf(p.index), width: 1.5),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(p.instrument,
                        style: _hidden.contains(p.index) ? st.muted : st.text,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis),
                    Text(
                      [if (p.trackName != null) p.trackName!, 'ch ${p.channel + 1}', '${p.noteCount} notes'].join(' · '),
                      style: st.muted,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ]),
                ),
              ]),
            ),
          ),
      ]),
    );
  }

  List<Widget> _lanes(MidiViewerStyle st, List<LaneGridLine> grid, List<PianoRollNote> visible) {
    final o = _o!;
    final ls = st.lanes;
    final start = _vp.start, span = _vp.span;
    void pan(double dt) => _vp.panTime(dt);
    void zoom(double f, double a) => _vp.zoomTime(f, anchor: a);
    return [
      TimelineLane.stalks(
        label: 'Vel',
        points: [
          for (final n in visible)
            if (n.end >= start && n.start <= start + span)
              LanePoint(n.start, n.velocity * 127, color: st.roll.colorOf(n.part)),
        ],
        start: start,
        span: span,
        min: 0,
        max: 127,
        grid: grid,
        style: ls,
        format: (v) => '${v.round()}',
        onPanTime: pan,
        onZoomTime: zoom,
      ),
      for (final l in o._lanes)
        l.kind == 1
            ? TimelineLane.onOff(
                label: l.label,
                spans: [for (final (a, b) in l.spans) LaneSpan(_t(a), _t(b))],
                start: start,
                span: span,
                grid: grid,
                style: ls,
                onPanTime: pan,
                onZoomTime: zoom,
              )
            : TimelineLane.step(
                label: l.label,
                points: [for (final (tick, v) in l.points) LanePoint(_t(tick), v)],
                curve: l.curve,
                min: l.min,
                max: l.max,
                end: _end(o),
                height: 32,
                start: start,
                span: span,
                grid: grid,
                style: ls,
                format: l.format,
                onPanTime: pan,
                onZoomTime: zoom,
              ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final st = widget.style;
    final o = _o;
    if (o == null) {
      return ColoredBox(
        color: st.background,
        child: Center(child: Text('Not a MIDI file: ${_error ?? ''}', style: st.muted)),
      );
    }
    final all = _rollNotes();
    final visible = [for (final n in all) if (!_hidden.contains(n.part)) n];
    final drumParts = {for (final p in o.parts) if (p.isDrums) p.index};
    final melodic = [for (final n in visible) if (!drumParts.contains(n.part)) n];
    final drums = [for (final n in visible) if (drumParts.contains(n.part)) n];
    final hasMelodic = o.parts.any((p) => !p.isDrums);
    final grid = _grid();
    final laneGrid = [for (final g in grid) LaneGridLine(g.time, bar: g.bar)];
    final drumRows = {for (final n in drums) n.pitch}.length;

    return ColoredBox(
      color: st.background,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _header(st),
        Container(height: 1, color: st.divider),
        Expanded(
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            _panel(st),
            Container(width: 1, color: st.divider),
            Expanded(
              child: LayoutBuilder(builder: (context, c) {
                final drumHeight = drums.isEmpty
                    ? 0.0
                    : hasMelodic
                        ? math.min(drumRows * st.drumRowHeight, c.maxHeight * 0.3)
                        : double.infinity;
                return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  if (hasMelodic)
                    Expanded(
                      child: PianoRoll(
                        notes: melodic,
                        viewport: _vp,
                        grid: grid,
                        style: st.roll,
                        describe: _describe,
                        rowLabel: (p) => p % 12 == 0 ? pitchName(p, keySharps: o._keySharps) : null,
                      ),
                    ),
                  if (drums.isNotEmpty) ...[
                    Container(height: 1, color: st.divider),
                    drumHeight.isFinite
                        ? SizedBox(height: drumHeight, child: _drumRoll(st, drums, grid, hasMelodic))
                        : Expanded(child: _drumRoll(st, drums, grid, hasMelodic)),
                  ],
                  ConstrainedBox(
                    constraints: BoxConstraints(maxHeight: c.maxHeight * st.laneAreaFraction),
                    child: ListenableBuilder(
                      listenable: _vp,
                      builder: (context, _) => SingleChildScrollView(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: _lanes(st, laneGrid, visible),
                        ),
                      ),
                    ),
                  ),
                ]);
              }),
            ),
          ]),
        ),
      ]),
    );
  }

  Widget _drumRoll(MidiViewerStyle st, List<PianoRollNote> drums, List<PianoRollGridLine> grid, bool hasMelodic) =>
      PianoRoll(
        notes: drums,
        viewport: _vp,
        grid: grid,
        drums: true,
        style: hasMelodic ? st.drums : st.roll,
        describe: _describe,
        rowLabel: (p) => gmDrum(p) ?? '$p',
      );
}
