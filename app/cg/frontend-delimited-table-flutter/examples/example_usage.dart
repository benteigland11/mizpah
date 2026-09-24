import 'package:delimited_table/delimited_table.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

/// Show a small readings file: the header and numeric columns are found
/// without being told.
void main() {
  testWidgets('show a readings file', (tester) async {
    const text = 'bar\tonset_s\tvelocity\tpedal\n1\t0.000\t72\tdown\n2\t2.143\t78\tup\n';
    final table = parseDelimited(text);
    expect(table.delimiter, '\t');
    expect(table.header, ['bar', 'onset_s', 'velocity', 'pedal']);
    expect(table.numeric, [true, true, true, false]);

    await tester.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: DelimitedTableView(table: table),
    ));
    expect(find.text('velocity'), findsOneWidget);
  });
}
