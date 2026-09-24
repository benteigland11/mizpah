import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/run_tool.dart';

void main() {
  test('returns the exit code and both streams', () async {
    final r = await runTool('sh', ['-c', 'echo out; echo err >&2; exit 3']);
    expect(r.exitCode, 3);
    expect((r.stdout as String).trim(), 'out');
    expect((r.stderr as String).trim(), 'err');
  });

  test('a tool past its ceiling is killed and reported', () async {
    final sw = Stopwatch()..start();
    await expectLater(
      runTool('sh', ['-c', 'sleep 5'], timeout: const Duration(milliseconds: 300)),
      throwsA(isA<ToolTimeout>().having((e) => '$e', 'message', contains('no answer in 0s'))),
    );
    expect(sw.elapsed.inSeconds, lessThan(3));
  });
}
