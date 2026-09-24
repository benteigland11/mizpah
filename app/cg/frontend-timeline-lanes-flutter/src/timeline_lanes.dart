/// Time-aligned strips that scroll with a track view: a step or curve lane
/// (tempo, a controller), an on/off lane (a pedal), and a stalk lane
/// (velocities).
///
/// ```dart
/// TimelineLane.step(
///   label: 'Tempo',
///   points: [LanePoint(0, 120), LanePoint(8, 96)],
///   start: viewport.start, span: viewport.span,
///   onPanTime: viewport.panTime,
/// );
/// ```
///
/// A lane is told what window to show ([TimelineLane.start],
/// [TimelineLane.span]) and reports pans and zooms through callbacks, so the
/// caller keeps one viewport for a roll and all its lanes. Each lane has a
/// label column of [LaneStyle.labelWidth] on the left, to line up with a
/// roll's keyboard; hovering shows the value under the pointer there.
library;

import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

/// A value at a time; [color] overrides the lane colour for a stalk.
@immutable
class LanePoint {
  /// Creates a point.
  const LanePoint(this.time, this.value, {this.color});

  /// When.
  final double time;

  /// The value.
  final double value;

  /// Colour for this stalk, or null for the lane colour.
  final Color? color;
}

/// An interval when something is on; [level] 0-1 draws partial (half-pedal).
@immutable
class LaneSpan {
  /// Creates a span.
  const LaneSpan(this.start, this.end, {this.level = 1});

  /// When it turns on.
  final double start;

  /// When it turns off.
  final double end;

  /// How far on, 0-1.
  final double level;
}

/// A vertical grid line; [bar] lines are drawn stronger.
@immutable
class LaneGridLine {
  /// Creates a grid line.
  const LaneGridLine(this.time, {this.bar = false});

  /// Where the line falls.
  final double time;

  /// A strong line.
  final bool bar;
}

/// Colours and sizes of a lane.
@immutable
class LaneStyle {
  /// Creates a style; each value has a dark-theme default.
  const LaneStyle({
    this.background = const Color(0xFF16181C),
    this.labelBackground = const Color(0xFF1C1F24),
    this.divider = const Color(0xFF2E3238),
    this.color = const Color(0xFF5B9BFF),
    this.fillOpacity = 0.22,
    this.beatLine = const Color(0xFF22252A),
    this.barLine = const Color(0xFF3A3F47),
    this.label = const TextStyle(fontSize: 10, color: Color(0xFF9AA0A8)),
    this.value = const TextStyle(fontSize: 10, color: Color(0xFFE6E8EB)),
    this.labelWidth = 44,
    this.strokeWidth = 1.5,
    this.stalkWidth = 2,
    this.padding = 3,
  });

  /// Plot background.
  final Color background;

  /// Label column background.
  final Color labelBackground;

  /// Line between the lane and what is above it.
  final Color divider;

  /// Line, bar and stalk colour.
  final Color color;

  /// Opacity of the area under a step lane and of on spans.
  final double fillOpacity;

  /// Weak grid lines.
  final Color beatLine;

  /// Strong grid lines.
  final Color barLine;

  /// Lane name.
  final TextStyle label;

  /// Hover value.
  final TextStyle value;

  /// Width of the label column.
  final double labelWidth;

  /// Width of a step or curve line.
  final double strokeWidth;

  /// Width of a stalk.
  final double stalkWidth;

  /// Space above and below the plotted values.
  final double padding;
}

enum _Kind { step, curve, onOff, stalks }

/// One time-aligned strip.
class TimelineLane extends StatefulWidget {
  const TimelineLane._(
    this._kind, {
    super.key,
    required this.label,
    required this.start,
    required this.span,
    this.points = const [],
    this.spans = const [],
    this.min,
    this.max,
    this.end,
    this.grid = const [],
    this.height = 40,
    this.style = const LaneStyle(),
    this.format,
    this.onPanTime,
    this.onZoomTime,
  });

  /// Values held until the next point (tempo, a switch-like controller).
  /// With [curve], points are joined by straight lines instead (pitch bend,
  /// expression). The last value holds until [end] or the window's end.
  const TimelineLane.step({
    Key? key,
    required String label,
    required List<LanePoint> points,
    required double start,
    required double span,
    bool curve = false,
    double? min,
    double? max,
    double? end,
    List<LaneGridLine> grid = const [],
    double height = 40,
    LaneStyle style = const LaneStyle(),
    String Function(double value)? format,
    ValueChanged<double>? onPanTime,
    void Function(double factor, double anchor)? onZoomTime,
  }) : this._(curve ? _Kind.curve : _Kind.step,
            key: key, label: label, points: points, start: start, span: span,
            min: min, max: max, end: end, grid: grid, height: height, style: style,
            format: format, onPanTime: onPanTime, onZoomTime: onZoomTime);

  /// Intervals when something is on (sustain pedal).
  const TimelineLane.onOff({
    Key? key,
    required String label,
    required List<LaneSpan> spans,
    required double start,
    required double span,
    List<LaneGridLine> grid = const [],
    double height = 16,
    LaneStyle style = const LaneStyle(),
    ValueChanged<double>? onPanTime,
    void Function(double factor, double anchor)? onZoomTime,
  }) : this._(_Kind.onOff,
            key: key, label: label, spans: spans, start: start, span: span,
            grid: grid, height: height, style: style,
            onPanTime: onPanTime, onZoomTime: onZoomTime);

  /// A vertical stalk per point (note velocities).
  const TimelineLane.stalks({
    Key? key,
    required String label,
    required List<LanePoint> points,
    required double start,
    required double span,
    double? min,
    double? max,
    List<LaneGridLine> grid = const [],
    double height = 40,
    LaneStyle style = const LaneStyle(),
    String Function(double value)? format,
    ValueChanged<double>? onPanTime,
    void Function(double factor, double anchor)? onZoomTime,
  }) : this._(_Kind.stalks,
            key: key, label: label, points: points, start: start, span: span,
            min: min, max: max, grid: grid, height: height, style: style,
            format: format, onPanTime: onPanTime, onZoomTime: onZoomTime);

  final _Kind _kind;

  /// Name in the label column.
  final String label;

  /// Time at the left edge of the plot.
  final double start;

  /// Width of the plot in time.
  final double span;

  /// Values (step, curve and stalk lanes).
  final List<LanePoint> points;

  /// On intervals (on/off lanes).
  final List<LaneSpan> spans;

  /// Bottom of the value range; default the least value.
  final double? min;

  /// Top of the value range; default the greatest value.
  final double? max;

  /// Where the last step value stops; default the window's end.
  final double? end;

  /// Grid lines to draw behind the values.
  final List<LaneGridLine> grid;

  /// Height of the lane.
  final double height;

  /// Colours and sizes.
  final LaneStyle style;

  /// Hover text for a value; default up to two decimals.
  final String Function(double value)? format;

  /// Called with a time delta when the lane is dragged or scrolled sideways.
  final ValueChanged<double>? onPanTime;

  /// Called with a factor (< 1 zooms in) and anchor time on ctrl/cmd+scroll.
  final void Function(double factor, double anchor)? onZoomTime;

  @override
  State<TimelineLane> createState() => _TimelineLaneState();
}

class _TimelineLaneState extends State<TimelineLane> {
  double? _hoverTime;
  double _plotWidth = 1;

  double _timeAt(double x) => widget.start + (x - widget.style.labelWidth) / _plotWidth * widget.span;

  List<LanePoint> get _sorted => [...widget.points]..sort((a, b) => a.time.compareTo(b.time));

  String _fmt(double v) {
    final f = widget.format;
    if (f != null) return f(v);
    return v == v.roundToDouble() ? v.toInt().toString() : v.toStringAsFixed(2);
  }

  /// Value text at [t], or null when there is nothing there.
  String? _valueAt(double t) {
    final pts = _sorted;
    switch (widget._kind) {
      case _Kind.onOff:
        for (final s in widget.spans) {
          if (t >= s.start && t < s.end) return s.level >= 1 ? 'on' : '${(s.level * 100).round()}%';
        }
        return 'off';
      case _Kind.stalks:
        final slop = 4 * widget.span / _plotWidth;
        LanePoint? best;
        for (final p in pts) {
          if ((p.time - t).abs() <= slop && (best == null || p.value > best.value)) best = p;
        }
        return best == null ? null : _fmt(best.value);
      case _Kind.step:
      case _Kind.curve:
        if (pts.isEmpty || t < pts.first.time) return null;
        for (var i = pts.length - 1; i >= 0; i--) {
          if (pts[i].time <= t) {
            if (widget._kind == _Kind.curve && i + 1 < pts.length) {
              final a = pts[i], b = pts[i + 1];
              final f = b.time == a.time ? 0 : (t - a.time) / (b.time - a.time);
              return _fmt(a.value + (b.value - a.value) * f);
            }
            return _fmt(pts[i].value);
          }
        }
        return null;
    }
  }

  void _signal(PointerSignalEvent e) {
    if (e is! PointerScrollEvent) return;
    final kb = HardwareKeyboard.instance;
    final d = e.scrollDelta;
    if (kb.isControlPressed || kb.isMetaPressed) {
      widget.onZoomTime?.call(math.exp(d.dy * 0.002), _timeAt(e.localPosition.dx));
    } else if (kb.isShiftPressed || d.dx != 0) {
      widget.onPanTime?.call((kb.isShiftPressed ? d.dx + d.dy : d.dx) / _plotWidth * widget.span);
    }
  }

  @override
  Widget build(BuildContext context) {
    final st = widget.style;
    final hover = _hoverTime;
    final value = hover == null ? null : _valueAt(hover);
    return SizedBox(
      height: widget.height,
      child: LayoutBuilder(builder: (context, c) {
        _plotWidth = math.max(1, c.maxWidth - st.labelWidth);
        return Listener(
          onPointerSignal: _signal,
          child: MouseRegion(
            onHover: (e) => setState(() => _hoverTime =
                e.localPosition.dx >= st.labelWidth ? _timeAt(e.localPosition.dx) : null),
            onExit: (_) => setState(() => _hoverTime = null),
            child: GestureDetector(
              onHorizontalDragUpdate: widget.onPanTime == null
                  ? null
                  : (d) => widget.onPanTime!(-d.delta.dx / _plotWidth * widget.span),
              child: CustomPaint(
                size: Size(c.maxWidth, widget.height),
                painter: _LanePainter(widget, _sorted, hover),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: SizedBox(
                    width: st.labelWidth,
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(widget.label, style: st.label, maxLines: 1, overflow: TextOverflow.ellipsis),
                          if (value != null && widget.height >= 28)
                            Text(value, style: st.value, maxLines: 1, overflow: TextOverflow.ellipsis),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        );
      }),
    );
  }
}

class _LanePainter extends CustomPainter {
  _LanePainter(this.lane, this.points, this.hover);
  final TimelineLane lane;
  final List<LanePoint> points;
  final double? hover;

  @override
  void paint(Canvas canvas, Size size) {
    final st = lane.style;
    final left = math.min(st.labelWidth, size.width);
    final plot = Rect.fromLTRB(left, 0, size.width, size.height);
    final p = Paint();
    canvas.drawRect(Rect.fromLTWH(0, 0, left, size.height), p..color = st.labelBackground);
    canvas.save();
    canvas.clipRect(plot);
    canvas.drawRect(plot, p..color = st.background);
    double x(double t) => plot.left + (t - lane.start) / lane.span * plot.width;
    final end = lane.start + lane.span;

    for (final g in lane.grid) {
      if (g.time < lane.start || g.time > end) continue;
      canvas.drawRect(Rect.fromLTWH(x(g.time).floorToDouble(), 0, 1, size.height),
          p..color = g.bar ? st.barLine : st.beatLine);
    }

    final fill = st.color.withValues(alpha: st.fillOpacity * st.color.a);
    if (lane._kind == _Kind.onOff) {
      final barH = size.height - 2 * st.padding;
      for (final s in lane.spans) {
        if (s.end < lane.start || s.start > end) continue;
        final h = math.max(2.0, barH * s.level.clamp(0, 1));
        final r = Rect.fromLTRB(x(s.start), size.height - st.padding - h,
            math.max(x(s.end), x(s.start) + 1), size.height - st.padding);
        canvas.drawRect(r, p..color = fill);
        canvas.drawRect(Rect.fromLTWH(r.left, r.top, r.width, math.min(2, h)), p..color = st.color);
      }
    } else if (points.isNotEmpty) {
      var lo = lane.min ?? points.map((e) => e.value).reduce(math.min);
      var hi = lane.max ?? points.map((e) => e.value).reduce(math.max);
      if (lane._kind == _Kind.stalks && lane.min == null) lo = math.min(lo, 0);
      if (hi <= lo) {
        hi = lo + 1;
        lo = lo - 1;
      }
      double y(double v) =>
          size.height - st.padding - (v - lo) / (hi - lo) * (size.height - 2 * st.padding);

      if (lane._kind == _Kind.stalks) {
        final base = y(math.max(lo, 0));
        for (final pt in points) {
          if (pt.time < lane.start || pt.time > end) continue;
          final px = x(pt.time);
          final c = pt.color ?? st.color;
          canvas.drawRect(Rect.fromLTRB(px - st.stalkWidth / 2, y(pt.value), px + st.stalkWidth / 2, base),
              p..color = c);
          canvas.drawCircle(Offset(px, y(pt.value)), st.stalkWidth * 1.2, p);
        }
      } else {
        final stop = lane.end ?? end;
        final line = Path();
        final area = Path();
        final bottom = size.height;
        for (var i = 0; i < points.length; i++) {
          final pt = points[i];
          final px = x(pt.time), py = y(pt.value);
          if (i == 0) {
            line.moveTo(px, py);
            area.moveTo(px, bottom);
            area.lineTo(px, py);
          } else if (lane._kind == _Kind.step) {
            line.lineTo(px, y(points[i - 1].value));
            line.lineTo(px, py);
            area.lineTo(px, y(points[i - 1].value));
            area.lineTo(px, py);
          } else {
            line.lineTo(px, py);
            area.lineTo(px, py);
          }
        }
        final lastX = math.max(x(stop), x(points.last.time));
        line.lineTo(lastX, y(points.last.value));
        area.lineTo(lastX, y(points.last.value));
        area.lineTo(lastX, bottom);
        area.close();
        canvas.drawPath(area, p..color = fill);
        canvas.drawPath(
            line,
            Paint()
              ..color = st.color
              ..style = PaintingStyle.stroke
              ..strokeWidth = st.strokeWidth);
      }
    }

    final h = hover;
    if (h != null && h >= lane.start && h <= end) {
      canvas.drawRect(Rect.fromLTWH(x(h), 0, 1, size.height), p..color = st.value.color ?? st.color);
    }
    canvas.restore();
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, 1), p..color = st.divider);
  }

  @override
  bool shouldRepaint(_LanePainter old) => true;
}
