import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../engine/desk.dart';
import '../../engine/engine.dart' show BriefSummary;
import '../../models/environment.dart';
import '../../state/app_nav.dart';
import '../../state/brief_manager.dart';
import '../../state/gyms_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/ops.dart';
import '../../widgets/unsaved_dialog.dart';

/// Gyms: the environments a gym runs in. Every environment is a root in the
/// explorer; picking one shows its overview (what the worker is told and
/// gets, what is installed, which gyms run in it), picking a file shows it
/// read-only. Editing happens in the desk's code editor — OPEN IN EDITOR. A
/// run freezes its environment when it starts: edits reach the next run.
class GymsScreen extends StatelessWidget {
  const GymsScreen({super.key, required this.manager, required this.briefs, required this.nav});
  final GymsManager manager;

  /// The tasks, for the folders that run in an environment: their titles and
  /// states, walking into one, deleting one.
  final BriefManager briefs;
  final AppNav nav;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: Listenable.merge([manager, briefs]),
      builder: (context, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SizedBox(width: 300, child: _Explorer(manager: manager)),
                VerticalDivider(width: 1, color: cs.outlineVariant),
                Expanded(child: _Pane(manager: manager, briefs: briefs, nav: nav)),
              ],
            ),
          ),
          Divider(height: 1, color: cs.outlineVariant),
          _StatusBar(manager: manager),
        ],
      ),
    );
  }
}

String _size(int bytes) => bytes >= 1e9
    ? '${(bytes / 1e9).toStringAsFixed(1)} GB'
    : bytes >= 1e6
    ? '${(bytes / 1e6).toStringAsFixed(1)} MB'
    : bytes >= 1e3
    ? '${(bytes / 1e3).toStringAsFixed(0)} KB'
    : '$bytes B';

Future<String?> _askName(BuildContext context) async {
  final c = TextEditingController();
  final v = await showOpsDialog<String>(
    context,
    tag: 'NEW ENVIRONMENT',
    title: 'What is it called?',
    body: 'Lowercase letters, digits, - and _. It starts empty, with a base.json; fill it in the editor.',
    field: (ctx) => TextField(
      controller: c,
      autofocus: true,
      style: AppTheme.mono,
      onSubmitted: (v) => Navigator.pop(ctx, v),
      decoration: const InputDecoration(hintText: 'lean-4'),
    ),
    actions: (ctx) => Row(
      children: [
        FilledButton(onPressed: () => Navigator.pop(ctx, c.text), child: const Text('CREATE')),
        const SizedBox(width: Sp.m),
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('CANCEL')),
      ],
    ),
  );
  c.dispose();
  final t = v?.trim() ?? '';
  return t.isEmpty ? null : t;
}

/// OPEN IN EDITOR: the desk's code editor on this environment (and file).
/// Not offered in a browser: the editor opens on the desk.
class _OpenInEditor extends StatelessWidget {
  const _OpenInEditor({required this.manager, required this.env, this.path = ''});
  final GymsManager manager;
  final String env;
  final String path;

  @override
  Widget build(BuildContext context) => kIsWeb
      ? const SizedBox.shrink()
      : OutlinedButton.icon(
          onPressed: () => manager.openInEditor(env, path),
          icon: const Icon(Icons.open_in_new, size: 14),
          label: const Text('OPEN IN EDITOR'),
        );
}

// ---- explorer -----------------------------------------------------------------

typedef _Line = ({String env, String path, FileNode? node, int depth});

class _Explorer extends StatelessWidget {
  const _Explorer({required this.manager});
  final GymsManager manager;

  List<_Line> _lines() {
    final out = <_Line>[];
    void folder(String env, String path, int depth) {
      for (final f in manager.folders[GymsManager.keyOf(env, path)] ?? const <FileNode>[]) {
        final p = path.isEmpty ? f.name : '$path/${f.name}';
        out.add((env: env, path: p, node: f, depth: depth));
        if (f.isDir && f.link == null && manager.unfolded.contains(GymsManager.keyOf(env, p))) folder(env, p, depth + 1);
      }
    }

    for (final e in manager.environments) {
      out.add((env: e.name, path: '', node: null, depth: 0));
      if (manager.unfolded.contains(GymsManager.keyOf(e.name, ''))) folder(e.name, '', 1);
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final lines = _lines();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Sp.l, Sp.m, Sp.s, Sp.s),
          child: Row(
            children: [
              Text('EXPLORER', style: opsLabelStyle(context)),
              const Spacer(),
              IconButton(
                tooltip: 'New environment',
                iconSize: 16,
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.add),
                onPressed: () async {
                  final n = await _askName(context);
                  if (n != null) await manager.createEnvironment(n);
                },
              ),
              IconButton(
                tooltip: 'Read again from disk',
                iconSize: 16,
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.refresh),
                onPressed: manager.loading ? null : manager.reload,
              ),
            ],
          ),
        ),
        if (manager.problem != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(Sp.l, 0, Sp.l, Sp.s),
            child: Text(manager.problem!, style: AppTheme.mono.copyWith(fontSize: 11.5, color: cs.error)),
          ),
        Divider(height: 1, color: cs.outlineVariant),
        Expanded(
          child: manager.loading && manager.environments.isEmpty
              ? Center(child: Text('Reading…', style: opsKeyStyle(context)))
              : ListView.builder(
                  padding: const EdgeInsets.symmetric(vertical: Sp.xs),
                  itemCount: lines.length,
                  itemBuilder: (context, i) => _ExplorerRow(line: lines[i], manager: manager),
                ),
        ),
      ],
    );
  }
}

class _ExplorerRow extends StatelessWidget {
  const _ExplorerRow({required this.line, required this.manager});
  final _Line line;
  final GymsManager manager;

  bool get _isRoot => line.node == null;
  bool get _isDir => _isRoot || (line.node!.isDir && line.node!.link == null);

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final env = manager.info(line.env);
    final open = manager.unfolded.contains(GymsManager.keyOf(line.env, line.path));
    final selected = manager.env == line.env && manager.path == line.path;
    final mono = AppTheme.mono.copyWith(fontSize: 12.5, height: 1.3);
    return InkWell(
      onTap: () => manager.select(line.env, line.path, dir: _isDir),
      onDoubleTap: kIsWeb ? null : () => manager.openInEditor(line.env, line.path),
      child: Container(
        color: selected ? cs.surfaceContainerHighest : null,
        padding: EdgeInsets.fromLTRB(8 + 14.0 * line.depth, 3, Sp.m, 3),
        child: Row(
          children: [
            SizedBox(
              width: 18,
              child: _isDir ? Icon(open ? Icons.expand_more : Icons.chevron_right, size: 15, color: cs.onSurfaceVariant) : null,
            ),
            Icon(
              _isRoot
                  ? Icons.fitness_center_outlined
                  : _isDir
                  ? (open ? Icons.folder_open_outlined : Icons.folder_outlined)
                  : line.node!.link != null
                  ? Icons.link
                  : Icons.description_outlined,
              size: 14,
              color: _isRoot ? AppTheme.ink(context) : cs.onSurfaceVariant,
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                _isRoot ? line.env : '${line.node!.name}${line.node!.link != null ? ' → ${line.node!.link}' : ''}',
                overflow: TextOverflow.ellipsis,
                style: _isRoot
                    ? theme.textTheme.bodyMedium!.copyWith(fontWeight: FontWeight.w700, color: AppTheme.ink(context))
                    : mono.copyWith(color: _isDir ? cs.onSurface : cs.onSurfaceVariant),
              ),
            ),
            if (_isRoot && env != null && env.isDefault)
              Text('DEFAULT', style: opsKeyStyle(context).copyWith(fontSize: 10, color: AppTheme.system(cs.brightness))),
            if (!_isRoot && line.node!.size != null) Text(_size(line.node!.size!), style: mono.copyWith(fontSize: 11, color: cs.outline)),
          ],
        ),
      ),
    );
  }
}

// ---- the right-hand pane ------------------------------------------------------------

class _Pane extends StatelessWidget {
  const _Pane({required this.manager, required this.briefs, required this.nav});
  final GymsManager manager;
  final BriefManager briefs;
  final AppNav nav;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final env = manager.env == null ? null : manager.info(manager.env!);
    if (env == null) {
      return Center(child: Text(manager.loading ? 'Reading the environments…' : 'No environments saved yet.', style: opsKeyStyle(context)));
    }
    if (manager.path.isEmpty) return _Overview(env: env, manager: manager, briefs: briefs, nav: nav);
    final crumbs = [env.name, ...manager.path.split('/')].join('  /  ');
    final f = manager.preview;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.m, Sp.l, Sp.m),
          child: Row(
            children: [
              Expanded(child: Text(crumbs, overflow: TextOverflow.ellipsis, style: AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurface))),
              IconButton(
                tooltip: 'Copy path',
                iconSize: 15,
                icon: const Icon(Icons.copy),
                onPressed: () => Clipboard.setData(ClipboardData(text: '${env.path}/${manager.path}')),
              ),
              const SizedBox(width: Sp.s),
              _OpenInEditor(manager: manager, env: env.name, path: manager.path),
            ],
          ),
        ),
        Divider(height: 1, color: cs.outlineVariant),
        Expanded(
          child: manager.isDir
              ? Center(
                  child: Text(
                    'A folder of ${env.name}. Open the files beside it in the explorer, or the whole environment in the editor.',
                    style: opsKeyStyle(context),
                  ),
                )
              : f == null
              ? Center(child: Text('Reading…', style: opsKeyStyle(context)))
              : f.text == null
              ? Center(
                  child: Text('${f.binary ? 'A binary file' : 'Too big to show here'} · ${_size(f.size)}', style: opsKeyStyle(context)),
                )
              : _Preview(text: f.text!),
        ),
      ],
    );
  }
}

/// A file shown read-only: line numbers, no wrapping (long lines scroll sideways).
class _Preview extends StatelessWidget {
  const _Preview({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final style = AppTheme.mono.copyWith(fontSize: 13, height: 1.5, color: cs.onSurface);
    final lines = '\n'.allMatches(text).length + 1;
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(vertical: Sp.s),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: Sp.m),
            child: Text([for (var i = 1; i <= lines; i++) '$i'].join('\n'), textAlign: TextAlign.right, style: style.copyWith(color: cs.outline)),
          ),
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: SelectableText(text, style: style),
            ),
          ),
        ],
      ),
    );
  }
}

class _StatusBar extends StatelessWidget {
  const _StatusBar({required this.manager});
  final GymsManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final env = manager.env == null ? null : manager.info(manager.env!);
    final parts = <String>[
      if (env == null) '${manager.environments.length} environments',
      if (env != null) manager.path.isEmpty ? env.name : '${env.name}/${manager.path}',
      if (env != null && manager.path.isEmpty) '${_size(env.bytes)} · ${env.files} files',
      if (manager.preview != null && !manager.isDir) _size(manager.preview!.size),
      if (env != null) env.usedBy.isEmpty ? 'no gyms run here' : '${env.usedBy.length} gym${env.usedBy.length == 1 ? '' : 's'}',
      'a run freezes its environment at start — edits reach the next run',
    ];
    return Container(
      height: 26,
      padding: const EdgeInsets.symmetric(horizontal: Sp.l),
      alignment: Alignment.centerLeft,
      color: cs.surfaceContainerLow,
      child: Text(parts.join('   ·   '), overflow: TextOverflow.ellipsis, style: AppTheme.mono.copyWith(fontSize: 11.5, color: cs.onSurfaceVariant)),
    );
  }
}

// ---- an environment's overview ----------------------------------------------------

class _Overview extends StatelessWidget {
  const _Overview({required this.env, required this.manager, required this.briefs, required this.nav});
  final EnvironmentInfo env;
  final GymsManager manager;
  final BriefManager briefs;
  final AppNav nav;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final body = theme.textTheme.bodyLarge!.copyWith(fontSize: 16, color: cs.onSurface, height: 1.5);
    final mono = AppTheme.mono.copyWith(fontSize: 12.5, color: cs.onSurface, height: 1.5);
    final key = opsKeyStyle(context);
    // What `mizpah.bases.apply` does to a gym's sandbox, in its order.
    final gets = <(String, String)>[
      ('bound read-only at', env.path),
      if (env.hasVenv) ('PATH first', '${env.path}/venv/bin'),
      if (env.hasBin) ('PATH', '${env.path}/bin'),
      if (env.hasVenv) ('VIRTUAL_ENV', '${env.path}/venv'),
      if (env.hasVenv) ('PYTHONPATH', 'its venv\'s site-packages (so probes import what the shell can)'),
      for (final e in env.env.entries) (e.key, e.value),
      ('MIZPAH_BASE', env.path),
    ];
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 28, 40, 48),
      child: Center(
        child: OpsSheet(
          maxWidth: 860,
          padding: const EdgeInsets.fromLTRB(56, 44, 56, 44),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Text('ENVIRONMENT', style: opsLabelStyle(context)),
                  const Spacer(),
                  if (env.isDefault)
                    Text('DEFAULT — A GYM THAT NAMES NONE RUNS HERE', style: key.copyWith(color: AppTheme.system(cs.brightness)))
                  else
                    TextButton(onPressed: () => manager.makeDefault(env.name), child: const Text('MAKE DEFAULT')),
                  const SizedBox(width: Sp.s),
                  _OpenInEditor(manager: manager, env: env.name),
                ],
              ),
              const SizedBox(height: Sp.s),
              Text(env.name, style: theme.textTheme.headlineMedium!.copyWith(fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              SelectableText('${env.path} · ${_size(env.bytes)} · ${env.files} files',
                  style: AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant)),
              const SizedBox(height: Sp.xl),
              const OpsLabel('What the worker is told'),
              const SizedBox(height: Sp.m),
              SelectableText(env.note.isEmpty ? 'No note — the worker is told nothing about this environment.' : env.note,
                  style: env.note.isEmpty ? body.copyWith(color: cs.onSurfaceVariant) : body),
              const SizedBox(height: Sp.xl),
              const OpsLabel('What the worker gets'),
              const SizedBox(height: Sp.m),
              for (final (k, v) in gets)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(width: 170, child: Text(k, style: key)),
                      Expanded(child: SelectableText(v, style: mono)),
                    ],
                  ),
                ),
              if (env.network.isNotEmpty) ...[
                const SizedBox(height: Sp.m),
                Text('HOSTS IT MAY REACH · ${env.network.length}', style: key),
                const SizedBox(height: 4),
                SelectableText(env.network.join('  ·  '), style: mono),
              ],
              if (env.packages.isNotEmpty) ...[
                const SizedBox(height: Sp.xl),
                OpsLabel('Python packages · ${env.packages.length}'),
                const SizedBox(height: Sp.m),
                Wrap(
                  spacing: Sp.s,
                  runSpacing: Sp.s,
                  children: [
                    for (final p in env.packages)
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: Sp.s, vertical: 3),
                        decoration: BoxDecoration(
                          border: Border.all(color: cs.outlineVariant),
                          borderRadius: BorderRadius.circular(AppTheme.radius),
                        ),
                        child: Text(p, style: mono.copyWith(fontSize: 12)),
                      ),
                  ],
                ),
              ],
              const SizedBox(height: Sp.xl),
              OpsLabel('Tasks that run here · ${env.usedBy.length}'),
              const SizedBox(height: Sp.m),
              if (env.usedBy.isEmpty)
                Text('None yet.', style: body.copyWith(color: cs.onSurfaceVariant))
              else
                _TaskFolders(ids: env.usedBy, briefs: briefs, nav: nav, gyms: manager),
            ],
          ),
        ),
      ),
    );
  }
}

// ---- the tasks that run in an environment -------------------------------------------

/// Each task that runs in this environment, as the folder it is: its title,
/// where it lives, its state; open the folder in the editor, go to the task,
/// or delete it. Live first, then stopped, completed, drafts, archived.
class _TaskFolders extends StatelessWidget {
  const _TaskFolders({required this.ids, required this.briefs, required this.nav, required this.gyms});
  final List<String> ids;
  final BriefManager briefs;
  final AppNav nav;
  final GymsManager gyms;

  static const _order = ['live', 'stopped', 'completed', 'idle', 'archived'];

  @override
  Widget build(BuildContext context) {
    final byId = {for (final b in briefs.briefs) b.id: b};
    final tasks = [for (final id in ids) byId[id] ?? BriefSummary(id: id, title: id, path: '')]
      ..sort((a, b) {
        final s = _order.indexOf(a.state).compareTo(_order.indexOf(b.state));
        return s != 0 ? s : a.title.toLowerCase().compareTo(b.title.toLowerCase());
      });
    final cs = Theme.of(context).colorScheme;
    return Container(
      decoration: BoxDecoration(border: Border(top: BorderSide(color: cs.outlineVariant))),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [for (final t in tasks) _TaskFolderRow(task: t, briefs: briefs, nav: nav, gyms: gyms)],
      ),
    );
  }
}

class _TaskFolderRow extends StatelessWidget {
  const _TaskFolderRow({required this.task, required this.briefs, required this.nav, required this.gyms});
  final BriefSummary task;
  final BriefManager briefs;
  final AppNav nav;
  final GymsManager gyms;

  static String _stateWord(String s) => switch (s) {
    'live' => 'LIVE',
    'stopped' => 'STOPPED',
    'completed' => 'COMPLETED',
    'idle' => 'DRAFT',
    'archived' => 'ARCHIVED',
    _ => s.toUpperCase(),
  };

  Future<void> _goTo(BuildContext context) async {
    if (task.id != briefs.selectedId) {
      if (!await confirmLeave(context, briefs)) return;
      await briefs.select(task.id);
    }
    nav.go(task.state == 'idle' ? AppNav.brief : AppNav.dailyWork);
  }

  Future<void> _delete(BuildContext context) async {
    final gym = task.id.startsWith('gyms/');
    final yes = await showOpsDialog<bool>(
      context,
      tag: 'DELETE TASK',
      title: task.title,
      body: task.state == 'idle'
          ? 'The draft and everything in its folder go for good.'
          : gym
          ? 'Every run, its journals and library records, and the gym folder itself go for good.'
          : 'Every run, its journals and library records, and the .mizpah tree go for good. Your repository and its files stay.',
      actions: (ctx) => Row(
        children: [
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('DELETE')),
          const SizedBox(width: Sp.m),
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
        ],
      ),
    );
    if (yes != true) return;
    try {
      await briefs.deleteProject(task.id);
    } catch (e) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
    await gyms.reload();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final live = task.state == 'live';
    final where = Desk.short(task.path);
    return InkWell(
      onTap: () => _goTo(context),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: Sp.s),
        decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outlineVariant))),
        child: Row(
          children: [
            Icon(Icons.folder_outlined, size: 18, color: cs.onSurfaceVariant),
            const SizedBox(width: Sp.m),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(task.title, maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodyMedium!.copyWith(color: AppTheme.ink(context), fontWeight: FontWeight.w500)),
                  if (where.isNotEmpty)
                    Text(where, maxLines: 1, overflow: TextOverflow.ellipsis,
                        style: AppTheme.mono.copyWith(fontSize: 11, color: cs.onSurfaceVariant)),
                ],
              ),
            ),
            const SizedBox(width: Sp.m),
            Text(_stateWord(task.state),
                style: opsKeyStyle(context).copyWith(color: live ? AppTheme.ink(context) : null, fontWeight: live ? FontWeight.w700 : null)),
            const SizedBox(width: Sp.s),
            if (!kIsWeb)
              IconButton(
                tooltip: 'Open the folder in the editor',
                iconSize: 16,
                icon: const Icon(Icons.open_in_new),
                onPressed: () => gyms.openTaskInEditor(task.id),
              ),
            IconButton(
              tooltip: 'Go to task',
              iconSize: 16,
              icon: const Icon(Icons.north_east),
              onPressed: () => _goTo(context),
            ),
            Tooltip(
              message: live ? 'Running — stop the run before deleting' : 'Delete the task',
              // Greyed while running: a loop's folder is not taken out from under it.
              child: Opacity(
                opacity: live ? 0.3 : 1,
                child: IgnorePointer(ignoring: live, child: OpsRemove(onPressed: () => _delete(context), tooltip: '')),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
