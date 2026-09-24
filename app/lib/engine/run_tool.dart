import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// A tool that did not answer in time. Its process was killed.
class ToolTimeout implements Exception {
  ToolTimeout(this.executable, this.args, this.limit);
  final String executable;
  final List<String> args;
  final Duration limit;
  @override
  String toString() =>
      '${executable.split(RegExp(r'[/\\]')).last} ${args.take(2).join(' ')} gave no answer in ${limit.inSeconds}s';
}

/// `Process.run` with a ceiling. Every CLI the app calls goes through
/// here so a hung tool ends as an error in the UI, not a button stuck on
/// "SIGNING…". The process is killed on timeout; stdout and stderr are
/// returned decoded, as `Process.run` gives them.
Future<ProcessResult> runTool(
  String executable,
  List<String> args, {
  String? workingDirectory,
  Map<String, String>? environment,
  Duration timeout = const Duration(seconds: 30),
}) async {
  final p = await Process.start(executable, args, workingDirectory: workingDirectory, environment: environment);
  final out = p.stdout.transform(utf8.decoder).join();
  final err = p.stderr.transform(utf8.decoder).join();
  final code = await p.exitCode.timeout(timeout, onTimeout: () {
    p.kill();
    return -1;
  });
  if (code == -1) throw ToolTimeout(executable, args, timeout);
  return ProcessResult(p.pid, code, await out, await err);
}
