// The desk's own paper: the Board's notices from files, each sent to a desk
// once and read from the file every time; the Deputy's from its jsonl, one
// line each, latest per number; both read into the tray.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/desk_notices.dart';
import 'package:mizpah_app/engine/local_engine.dart';
import 'package:mizpah_app/models/document.dart';

void main() {
  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('notices'));
  tearDown(() => tmp.deleteSync(recursive: true));

  Directory boardDir(Map<String, String> files) {
    final d = Directory('${tmp.path}/notices/board')..createSync(recursive: true);
    files.forEach((name, text) => File('${d.path}/$name').writeAsStringSync(text));
    return d;
  }

  DeskNotices desk(Directory? dir) => DeskNotices(
    boardDir: dir,
    boardFile: File('${tmp.path}/state/board.json'),
    deputyFile: File('${tmp.path}/state/deputy/notices.jsonl'),
  );

  const welcome = '---\nnumber: BN-001\ntitle: Welcome aboard.\nstatus: WELCOME\n---\n## Welcome\nGlad to have you.\n';
  const checklist = '---\nnumber: BN-002\ntitle: Before the first brief: \$steps things to set up.\nstatus: FOR ACTION\n---\n'
      '## Do\n- [Settings](nav:settings/signature) Set how you sign.\n\n- [Home](nav:home) Talk to the Deputy.\n`terra gate`\n**mind the bill**\n> a quote\nThat is \$steps, and [Home](nav:home) is where the [Deputy](nav:home) sits.\n';

  test('the notice format parses to a sheet', () {
    final d = parseNotice(checklist, from: 'The Board', kind: DocKind.boardNotice)!;
    expect((d.number, d.title, d.status, d.hot), ('BN-002', 'Before the first brief: two things to set up.', 'FOR ACTION', false));
    expect(d.sections.single.heading, 'Do');
    final l = d.sections.single.lines;
    expect((l[0].lead, l[0].link, l[0].text), ('Settings', 'nav:settings/signature', 'Set how you sign.'));
    expect((l[1].lead, l[1].link), ('Home', 'nav:home'));
    expect((l[2].text, l[2].mono), ('terra gate', true));
    expect((l[3].text, l[3].emphasis), ('mind the bill', true));
    expect((l[4].text, l[4].quote), ('a quote', true));
    expect(l[5].text, 'That is two, and [Home](nav:home) is where the [Deputy](nav:home) sits.');   // the count fills in the body too
    expect(l[5].rich, isTrue);                                                                    // and inline links stay in the text as runs
    expect(l[5].runs, [('That is two, and ', ''), ('Home', 'nav:home'), (' is where the ', ''), ('Deputy', 'nav:home'), (' sits.', '')]);
    expect(l[0].rich, isFalse);
    expect(DocLine.fromJson(l[5].toJson()).runs, l[5].runs);                                      // rich survives the wire
    expect(parseNotice('no front matter', from: 'x', kind: DocKind.boardNotice), isNull);
    expect(parseNotice('---\ntitle: no number\n---\nx', from: 'x', kind: DocKind.boardNotice), isNull);
  });

  test('a fresh desk is sent every file once, in name order, the first on top', () {
    final dir = boardDir({'BN-001-welcome.md': welcome, 'BN-002-checklist.md': checklist});
    final first = desk(dir).read().map((i) => i.document).toList();
    expect(first.map((d) => d.number), ['BN-001', 'BN-002']);
    expect(first.map((d) => d.from).toSet(), {'The Board'});
    expect(first[0].at!.isAfter(first[1].at!), isTrue);
    // Read again: the same times — sent once — and the record is by number.
    final again = desk(dir).read().map((i) => i.document).toList();
    expect(again.map((d) => d.at), first.map((d) => d.at));
    expect((jsonDecode(File('${tmp.path}/state/board.json').readAsStringSync()) as Map).keys, ['BN-001', 'BN-002']);
  });

  test('an edit shows at once; a new file is sent to an existing desk', () {
    final dir = boardDir({'BN-001-welcome.md': welcome});
    final before = desk(dir).read().single.document;
    File('${dir.path}/BN-001-welcome.md').writeAsStringSync(welcome.replaceFirst('Glad to have you.', 'Very glad.'));
    File('${dir.path}/BN-003-later.md').writeAsStringSync(welcome.replaceFirst('BN-001', 'BN-003'));
    final after = desk(dir).read().map((i) => i.document).toList();
    expect(after[0].sections.single.lines.single.text, 'Very glad.');
    expect(after[0].at, before.at);                       // the edit did not resend it
    expect(after.map((d) => d.number), ['BN-001', 'BN-003']);
    expect(after[1].at, isNotNull);                       // the new one was sent now
  });

  test('no folder, no Board paper; an older desk\'s record of sent documents carries over', () {
    expect(desk(null).read(), isEmpty);
    expect(desk(Directory('${tmp.path}/nowhere')).read(), isEmpty);
    File('${tmp.path}/state/board.json')
      ..parent.createSync(recursive: true)
      ..writeAsStringSync(jsonEncode([{'kind': 'boardNotice', 'number': 'BN-001', 'title': 'old', 'at': '2026-09-01T00:00:00.000Z', 'from': 'The Board', 'status': 'x', 'header': [], 'sections': []}]));
    final d = desk(boardDir({'BN-001-welcome.md': welcome})).read().single.document;
    expect(d.at, DateTime.parse('2026-09-01T00:00:00.000Z'));
    expect(d.title, 'Welcome aboard.');                   // text from the file, time from the record
  });

  test('the Deputy\'s notices come in beside the Board\'s, latest per number', () {
    final deputyFile = File('${tmp.path}/state/deputy/notices.jsonl')..parent.createSync(recursive: true);
    String doc(String number, String title, {bool hot = false}) => jsonEncode({
      'kind': 'deputyNotice', 'number': number, 'title': title, 'at': '2026-09-21T20:00:00Z', 'from': 'The Deputy',
      'status': 'READY', 'header': [], 'sections': [{'heading': 'The draft', 'lines': [{'text': 'x', 'lead': 'Draft', 'mono': true, 'link': 'brief:gyms/x'}]}],
      if (hot) 'hot': true,
    });
    deputyFile.writeAsStringSync([doc('DN-draft-x', 'first'), doc('DN-outage-1', 'quiet', hot: true), doc('DN-draft-x', 'revised')].map((d) => '$d\n').join());
    final items = desk(boardDir({'BN-001-welcome.md': welcome})).read();
    expect(items.map((i) => '${i.project.id}:${i.document.number}'), ['board:BN-001', 'deputy:DN-draft-x', 'deputy:DN-outage-1']);
    final draft = items[1].document;
    expect(draft.kind, DocKind.deputyNotice);
    expect(draft.title, 'revised');
    expect(draft.sections.single.lines.single.link, 'brief:gyms/x');
    expect(items[2].document.hot, isTrue);
  });

  test('the repository\'s own Board files parse and the engine reads them into the tray', () async {
    final repo = Directory('${Directory.current.path}/../notices/board');
    expect(repo.existsSync(), isTrue, reason: 'notices/board is beside app/');
    final engine = LocalEngine(runsRoot: Directory('${tmp.path}/tasks'), marksFile: File('${tmp.path}/state/acknowledged.json'), boardDir: repo);
    final items = (await engine.readAttention()).where((i) => i.project.id == 'board').toList();
    expect(items.map((i) => i.document.number), ['BN-001', 'BN-002', 'BN-003']);
    expect(items[1].document.sections.expand((s) => s.lines).map((l) => l.link).where((l) => l.isNotEmpty), ['nav:providers']);
    expect(items[2].document.sections.map((s) => s.heading), ['Where things are', 'How it thinks']);
    engine.dispose();
  });
}
