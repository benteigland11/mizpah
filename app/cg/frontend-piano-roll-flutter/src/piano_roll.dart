/// A zoomable, pannable piano roll over plain note data.
///
/// ```dart
/// final viewport = PianoRollViewport(timeMax: 16, pitchMin: 48, pitchMax: 84);
/// PianoRoll(
///   notes: [PianoRollNote(start: 0, end: 1, pitch: 60, velocity: 0.8)],
///   viewport: viewport,
///   grid: [for (var b = 0; b < 16; b++) PianoRollGridLine(b.toDouble(), bar: b % 4 == 0)],
/// );
/// ```
///
/// Time is any unit the caller chooses; the grid and ruler labels come from
/// the caller too, so the roll knows nothing about meters or files.
/// Scroll pans pitch (horizontal scroll or shift pans time), ctrl/cmd +
/// scroll zooms time at the pointer, alt + scroll zooms pitch, drag pans,
/// a pinch zooms time, and a double tap fits everything.
library;

import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import 'piano_roll_viewport.dart';

export 'piano_roll_viewport.dart';

/// One note on the roll.
@immutable
class PianoRollNote {
  /// Creates a note.
  const PianoRollNote({
    required this.start,
    required this.end,
    required this.pitch,
    this.velocity = 1,
    this.part = 0,
  });

  /// Start time.
  final double start;

  /// End time.
  final double end;

  /// Pitch row 0-127.
  final int pitch;

  /// Loudness 0-1; drawn as brightness.
  final double velocity;

  /// Index into [PianoRollStyle.partColors].
  final int part;
}

/// A vertical grid line, optionally labelled on the ruler.
@immutable
class PianoRollGridLine {
  /// Creates a grid line at [time].
  const PianoRollGridLine(this.time, {this.bar = false, this.label});

  /// Where the line falls.
  final double time;

  /// A strong (bar) line rather than a weak (beat) one.
  final bool bar;

  /// Ruler text, or null for none.
  final String? label;
}

/// Every colour and size of the roll.
@immutable
class PianoRollStyle {
  /// Creates a style; each value has a dark-theme default.
  const PianoRollStyle({
    this.background = const Color(0xFF16181C),
    this.blackKeyRow = const Color(0xFF111316),
    this.octaveLine = const Color(0xFF2E3238),
    this.beatLine = const Color(0xFF22252A),
    this.barLine = const Color(0xFF3A3F47),
    this.whiteKey = const Color(0xFFD9DCE1),
    this.blackKey = const Color(0xFF2A2D33),
    this.keyLabel = const TextStyle(fontSize: 9, color: Color(0xFF5A5F68)),
    this.rulerBackground = const Color(0xFF1C1F24),
    this.rulerLabel = const TextStyle(fontSize: 10, color: Color(0xFF9AA0A8)),
    this.partColors = const [
      Color(0xFF5B9BFF), Color(0xFFFF8A5B), Color(0xFF6BD68A), Color(0xFFE0C35B),
      Color(0xFFC77DFF), Color(0xFF5BD6D0), Color(0xFFFF6B9A), Color(0xFFA8B45B),
    ],
    this.velocityFloor = 0.35,
    this.noteOutline = const Color(0x55000000),
    this.cardBackground = const Color(0xF0252830),
    this.cardText = const TextStyle(fontSize: 11, color: Color(0xFFE6E8EB), height: 1.35),
    this.keyboardWidth = 44,
    this.rulerHeight = 20,
    this.minLabelGap = 48,
  });

  /// Plot background (white-key rows).
  final Color background;

  /// Rows of black keys.
  final Color blackKeyRow;

  /// Line between B and C.
  final Color octaveLine;

  /// Weak grid lines.
  final Color beatLine;

  /// Strong grid lines.
  final Color barLine;

  /// Keyboard white keys.
  final Color whiteKey;

  /// Keyboard black keys.
  final Color blackKey;

  /// Key and drum-row labels.
  final TextStyle keyLabel;

  /// Ruler strip.
  final Color rulerBackground;

  /// Ruler labels.
  final TextStyle rulerLabel;

  /// Note colour per part (cycled).
  final List<Color> partColors;

  /// Opacity of a velocity-0 note; velocity 1 is opaque.
  final double velocityFloor;

  /// Thin outline around notes.
  final Color noteOutline;

  /// Hover card fill.
  final Color cardBackground;

  /// Hover card text.
  final TextStyle cardText;

  /// Width of the keyboard (or drum label) column.
  final double keyboardWidth;

  /// Height of the ruler strip; 0 hides it.
  final double rulerHeight;

  /// Least spacing between ruler labels, in pixels.
  final double minLabelGap;

  /// Colour for [part].
  Color colorOf(int part) =>
      partColors.isEmpty ? const Color(0xFFFFFFFF) : partColors[part % partColors.length];
}

/// A piano roll: pitch rows, notes coloured by part, a keyboard, a ruler.
///
/// In [drums] mode only the pitches present get rows (labelled by
/// [rowLabel], e.g. kit pieces), rows fill the height, notes are drawn as
/// hits, and the viewport's pitch window is ignored.
class PianoRoll extends StatefulWidget {
  /// Creates a roll.
  const PianoRoll({
    super.key,
    required this.notes,
    required this.viewport,
    this.grid = const [],
    this.style = const PianoRollStyle(),
    this.drums = false,
    this.rowLabel,
    this.describe,
  });

  /// Notes in any order.
  final List<PianoRollNote> notes;

  /// Shared visible window.
  final PianoRollViewport viewport;

  /// Grid lines and ruler labels.
  final List<PianoRollGridLine> grid;

  /// Colours and sizes.
  final PianoRollStyle style;

  /// Drum-grid mode.
  final bool drums;

  /// Label for a pitch row; default names each C (C4 = 60) in pitch mode
  /// and every row by number in drum mode. Return null for no label.
  final String? Function(int pitch)? rowLabel;

  /// Text of the hover card for a note; null disables hover.
  final String Function(PianoRollNote note)? describe;

  @override
  State<PianoRoll> createState() => _PianoRollState();
}

class _PianoRollState extends State<PianoRoll> {
  late List<PianoRollNote> _sorted;
  late List<int> _drumRows;
  double _maxLen = 0;
  PianoRollNote? _hover;
  Offset _hoverAt = Offset.zero;
  double _lastScale = 1;

  @override
  void initState() {
    super.initState();
    _index();
  }

  @override
  void didUpdateWidget(PianoRoll old) {
    super.didUpdateWidget(old);
    if (!identical(old.notes, widget.notes) || old.drums != widget.drums) {
      _index();
      _hover = null;
    }
  }

  void _index() {
    _sorted = [...widget.notes]..sort((a, b) => a.start.compareTo(b.start));
    _maxLen = 0;
    for (final n in _sorted) {
      _maxLen = math.max(_maxLen, n.end - n.start);
    }
    _drumRows = ({for (final n in _sorted) n.pitch}.toList()..sort((a, b) => b - a));
  }

  _Geometry _geo(Size size) => _Geometry(
      size, widget.style, widget.viewport, widget.drums ? _drumRows : null);

  PianoRollNote? _hit(Offset p, Size size) {
    final g = _geo(size);
    if (!g.plot.contains(p)) return null;
    final pitch = g.pitchAt(p.dy);
    if (pitch == null) return null;
    final slop = 3 * widget.viewport.span / math.max(1, g.plot.width);
    final t = g.timeAt(p.dx);
    PianoRollNote? best;
    for (final n in _sorted) {
      if (n.start - slop > t) break;
      if (n.pitch != pitch) continue;
      final end = widget.drums ? n.start + g.hitSize / 2 * widget.viewport.span / g.plot.width : n.end;
      if (t >= n.start - slop && t <= end + slop) best = n;
    }
    return best;
  }

  bool get _ctrl =>
      HardwareKeyboard.instance.isControlPressed || HardwareKeyboard.instance.isMetaPressed;

  void _onSignal(PointerSignalEvent e, Size size) {
    final g = _geo(size);
    final v = widget.viewport;
    if (e is PointerScrollEvent) {
      final d = e.scrollDelta;
      final kb = HardwareKeyboard.instance;
      if (_ctrl) {
        v.zoomTime(math.exp(d.dy * 0.002), anchor: g.timeAt(e.localPosition.dx));
      } else if (kb.isAltPressed && !widget.drums) {
        v.zoomPitch(math.exp(d.dy * 0.002), anchor: g.pitchValueAt(e.localPosition.dy));
      } else if (kb.isShiftPressed) {
        v.panTime((d.dx + d.dy) / g.plot.width * v.span);
      } else {
        if (d.dx != 0) v.panTime(d.dx / g.plot.width * v.span);
        if (d.dy != 0 && !widget.drums) v.panPitch(-d.dy / g.rowHeight);
      }
    } else if (e is PointerScaleEvent) {
      v.zoomTime(1 / e.scale, anchor: g.timeAt(e.localPosition.dx));
    }
  }

  void _onScale(ScaleUpdateDetails d, Size size) {
    final g = _geo(size);
    final v = widget.viewport;
    if (d.pointerCount > 1 && d.scale != _lastScale) {
      v.zoomTime(_lastScale / d.scale, anchor: g.timeAt(d.localFocalPoint.dx));
      _lastScale = d.scale;
    }
    final delta = d.focalPointDelta;
    if (delta.dx != 0) v.panTime(-delta.dx / g.plot.width * v.span);
    if (delta.dy != 0 && !widget.drums) v.panPitch(delta.dy / g.rowHeight);
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final size = c.biggest;
      final card = _hover;
      final describe = widget.describe;
      return Listener(
        onPointerSignal: (e) => _onSignal(e, size),
        child: MouseRegion(
          onHover: describe == null
              ? null
              : (e) {
                  final n = _hit(e.localPosition, size);
                  if (n != _hover || n != null) {
                    setState(() {
                      _hover = n;
                      _hoverAt = e.localPosition;
                    });
                  }
                },
          onExit: (_) => setState(() => _hover = null),
          child: GestureDetector(
            onDoubleTap: widget.viewport.fit,
            onScaleStart: (_) => _lastScale = 1,
            onScaleUpdate: (d) => _onScale(d, size),
            child: Stack(children: [
              Positioned.fill(
                child: CustomPaint(
                  painter: _RollPainter(
                    notes: _sorted,
                    maxLen: _maxLen,
                    drumRows: widget.drums ? _drumRows : null,
                    grid: widget.grid,
                    viewport: widget.viewport,
                    style: widget.style,
                    rowLabel: widget.rowLabel,
                    hover: _hover,
                  ),
                ),
              ),
              if (card != null && describe != null)
                _Card(
                  at: _hoverAt,
                  bounds: size,
                  text: describe(card),
                  style: widget.style,
                ),
            ]),
          ),
        ),
      );
    });
  }
}

class _Card extends StatelessWidget {
  const _Card({required this.at, required this.bounds, required this.text, required this.style});
  final Offset at;
  final Size bounds;
  final String text;
  final PianoRollStyle style;

  @override
  Widget build(BuildContext context) {
    // Sit below-right of the pointer, flipping at the edges.
    final right = at.dx > bounds.width * 0.6;
    final below = at.dy < bounds.height * 0.6;
    return Positioned(
      left: right ? null : at.dx + 14,
      right: right ? bounds.width - at.dx + 14 : null,
      top: below ? at.dy + 14 : null,
      bottom: below ? null : bounds.height - at.dy + 14,
      child: IgnorePointer(
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: style.cardBackground,
            borderRadius: BorderRadius.circular(4),
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
            child: Text(text, style: style.cardText),
          ),
        ),
      ),
    );
  }
}

class _Geometry {
  _Geometry(this.size, this.style, this.v, this.drumRows)
      : plot = Rect.fromLTRB(
            math.min(style.keyboardWidth, size.width), math.min(style.rulerHeight, size.height),
            size.width, size.height);
  final Size size;
  final PianoRollStyle style;
  final PianoRollViewport v;
  final List<int>? drumRows;
  final Rect plot;

  double get rowHeight {
    final rows = drumRows;
    if (rows != null) return rows.isEmpty ? plot.height : plot.height / rows.length;
    return plot.height / (v.pitchHigh - v.pitchLow);
  }

  double get hitSize => math.max(4, math.min(rowHeight * 0.7, 12));

  double x(double t) => plot.left + (t - v.start) / v.span * plot.width;
  double timeAt(double px) => v.start + (px - plot.left) / math.max(1, plot.width) * v.span;

  /// Top edge of a pitch row.
  double rowTop(int pitch) {
    final rows = drumRows;
    if (rows != null) return plot.top + rows.indexOf(pitch) * rowHeight;
    return plot.top + (v.pitchHigh - pitch - 1) * rowHeight;
  }

  double pitchValueAt(double py) => v.pitchHigh - (py - plot.top) / rowHeight;

  int? pitchAt(double py) {
    final rows = drumRows;
    if (rows != null) {
      final i = ((py - plot.top) / rowHeight).floor();
      return i >= 0 && i < rows.length ? rows[i] : null;
    }
    return pitchValueAt(py).floor();
  }
}

bool _black(int p) => const {1, 3, 6, 8, 10}.contains(p % 12);

class _RollPainter extends CustomPainter {
  _RollPainter({
    required this.notes,
    required this.maxLen,
    required this.drumRows,
    required this.grid,
    required this.viewport,
    required this.style,
    required this.rowLabel,
    required this.hover,
  }) : super(repaint: viewport);

  final List<PianoRollNote> notes;
  final double maxLen;
  final List<int>? drumRows;
  final List<PianoRollGridLine> grid;
  final PianoRollViewport viewport;
  final PianoRollStyle style;
  final String? Function(int)? rowLabel;
  final PianoRollNote? hover;

  String? _label(int p) {
    final f = rowLabel;
    if (f != null) return f(p);
    if (drumRows != null) return '$p';
    return p % 12 == 0 ? 'C${p ~/ 12 - 1}' : null;
  }

  void _text(Canvas c, String s, TextStyle st, Offset at, {double? maxWidth, bool centerY = true}) {
    final tp = TextPainter(
        text: TextSpan(text: s, style: st),
        textDirection: TextDirection.ltr,
        maxLines: 1,
        ellipsis: '…')
      ..layout(maxWidth: maxWidth ?? double.infinity);
    tp.paint(c, centerY ? at - Offset(0, tp.height / 2) : at);
  }

  @override
  void paint(Canvas canvas, Size size) {
    final g = _Geometry(size, style, viewport, drumRows);
    final plot = g.plot;
    final v = viewport;
    final rh = g.rowHeight;
    final paint = Paint();

    canvas.save();
    canvas.clipRect(plot);
    canvas.drawRect(plot, paint..color = style.background);

    // Rows.
    final rows = drumRows;
    final visible = rows ??
        [for (var p = v.pitchLow.floor(); p < v.pitchHigh.ceil() && p < 128; p++) if (p >= 0) p];
    for (var i = 0; i < visible.length; i++) {
      final p = visible[i];
      final top = g.rowTop(p);
      if (rows != null ? i.isOdd : _black(p)) {
        canvas.drawRect(Rect.fromLTWH(plot.left, top, plot.width, rh), paint..color = style.blackKeyRow);
      }
      if (rows == null && p % 12 == 0 && rh > 1.5) {
        canvas.drawRect(Rect.fromLTWH(plot.left, top + rh - 1, plot.width, 1), paint..color = style.octaveLine);
      }
    }

    // Grid, thinning beat lines that would crowd.
    final perUnit = plot.width / v.span;
    var beatGap = double.infinity;
    for (var i = 1; i < grid.length; i++) {
      if (!grid[i].bar || !grid[i - 1].bar) {
        beatGap = math.min(beatGap, (grid[i].time - grid[i - 1].time) * perUnit);
        break;
      }
    }
    final showBeats = beatGap >= 5;
    for (final l in grid) {
      if (l.time < v.start || l.time > v.end) continue;
      if (!l.bar && !showBeats) continue;
      final gx = g.x(l.time).floorToDouble();
      canvas.drawRect(Rect.fromLTWH(gx, plot.top, 1, plot.height),
          paint..color = l.bar ? style.barLine : style.beatLine);
    }

    // Notes: sorted by start, so skip to the first that can reach the window.
    var lo = 0, hi = notes.length;
    final from = v.start - maxLen;
    while (lo < hi) {
      final mid = (lo + hi) >> 1;
      if (notes[mid].start < from) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    final outline = Paint()
      ..color = style.noteOutline
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    final hs = g.hitSize;
    for (var i = lo; i < notes.length; i++) {
      final n = notes[i];
      if (n.start > v.end) break;
      if (n.end < v.start && rows == null) continue;
      if (rows == null && (n.pitch + 1 < v.pitchLow || n.pitch > v.pitchHigh)) continue;
      final base = style.colorOf(n.part);
      final a = style.velocityFloor + (1 - style.velocityFloor) * n.velocity.clamp(0, 1);
      final top = g.rowTop(n.pitch);
      final isHover = identical(n, hover);
      paint.color = base.withValues(alpha: isHover ? 1 : a * base.a);
      if (rows != null) {
        final cx = g.x(n.start), cy = top + rh / 2;
        final path = Path()
          ..moveTo(cx, cy - hs / 2)
          ..lineTo(cx + hs / 2, cy)
          ..lineTo(cx, cy + hs / 2)
          ..lineTo(cx - hs / 2, cy)
          ..close();
        canvas.drawPath(path, paint);
        if (isHover) canvas.drawPath(path, outline..color = style.cardText.color ?? base);
        continue;
      }
      final r = Rect.fromLTRB(g.x(n.start), top + (rh > 3 ? 0.5 : 0),
          math.max(g.x(n.end), g.x(n.start) + 1.5), top + math.max(1, rh - (rh > 3 ? 0.5 : 0)));
      if (rh > 4) {
        final rr = RRect.fromRectAndRadius(r, Radius.circular(math.min(2, rh / 4)));
        canvas.drawRRect(rr, paint);
        canvas.drawRRect(rr, outline..color = isHover ? (style.cardText.color ?? base) : style.noteOutline);
      } else {
        canvas.drawRect(r, paint);
      }
    }
    canvas.restore();

    // Keyboard / row labels.
    final kb = Rect.fromLTRB(0, plot.top, plot.left, size.height);
    if (kb.width > 0) {
      canvas.save();
      canvas.clipRect(kb);
      canvas.drawRect(kb, paint..color = rows == null ? style.whiteKey : style.rulerBackground);
      for (var i = 0; i < visible.length; i++) {
        final p = visible[i];
        final top = g.rowTop(p);
        if (rows == null) {
          if (_black(p)) {
            canvas.drawRect(Rect.fromLTWH(0, top, kb.width * 0.6, rh), paint..color = style.blackKey);
          } else if (p % 12 == 0 || p % 12 == 5) {
            canvas.drawRect(Rect.fromLTWH(0, top + rh - 0.5, kb.width, 0.5), paint..color = style.octaveLine);
          }
        }
        final label = (rows != null || rh >= 7 || p % 12 == 0) ? _label(p) : null;
        if (label != null && rh >= 6) {
          _text(canvas, label, style.keyLabel, Offset(rows == null ? kb.width * 0.62 : 4, top + rh / 2),
              maxWidth: rows == null ? kb.width * 0.38 : kb.width - 6);
        } else if (label != null && p % 12 == 0) {
          _text(canvas, label, style.keyLabel, Offset(kb.width * 0.62, top + rh / 2),
              maxWidth: kb.width * 0.38);
        }
      }
      canvas.restore();
    }

    // Ruler.
    final ruler = Rect.fromLTRB(plot.left, 0, size.width, plot.top);
    if (ruler.height > 0) {
      canvas.save();
      canvas.clipRect(ruler);
      canvas.drawRect(ruler, paint..color = style.rulerBackground);
      var lastX = -double.infinity;
      for (final l in grid) {
        final label = l.label;
        if (label == null || l.time < v.start - v.span * 0.1 || l.time > v.end) continue;
        final lx = g.x(l.time);
        if (lx - lastX < style.minLabelGap) continue;
        lastX = lx;
        canvas.drawRect(Rect.fromLTWH(lx.floorToDouble(), ruler.bottom - 5, 1, 5),
            paint..color = style.barLine);
        _text(canvas, label, style.rulerLabel, Offset(lx + 3, ruler.height / 2));
      }
      canvas.restore();
    }
  }

  @override
  bool shouldRepaint(_RollPainter old) =>
      !identical(old.notes, notes) ||
      !identical(old.grid, grid) ||
      old.style != style ||
      old.hover != hover ||
      old.drumRows != drumRows ||
      old.viewport != viewport;
}
