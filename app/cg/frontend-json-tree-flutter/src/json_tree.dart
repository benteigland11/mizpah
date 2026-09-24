/// JSON as a tree you fold open, instead of a wall of text.
///
/// ```dart
/// JsonTree(value: parseJsonDocument(text), expandDepth: 2);
/// ```
///
/// Objects and arrays fold (tap the row); folded ones show `{3 keys}` or
/// `[12 items]`. Big arrays and objects show [JsonTree.pageSize] children
/// and a row to show more. A long string shows one line; tap it to see all.
/// Rows are built lazily, so a large document scrolls smoothly. A
/// [JsonTreeController] expands or collapses everything from outside.
library;

import 'dart:convert';

import 'package:flutter/widgets.dart';

/// Decodes [text] as one JSON document or, failing that, as JSON Lines (one
/// value per non-empty line, returned as a list). Throws [FormatException]
/// when it is neither.
Object? parseJsonDocument(String text) {
  try {
    return jsonDecode(text);
  } on FormatException catch (whole) {
    final lines = text.split('\n').where((l) => l.trim().isNotEmpty).toList();
    if (lines.length < 2) rethrow;
    try {
      return [for (final l in lines) jsonDecode(l)];
    } on FormatException {
      throw whole;
    }
  }
}

/// A JSONPath-style address for a node: `$`, `$.items[2].name`,
/// `$["odd key"]`.
String jsonPath(List<Object> segments) {
  final b = StringBuffer(r'$');
  for (final s in segments) {
    if (s is int) {
      b.write('[$s]');
    } else if (RegExp(r'^[A-Za-z_][A-Za-z0-9_]*$').hasMatch('$s')) {
      b.write('.$s');
    } else {
      b.write('[${jsonEncode('$s')}]');
    }
  }
  return b.toString();
}

/// Expands or collapses a [JsonTree] from outside.
class JsonTreeController extends ChangeNotifier {
  int _command = 0; // 1 expand all, -1 collapse all
  int _generation = 0;

  /// Unfold every object and array.
  void expandAll() {
    _command = 1;
    _generation++;
    notifyListeners();
  }

  /// Fold everything but the root.
  void collapseAll() {
    _command = -1;
    _generation++;
    notifyListeners();
  }
}

/// Colours and sizes of a [JsonTree].
@immutable
class JsonTreeStyle {
  /// Creates a style; the defaults suit a dark background.
  const JsonTreeStyle({
    this.text = const TextStyle(fontFamily: 'monospace', fontSize: 12.5, height: 1.5, color: Color(0xFFE6E8EB)),
    this.keyColor = const Color(0xFF4FC3D9),
    this.indexColor = const Color(0xFF8A8A86),
    this.stringColor = const Color(0xFF5FD38D),
    this.numberColor = const Color(0xFFF5C84A),
    this.literalColor = const Color(0xFFFF6B5B),
    this.punctuationColor = const Color(0xFF8A8A86),
    this.summaryColor = const Color(0xFF777777),
    this.guideColor = const Color(0x22FFFFFF),
    this.selectedColor = const Color(0x225B9BFF),
    this.indent = 16,
    this.padding = const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
  });

  /// Base text (family, size, line height, default colour).
  final TextStyle text;

  /// Object keys.
  final Color keyColor;

  /// Array indexes.
  final Color indexColor;

  /// String values.
  final Color stringColor;

  /// Numbers.
  final Color numberColor;

  /// true, false and null.
  final Color literalColor;

  /// Colons, brackets and the fold arrow.
  final Color punctuationColor;

  /// `{3 keys}`, `[12 items]` and "show more".
  final Color summaryColor;

  /// Vertical lines marking depth.
  final Color guideColor;

  /// Background of the selected row.
  final Color selectedColor;

  /// Width of one level of nesting.
  final double indent;

  /// Space around the rows.
  final EdgeInsets padding;
}

class _Row {
  _Row(this.path, this.id, this.depth, this.label, this.value, {this.more = 0, this.moreOf});
  final List<Object> path;
  final String id;
  final int depth;
  final Object? label; // String key, int index, or null for the root
  final Object? value;
  final int more; // > 0: a "show more" row for `moreOf`
  final String? moreOf;
}

/// A foldable view of a decoded JSON value.
class JsonTree extends StatefulWidget {
  /// Creates the tree for [value] (what `jsonDecode` returns).
  const JsonTree({
    super.key,
    required this.value,
    this.style = const JsonTreeStyle(),
    this.expandDepth = 1,
    this.pageSize = 100,
    this.controller,
    this.onSelect,
    this.shrinkWrap = false,
  });

  /// The decoded value: maps, lists, strings, numbers, bools and null.
  final Object? value;

  /// Colours and sizes.
  final JsonTreeStyle style;

  /// Levels unfolded at first: 0 shows the root folded, 1 opens the root.
  final int expandDepth;

  /// Children shown before a "show more" row.
  final int pageSize;

  /// Expand or collapse all from outside.
  final JsonTreeController? controller;

  /// Called with the [jsonPath] of a tapped row.
  final ValueChanged<String>? onSelect;

  /// Size to the rows instead of filling and scrolling.
  final bool shrinkWrap;

  @override
  State<JsonTree> createState() => _JsonTreeState();
}

class _JsonTreeState extends State<JsonTree> {
  final Set<String> _open = {};
  final Set<String> _unwrapped = {};
  final Map<String, int> _shown = {};
  String? _selected;
  int _seen = 0;

  @override
  void initState() {
    super.initState();
    _openTo(widget.value, const [], widget.expandDepth);
    widget.controller?.addListener(_command);
  }

  @override
  void didUpdateWidget(JsonTree old) {
    super.didUpdateWidget(old);
    if (old.controller != widget.controller) {
      old.controller?.removeListener(_command);
      widget.controller?.addListener(_command);
    }
    if (!identical(old.value, widget.value)) {
      _open.clear();
      _unwrapped.clear();
      _shown.clear();
      _selected = null;
      _openTo(widget.value, const [], widget.expandDepth);
    }
  }

  @override
  void dispose() {
    widget.controller?.removeListener(_command);
    super.dispose();
  }

  void _command() {
    final c = widget.controller!;
    if (c._generation == _seen) return;
    _seen = c._generation;
    setState(() {
      _open.clear();
      if (c._command > 0) {
        _openTo(widget.value, const [], 1 << 30);
      } else {
        _openTo(widget.value, const [], 1);
      }
    });
  }

  static String _id(List<Object> path) => jsonPath(path);

  void _openTo(Object? v, List<Object> path, int depth) {
    if (depth <= 0 || (v is! Map && v is! List)) return;
    _open.add(_id(path));
    if (v is Map) {
      for (final e in v.entries) {
        _openTo(e.value, [...path, '${e.key}'], depth - 1);
      }
    } else if (v is List) {
      for (var i = 0; i < v.length; i++) {
        _openTo(v[i], [...path, i], depth - 1);
      }
    }
  }

  List<_Row> _rows() {
    final out = <_Row>[];
    void walk(Object? v, List<Object> path, int depth, Object? label) {
      final id = _id(path);
      out.add(_Row(path, id, depth, label, v));
      if (!_open.contains(id)) return;
      final limit = _shown[id] ?? widget.pageSize;
      if (v is Map) {
        var i = 0;
        for (final e in v.entries) {
          if (i++ >= limit) break;
          walk(e.value, [...path, '${e.key}'], depth + 1, '${e.key}');
        }
        if (v.length > limit) out.add(_Row(path, '$id#more', depth + 1, null, null, more: v.length - limit, moreOf: id));
      } else if (v is List) {
        for (var i = 0; i < v.length && i < limit; i++) {
          walk(v[i], [...path, i], depth + 1, i);
        }
        if (v.length > limit) out.add(_Row(path, '$id#more', depth + 1, null, null, more: v.length - limit, moreOf: id));
      }
    }

    walk(widget.value, const [], 0, null);
    return out;
  }

  static String _count(int n, String one, String many) => '$n ${n == 1 ? one : many}';

  Widget _row(_Row r) {
    final st = widget.style;
    final t = st.text;
    TextSpan span(String s, Color c) => TextSpan(text: s, style: t.copyWith(color: c));
    final left = st.padding.left + r.depth * st.indent;

    if (r.more > 0) {
      return GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => setState(() => _shown[r.moreOf!] = (_shown[r.moreOf!] ?? widget.pageSize) + widget.pageSize),
        child: Padding(
          padding: EdgeInsets.only(left: left + 14, right: st.padding.right),
          child: Text('… ${r.more} more — show ${r.more < widget.pageSize ? r.more : widget.pageSize}',
              style: t.copyWith(color: st.summaryColor)),
        ),
      );
    }

    final v = r.value;
    final container = v is Map || v is List;
    final open = _open.contains(r.id);
    final parts = <InlineSpan>[];
    final label = r.label;
    if (label is String) {
      parts..add(span(jsonEncode(label), st.keyColor))..add(span(': ', st.punctuationColor));
    } else if (label is int) {
      parts..add(span('$label', st.indexColor))..add(span(': ', st.punctuationColor));
    }
    var wrap = false;
    if (v is Map) {
      parts.add(span('{', st.punctuationColor));
      parts.add(span(_count(v.length, 'key', 'keys'), st.summaryColor));
      parts.add(span('}', st.punctuationColor));
    } else if (v is List) {
      parts.add(span('[', st.punctuationColor));
      parts.add(span(_count(v.length, 'item', 'items'), st.summaryColor));
      parts.add(span(']', st.punctuationColor));
    } else if (v is String) {
      wrap = _unwrapped.contains(r.id);
      parts.add(span(jsonEncode(v), st.stringColor));
    } else if (v is num) {
      parts.add(span('$v', st.numberColor));
    } else {
      parts.add(span('$v', st.literalColor));
    }

    final selected = _selected == r.id;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => setState(() {
        _selected = r.id;
        if (container) {
          open ? _open.remove(r.id) : _open.add(r.id);
        } else if (v is String) {
          wrap ? _unwrapped.remove(r.id) : _unwrapped.add(r.id);
        }
        widget.onSelect?.call(r.id);
      }),
      child: Container(
        color: selected ? st.selectedColor : null,
        child: CustomPaint(
          painter: _Guides(r.depth, st.padding.left, st.indent, st.guideColor),
          child: Padding(
            padding: EdgeInsets.only(left: left, right: st.padding.right),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 14,
                  child: container
                      ? Text(open ? '▾' : '▸', style: t.copyWith(color: st.punctuationColor))
                      : null,
                ),
                Expanded(
                  child: Text.rich(
                    TextSpan(children: parts),
                    maxLines: wrap ? null : 1,
                    softWrap: wrap,
                    overflow: wrap ? TextOverflow.visible : TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows();
    final st = widget.style;
    return ListView.builder(
      shrinkWrap: widget.shrinkWrap,
      physics: widget.shrinkWrap ? const NeverScrollableScrollPhysics() : null,
      padding: EdgeInsets.only(top: st.padding.top, bottom: st.padding.bottom),
      itemCount: rows.length,
      itemBuilder: (context, i) => _row(rows[i]),
    );
  }
}

class _Guides extends CustomPainter {
  _Guides(this.depth, this.left, this.indent, this.color);
  final int depth;
  final double left, indent;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()..color = color;
    for (var d = 0; d < depth; d++) {
      final x = left + d * indent + 5;
      canvas.drawRect(Rect.fromLTWH(x, 0, 1, size.height), p);
    }
  }

  @override
  bool shouldRepaint(_Guides old) =>
      old.depth != depth || old.left != left || old.indent != indent || old.color != color;
}
