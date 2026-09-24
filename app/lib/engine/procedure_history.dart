import 'dart:io';

import 'run_tool.dart';

/// One commit that touched a procedure.
class ProcedureVersion {
  const ProcedureVersion({required this.sha, required this.at, required this.author, required this.message});
  final String sha;
  final DateTime at;
  final String author;
  final String message;
  String get short => sha.substring(0, 7);
}

/// A line of a unified diff, already classified.
class DiffHunkLine {
  const DiffHunkLine(this.kind, this.text);

  /// added | removed | context
  final String kind;
  final String text;
}

/// The playbook store as a git repository: log, diff, commit, restore for
/// one procedure's file. The engine commits per work order; the app
/// commits per save. Everything shells to `git`; nothing is held in
/// memory between calls.
class ProcedureHistory {
  ProcedureHistory(this.store);
  final Directory store;

  String _file(String id) => '$id.json';

  Future<ProcessResult> _git(List<String> args) => runTool(
        'git',
        ['-c', 'user.name=mizpah', '-c', 'user.email=mizpah@local', ...args],
        workingDirectory: store.path,
      );

  Future<bool> get isRepo async => Directory('${store.path}/.git').existsSync();

  Future<void> ensureRepo() async {
    if (await isRepo) return;
    await _git(['init', '-q']);
    File('${store.path}/.gitignore').writeAsStringSync('.*.tmp\n');
    await _git(['add', '-A']);
    await _git(['commit', '-q', '-m', 'procedures as found']);
  }

  /// Commits everything pending in the store under [message]. Returns
  /// false when there was nothing to commit.
  Future<bool> commit(String message, {String author = 'person <person@mizpah>'}) async {
    await ensureRepo();
    await _git(['add', '-A']);
    final staged = await _git(['diff', '--cached', '--quiet']);
    if (staged.exitCode == 0) return false;
    final r = await _git(['commit', '-q', '--author', author, '-m', message]);
    return r.exitCode == 0;
  }

  /// Newest first.
  Future<List<ProcedureVersion>> log(String id) async {
    if (!await isRepo) return const [];
    final r = await _git(['log', '--format=%H%x1f%aI%x1f%an%x1f%s', '--', _file(id)]);
    if (r.exitCode != 0) return const [];
    return [
      for (final line in (r.stdout as String).split('\n'))
        if (line.trim().isNotEmpty)
          () {
            final p = line.split('\x1f');
            return ProcedureVersion(
              sha: p[0],
              at: DateTime.tryParse(p.length > 1 ? p[1] : '') ?? DateTime.now(),
              author: p.length > 2 ? p[2] : '',
              message: p.length > 3 ? p[3] : '',
            );
          }(),
    ];
  }

  /// The change [sha] made to the file, as classified lines. With [against]
  /// null it is that commit against its parent; otherwise sha..against.
  Future<List<DiffHunkLine>> diff(String id, String sha, {String? against}) async {
    final range = against == null ? ['$sha^!'] : [sha, against];
    final r = await _git(['diff', '--unified=2', '--no-color', ...range, '--', _file(id)]);
    if (r.exitCode != 0) {
      // A first commit has no parent: show it as all added.
      final show = await _git(['show', '$sha:${_file(id)}']);
      if (show.exitCode != 0) return const [];
      return [for (final l in (show.stdout as String).split('\n')) DiffHunkLine('added', l)];
    }
    final out = <DiffHunkLine>[];
    for (final line in (r.stdout as String).split('\n')) {
      if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ')) continue;
      if (line.startsWith('@@')) {
        out.add(DiffHunkLine('hunk', line));
      } else if (line.startsWith('+')) {
        out.add(DiffHunkLine('added', line.substring(1)));
      } else if (line.startsWith('-')) {
        out.add(DiffHunkLine('removed', line.substring(1)));
      } else if (line.startsWith(' ')) {
        out.add(DiffHunkLine('context', line.substring(1)));
      }
    }
    return out;
  }

  /// Put the file back as it was at [sha], as a new commit (history is
  /// never rewritten).
  Future<bool> restore(String id, String sha) async {
    final r = await _git(['checkout', sha, '--', _file(id)]);
    if (r.exitCode != 0) return false;
    return commit('restore $id to ${sha.substring(0, 7)}');
  }
}
