import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:syntax_highlighted_code/syntax_highlighted_code.dart';

/// Preview a config file by its name, and colour a tool's output whose
/// language is only known by looking at it.
void main() {
  testWidgets('preview a file and a tool result', (tester) async {
    const path = 'environments/example/base.json';
    const file = '{\n  "name": "example",\n  "network": [],\n  "timeout": 30\n}';
    const toolOutput = '[{"id": "item-1", "ok": true}]';

    expect(languageForPath(path), 'json');
    expect(guessLanguage(toolOutput), 'json');

    await tester.pumpWidget(const Directionality(
      textDirection: TextDirection.ltr,
      child: Column(children: [
        HighlightedCode(text: file, language: 'json', lineNumbers: true),
        HighlightedCode(text: toolOutput, language: 'json'),
      ]),
    ));
    expect(find.text('1\n2\n3\n4\n5'), findsOneWidget);
  });
}
