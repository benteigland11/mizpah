import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/widgets.dart';

/// A node in one layer (column) of the graph.
class LedgerNode {
  const LedgerNode({
    required this.id,
    required this.layer,
    required this.label,
    this.detail = '',
    this.tone = 0,
    this.weight = 1,
  });
  final String id;

  /// Column index, 0 leftmost.
  final int layer;
  final String label;

  /// Small second line under the label (a value, a count).
  final String detail;

  /// Index into [LedgerGraphStyle.tones]; the caller decides what a tone
  /// means (met, failed, open, …).
  final int tone;

  /// Relative height, for a node that stands for several things.
  final double weight;
}

/// A directed edge between nodes of any two layers (or the same layer).
class LedgerEdge {
  const LedgerEdge({required this.from, required this.to, this.tone = 0, this.dashed = false});
  final String from;
  final String to;
  final int tone;
  final bool dashed;
}

/// Colours and metrics; every visual value is here.
class LedgerGraphStyle {
  const LedgerGraphStyle({
    required this.tones,
    required this.labelStyle,
    required this.detailStyle,
    required this.headingStyle,
    this.edgeColor = const Color(0xFF9E9E9E),
    this.nodeFill = const Color(0xFFFFFFFF),
    this.nodeBorder = const Color(0xFFBDBDBD),
    this.dimAlpha = 0.18,
    this.nodeWidth = 220,
    this.nodeMinHeight = 34,
    this.nodeGap = 10,
    this.layerGap = 90,
    this.padding = 16,
    this.headingHeight = 28,
    this.cornerRadius = 3,
    this.edgeWidth = 1.4,
    this.selectedEdgeWidth = 2.4,
  });

  /// Colour per tone index, used for the node's left bar and its edges.
  final List<Color> tones;
  final TextStyle labelStyle;
  final TextStyle detailStyle;
  final TextStyle headingStyle;
  final Color edgeColor;
  final Color nodeFill;
  final Color nodeBorder;

  /// Alpha of nodes and edges not connected to the selection.
  final double dimAlpha;
  final double nodeWidth;
  final double nodeMinHeight;
  final double nodeGap;
  final double layerGap;
  final double padding;
  final double headingHeight;
  final double cornerRadius;
  final double edgeWidth;
  final double selectedEdgeWidth;
}

/// Columns of nodes with curved edges between them: a ledger read left to
/// right. Nodes are ordered within a column by the mean position of their
/// neighbours in the column to the left (one barycenter pass), which keeps
/// most threads from crossing. Tap a node to select it: its edges and
/// neighbours stay full, everything else dims. The widget sizes itself to
/// its content; wrap it in a scroll view.
class LayeredLedgerGraph extends StatefulWidget {
  const LayeredLedgerGraph({
    super.key,
    required this.nodes,
    required this.edges,
    required this.headings,
    required this.style,
    this.selected,
    this.onSelect,
  });
  final List<LedgerNode> nodes;
  final List<LedgerEdge> edges;

  /// One heading per layer, drawn above the column.
  final List<String> headings;
  final LedgerGraphStyle style;

  /// Selected node id; null for none. When [onSelect] is given the widget
  /// reports taps and the caller owns the selection.
  final String? selected;
  final ValueChanged<String?>? onSelect;

  @override
  State<LayeredLedgerGraph> createState() => _LayeredLedgerGraphState();
}

class _LayeredLedgerGraphState extends State<LayeredLedgerGraph> {
  String? _local;

  String? get _selected => widget.onSelect == null ? _local : widget.selected;

  @override
  Widget build(BuildContext context) {
    final layout = LedgerLayout.compute(widget.nodes, widget.edges, widget.headings.length, widget.style);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTapUp: (d) {
        final hit = layout.hit(d.localPosition);
        final next = hit == _selected ? null : hit;
        if (widget.onSelect != null) {
          widget.onSelect!(next);
        } else {
          setState(() => _local = next);
        }
      },
      child: CustomPaint(
        size: layout.size,
        painter: _Painter(layout, widget.edges, widget.headings, widget.style, _selected),
      ),
    );
  }
}

/// Node rectangles by id, and the canvas size.
class LedgerLayout {
  LedgerLayout._(this.rects, this.size, this.byId);
  final Map<String, Rect> rects;
  final Size size;
  final Map<String, LedgerNode> byId;

  String? hit(Offset p) {
    for (final e in rects.entries) {
      if (e.value.contains(p)) return e.key;
    }
    return null;
  }

  static LedgerLayout compute(List<LedgerNode> nodes, List<LedgerEdge> edges, int layers, LedgerGraphStyle s) {
    final byId = {for (final n in nodes) n.id: n};
    final columns = List.generate(math.max(layers, 1), (_) => <LedgerNode>[]);
    for (final n in nodes) {
      if (n.layer >= 0 && n.layer < columns.length) columns[n.layer].add(n);
    }
    // Barycenter ordering: a node sits near the mean index of its
    // left-hand neighbours; ties keep the caller's order.
    final order = <String, int>{};
    for (var l = 0; l < columns.length; l++) {
      final col = columns[l];
      if (l > 0) {
        final key = <String, double>{};
        for (var i = 0; i < col.length; i++) {
          final left = <int>[];
          for (final e in edges) {
            final other = e.to == col[i].id ? e.from : e.from == col[i].id ? e.to : null;
            if (other != null && byId[other]?.layer == l - 1 && order.containsKey(other)) {
              left.add(order[other]!);
            }
          }
          key[col[i].id] = left.isEmpty ? i.toDouble() + 1e6 : left.reduce((a, b) => a + b) / left.length;
        }
        col.sort((a, b) => key[a.id]!.compareTo(key[b.id]!));
      }
      for (var i = 0; i < col.length; i++) {
        order[col[i].id] = i;
      }
    }
    final rects = <String, Rect>{};
    var height = 0.0;
    for (var l = 0; l < columns.length; l++) {
      var y = s.padding + s.headingHeight;
      final x = s.padding + l * (s.nodeWidth + s.layerGap);
      for (final n in columns[l]) {
        final h = math.max(s.nodeMinHeight, s.nodeMinHeight * n.weight) + (n.detail.isEmpty ? 0 : 14);
        rects[n.id] = Rect.fromLTWH(x, y, s.nodeWidth, h);
        y += h + s.nodeGap;
      }
      height = math.max(height, y);
    }
    final width = s.padding * 2 + columns.length * s.nodeWidth + (columns.length - 1) * s.layerGap;
    return LedgerLayout._(rects, Size(width, height + s.padding), byId);
  }
}

class _Painter extends CustomPainter {
  _Painter(this.layout, this.edges, this.headings, this.s, this.selected);
  final LedgerLayout layout;
  final List<LedgerEdge> edges;
  final List<String> headings;
  final LedgerGraphStyle s;
  final String? selected;

  Set<String> get _lit {
    if (selected == null) return const {};
    final out = {selected!};
    for (final e in edges) {
      if (e.from == selected) out.add(e.to);
      if (e.to == selected) out.add(e.from);
    }
    return out;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final lit = _lit;
    bool dim(String id) => selected != null && !lit.contains(id);
    for (var l = 0; l < headings.length; l++) {
      final x = s.padding + l * (s.nodeWidth + s.layerGap);
      _text(canvas, headings[l], Offset(x, s.padding), s.headingStyle, s.nodeWidth);
    }
    for (final e in edges) {
      final a = layout.rects[e.from], b = layout.rects[e.to];
      if (a == null || b == null) continue;
      final on = selected != null && (e.from == selected || e.to == selected);
      final faded = selected != null && !on;
      final color = (e.tone < s.tones.length && e.tone > 0 ? s.tones[e.tone] : s.edgeColor)
          .withValues(alpha: faded ? s.dimAlpha : 1);
      final paint = Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = on ? s.selectedEdgeWidth : s.edgeWidth;
      final path = _curve(a, b);
      if (e.dashed) {
        _dashed(canvas, path, paint);
      } else {
        canvas.drawPath(path, paint);
      }
    }
    for (final entry in layout.rects.entries) {
      final n = layout.byId[entry.key]!;
      final r = entry.value;
      final alpha = dim(n.id) ? s.dimAlpha : 1.0;
      final rr = RRect.fromRectAndRadius(r, Radius.circular(s.cornerRadius));
      canvas.drawRRect(rr, Paint()..color = s.nodeFill.withValues(alpha: alpha));
      canvas.drawRRect(
        rr,
        Paint()
          ..color = (n.id == selected ? s.tones[math.min(n.tone, s.tones.length - 1)] : s.nodeBorder).withValues(alpha: alpha)
          ..style = PaintingStyle.stroke
          ..strokeWidth = n.id == selected ? 2 : 1,
      );
      canvas.drawRect(
        Rect.fromLTWH(r.left, r.top, 4, r.height),
        Paint()..color = s.tones[math.min(n.tone, s.tones.length - 1)].withValues(alpha: alpha),
      );
      _text(canvas, n.label, Offset(r.left + 10, r.top + 7), s.labelStyle.copyWith(color: s.labelStyle.color?.withValues(alpha: alpha)),
          r.width - 16, maxLines: n.detail.isEmpty ? 2 : 1);
      if (n.detail.isNotEmpty) {
        _text(canvas, n.detail, Offset(r.left + 10, r.bottom - 18),
            s.detailStyle.copyWith(color: s.detailStyle.color?.withValues(alpha: alpha)), r.width - 16);
      }
    }
  }

  Path _curve(Rect a, Rect b) {
    final path = Path();
    if (a.left == b.left) {
      // Same column: an arc out to the right and back.
      final p0 = Offset(a.right, a.center.dy), p1 = Offset(b.right, b.center.dy);
      final bulge = s.layerGap * 0.4;
      path.moveTo(p0.dx, p0.dy);
      path.cubicTo(p0.dx + bulge, p0.dy, p1.dx + bulge, p1.dy, p1.dx, p1.dy);
      return path;
    }
    final left = a.left < b.left ? a : b, right = a.left < b.left ? b : a;
    final p0 = Offset(left.right, left.center.dy), p1 = Offset(right.left, right.center.dy);
    final mid = (p0.dx + p1.dx) / 2;
    path.moveTo(p0.dx, p0.dy);
    path.cubicTo(mid, p0.dy, mid, p1.dy, p1.dx, p1.dy);
    return path;
  }

  void _dashed(Canvas canvas, Path path, Paint paint) {
    for (final metric in path.computeMetrics()) {
      var d = 0.0;
      while (d < metric.length) {
        final end = math.min(d + 6, metric.length);
        canvas.drawPath(metric.extractPath(d, end), paint);
        d = end + 5;
      }
    }
  }

  void _text(Canvas canvas, String text, Offset at, TextStyle style, double width, {int maxLines = 1}) {
    final tp = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: TextDirection.ltr,
      maxLines: maxLines,
      ellipsis: '…',
    )..layout(maxWidth: width);
    tp.paint(canvas, at);
  }

  @override
  bool shouldRepaint(_Painter old) =>
      old.layout != layout || old.selected != selected || old.edges != edges || old.s != s;
}
