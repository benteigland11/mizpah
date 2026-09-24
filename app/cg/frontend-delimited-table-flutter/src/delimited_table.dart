/// CSV, TSV and other delimited text shown as a table.
///
/// ```dart
/// final table = parseDelimited(text);          // sniffs `,` `\t` `;` `|`
/// DelimitedTableView(table: table);
/// ```
///
/// Parsing (quoted fields, embedded newlines, escaped quotes) is the csv
/// package's; this adds delimiter sniffing, header detection, numeric
/// columns (right-aligned) and ragged-row padding. The view keeps the header
/// in place, scrolls sideways when wide, numbers the rows, and builds rows
/// lazily so a long file scrolls smoothly.
library;

import 'dart:math' as math;

import 'package:csv/csv.dart';
import 'package:flutter/widgets.dart';

/// A parsed delimited file.
class DelimitedTable {
  /// Creates a table.
  const DelimitedTable({
    required this.header,
    required this.rows,
    required this.delimiter,
    required this.hasHeader,
    required this.numeric,
    this.truncated = false,
  });

  /// Column names: the first row, or A, B, C… when the file has none.
  final List<String> header;

  /// Body rows, each padded to [columns] cells.
  final List<List<String>> rows;

  /// The field delimiter used.
  final String delimiter;

  /// Whether the first row was taken as the header.
  final bool hasHeader;

  /// Per column: most non-empty cells are numbers.
  final List<bool> numeric;

  /// More rows were in the file than were kept.
  final bool truncated;

  /// Number of columns.
  int get columns => header.length;
}

const List<String> _candidates = [',', '\t', ';', '|'];

/// The likeliest field delimiter of [text] among `,` tab `;` `|`: the one
/// that splits the first lines (outside quotes) into the most consistent
/// number of fields above one. `,` when nothing fits.
String sniffDelimiter(String text) {
  final lines = text.split('\n').where((l) => l.trim().isNotEmpty).take(20).toList();
  var best = ',';
  var bestScore = 0.0;
  for (final d in _candidates) {
    final counts = [for (final l in lines) _countOutsideQuotes(l, d)];
    if (counts.isEmpty || counts.every((c) => c == 0)) continue;
    final first = counts.first;
    final same = counts.where((c) => c == first).length / counts.length;
    final score = same * (first + 1);
    if (first > 0 && score > bestScore) {
      best = d;
      bestScore = score;
    }
  }
  return best;
}

int _countOutsideQuotes(String line, String d) {
  var n = 0;
  var quoted = false;
  for (var i = 0; i < line.length; i++) {
    final c = line[i];
    if (c == '"') quoted = !quoted;
    if (!quoted && c == d) n++;
  }
  return n;
}

bool _isNumber(String s) => double.tryParse(s.trim().replaceAll(RegExp(r'[,_](?=\d{3}\b)'), '')) != null;

/// Parses [text] into a [DelimitedTable].
///
/// [delimiter] defaults to [sniffDelimiter]; [hasHeader] defaults to a
/// guess (the first row is all non-empty, non-numeric, distinct names, and
/// the body is not). At most [maxRows] body rows are kept.
DelimitedTable parseDelimited(String text, {String? delimiter, bool? hasHeader, int maxRows = 100000}) {
  final d = delimiter ?? sniffDelimiter(text);
  final src = text.startsWith('\uFEFF') ? text.substring(1) : text;
  final raw = Csv(fieldDelimiter: d, autoDetect: false).decode(src);
  final all = [
    for (final r in raw)
      if (!(r.length == 1 && '${r.first}'.trim().isEmpty)) [for (final c in r) '$c'],
  ];
  final width = all.fold<int>(0, (m, r) => math.max(m, r.length));
  List<String> pad(List<String> r) => r.length < width ? [...r, for (var i = r.length; i < width; i++) ''] : r;

  final first = all.isEmpty ? const <String>[] : pad(all.first);
  final header = hasHeader ??
      (all.length > 1 &&
          first.every((c) => c.trim().isNotEmpty && !_isNumber(c)) &&
          first.toSet().length == first.length &&
          (List.generate(width, (i) => i).any((col) => all.skip(1).take(50).any((r) => r.length > col && _isNumber(r[col]))) ||
              first.every((c) => c.length <= 40)));
  final body = [for (final r in all.skip(header ? 1 : 0)) pad(r)];
  final kept = body.length > maxRows ? body.sublist(0, maxRows) : body;

  final numeric = [
    for (var col = 0; col < width; col++)
      () {
        var cells = 0, nums = 0;
        for (final r in kept.take(500)) {
          final v = r[col].trim();
          if (v.isEmpty) continue;
          cells++;
          if (_isNumber(v)) nums++;
        }
        return cells > 0 && nums / cells >= 0.8;
      }(),
  ];
  return DelimitedTable(
    header: header ? first : [for (var i = 0; i < width; i++) _letters(i)],
    rows: kept,
    delimiter: d,
    hasHeader: header,
    numeric: numeric,
    truncated: body.length > maxRows,
  );
}

String _letters(int i) {
  var s = '';
  var n = i + 1;
  while (n > 0) {
    n--;
    s = String.fromCharCode(65 + n % 26) + s;
    n ~/= 26;
  }
  return s;
}

/// Colours and sizes of a [DelimitedTableView].
@immutable
class DelimitedTableStyle {
  /// Creates a style; the defaults suit a dark background.
  const DelimitedTableStyle({
    this.text = const TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: Color(0xFFE6E8EB)),
    this.headerText = const TextStyle(fontFamily: 'monospace', fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFFBFD9F2)),
    this.rowNumberText = const TextStyle(fontFamily: 'monospace', fontSize: 11, color: Color(0xFF777777)),
    this.background = const Color(0xFF121212),
    this.headerBackground = const Color(0xFF1C1C1C),
    this.stripe = const Color(0xFF161616),
    this.gridLine = const Color(0xFF2A2A2A),
    this.rowHeight = 26,
    this.cellPadding = const EdgeInsets.symmetric(horizontal: 10),
    this.minColumnWidth = 48,
    this.maxColumnWidth = 320,
  });

  /// Body cells.
  final TextStyle text;

  /// Column names.
  final TextStyle headerText;

  /// Row numbers.
  final TextStyle rowNumberText;

  /// Behind the rows.
  final Color background;

  /// Behind the header.
  final Color headerBackground;

  /// Every other row.
  final Color stripe;

  /// Lines between cells.
  final Color gridLine;

  /// Height of every row.
  final double rowHeight;

  /// Space inside a cell.
  final EdgeInsets cellPadding;

  /// Narrowest column.
  final double minColumnWidth;

  /// Widest column (longer cells are cut with an ellipsis).
  final double maxColumnWidth;
}

/// A [DelimitedTable] with a fixed header, row numbers and lazy rows.
class DelimitedTableView extends StatelessWidget {
  /// Creates the view.
  const DelimitedTableView({
    super.key,
    required this.table,
    this.style = const DelimitedTableStyle(),
    this.rowNumbers = true,
  });

  /// The table.
  final DelimitedTable table;

  /// Colours and sizes.
  final DelimitedTableStyle style;

  /// Show a row-number column.
  final bool rowNumbers;

  double _measure(String s, TextStyle st) {
    final tp = TextPainter(text: TextSpan(text: s, style: st), textDirection: TextDirection.ltr, maxLines: 1)..layout();
    return tp.width;
  }

  List<double> _widths(TextStyle ambient) {
    final st = style;
    TextStyle h = ambient.merge(st.headerText), b = ambient.merge(st.text);
    final pad = st.cellPadding.horizontal;
    return [
      for (var c = 0; c < table.columns; c++)
        () {
          var w = _measure(table.header[c], h);
          for (final r in table.rows.take(200)) {
            final cell = r[c];
            if (cell.isEmpty) continue;
            w = math.max(w, _measure(cell.length > 80 ? cell.substring(0, 80) : cell, b));
            if (w + pad >= st.maxColumnWidth) break;
          }
          return (w + pad + 2).clamp(st.minColumnWidth, st.maxColumnWidth).toDouble();
        }(),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final st = style;
    // Measure as the cells will draw: the ambient style (letter spacing,
    // text scaling) merged under the table's own.
    final ambient = DefaultTextStyle.of(context).style;
    final widths = _widths(ambient);
    final numW = rowNumbers
        ? _measure('${table.rows.length}', ambient.merge(st.rowNumberText)) + st.cellPadding.horizontal + 2
        : 0.0;
    final total = numW + widths.fold<double>(0, (s, w) => s + w);
    final line = BorderSide(color: st.gridLine);

    Widget cell(String s, double w, TextStyle ts, {bool right = false}) => Container(
          width: w,
          height: st.rowHeight,
          padding: st.cellPadding,
          alignment: right ? Alignment.centerRight : Alignment.centerLeft,
          decoration: BoxDecoration(border: Border(right: line)),
          child: Text(s.replaceAll('\n', ' ⏎ '), style: ts, maxLines: 1, overflow: TextOverflow.ellipsis),
        );

    Widget row(List<String> cells, TextStyle ts, {String? number, Color? color, bool header = false}) => Container(
          color: color,
          child: Row(children: [
            if (rowNumbers) cell(number ?? '', numW, st.rowNumberText, right: true),
            for (var c = 0; c < cells.length; c++)
              cell(cells[c], widths[c], ts, right: !header && table.numeric[c]),
          ]),
        );

    return ColoredBox(
      color: st.background,
      child: LayoutBuilder(builder: (context, box) {
        final w = math.max(total, box.maxWidth.isFinite ? box.maxWidth : total);
        return SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: SizedBox(
            width: w,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                DecoratedBox(
                  decoration: BoxDecoration(color: st.headerBackground, border: Border(bottom: line)),
                  child: row(table.header, st.headerText, header: true),
                ),
                Expanded(
                  child: ListView.builder(
                    itemCount: table.rows.length,
                    itemExtent: st.rowHeight,
                    itemBuilder: (context, i) =>
                        row(table.rows[i], st.text, number: '${i + 1}', color: i.isOdd ? st.stripe : null),
                  ),
                ),
              ],
            ),
          ),
        );
      }),
    );
  }
}
