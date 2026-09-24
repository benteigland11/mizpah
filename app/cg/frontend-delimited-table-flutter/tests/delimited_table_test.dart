import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:delimited_table/delimited_table.dart';

void main() {
  test('sniffDelimiter', () {
    expect(sniffDelimiter('a,b,c\n1,2,3'), ',');
    expect(sniffDelimiter('a\tb\n1\t2'), '\t');
    expect(sniffDelimiter('a;b;c\n1,5;2;3'), ';');
    expect(sniffDelimiter('"x,y"|b\n1|2'), '|');
    expect(sniffDelimiter('just words'), ',');
    expect(sniffDelimiter(''), ',');
  });

  test('header, numeric columns, quotes, ragged rows, BOM', () {
    final t = parseDelimited('﻿bar,onset_s,note\n1,0.000,"a, quoted"\n2,2.143,"two\nlines"\n3,4.286\n\n');
    expect(t.hasHeader, isTrue);
    expect(t.header, ['bar', 'onset_s', 'note']);
    expect(t.columns, 3);
    expect(t.rows.length, 3);
    expect(t.rows[0][2], 'a, quoted');
    expect(t.rows[1][2], 'two\nlines');
    expect(t.rows[2], ['3', '4.286', '']);
    expect(t.numeric, [true, true, false]);
    expect(t.delimiter, ',');
    expect(t.truncated, isFalse);
  });

  test('no header: lettered columns; overrides; truncation', () {
    final t = parseDelimited('1,2\n3,4');
    expect(t.hasHeader, isFalse);
    expect(t.header, ['A', 'B']);
    expect(t.rows.length, 2);
    final forced = parseDelimited('x\ty\n1\t2', hasHeader: false);
    expect(forced.header, ['A', 'B']);
    expect(forced.delimiter, '\t');
    final wide = parseDelimited([for (var i = 0; i < 30; i++) 'v$i'].join(';'), delimiter: ';', hasHeader: false);
    expect(wide.header.last, 'AD');
    final cut = parseDelimited('n\n1\n2\n3', maxRows: 2);
    expect(cut.rows.length, 2);
    expect(cut.truncated, isTrue);
    expect(parseDelimited('').columns, 0);
  });

  testWidgets('view: header, row numbers, lazy rows, wide tables scroll', (t) async {
    final table = parseDelimited([
      'id,name,value',
      for (var i = 0; i < 500; i++) '$i,item-$i,${i * 1.5}',
    ].join('\n'));
    await t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(width: 300, height: 200, child: DelimitedTableView(table: table)),
    ));
    expect(find.text('name'), findsOneWidget);
    expect(find.text('item-0'), findsOneWidget);
    expect(find.text('item-400'), findsNothing);
    expect(find.text('1'), findsWidgets);

    await t.pumpWidget(Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(
        width: 200,
        height: 200,
        child: DelimitedTableView(
          table: parseDelimited('a,b,c,d,e,f\n${'x' * 300},2,3,4,5,6'),
          rowNumbers: false,
          style: const DelimitedTableStyle(maxColumnWidth: 120),
        ),
      ),
    ));
    expect(find.text('a'), findsOneWidget);

    // Wide ambient letter spacing widens the columns so headers still fit.
    Future<double> nameWidth(double spacing) async {
      await t.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: DefaultTextStyle(
          style: TextStyle(letterSpacing: spacing),
          child: SizedBox(width: 600, height: 200, child: DelimitedTableView(table: parseDelimited('name,v\nx,1'))),
        ),
      ));
      return t.getSize(find.ancestor(of: find.text('name'), matching: find.byType(Container)).first).width;
    }
    expect(await nameWidth(4), greaterThan(await nameWidth(0)));
    expect(t.takeException(), isNull);
  });
}
