import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../../engine/desk.dart';
import '../../engine/engine.dart';
import '../../state/app_nav.dart';
import '../../state/brief_manager.dart';
import '../../widgets/folder_browser.dart';

/// Files: the open task's folder, read-only — what the agents are working
/// on, in the repository itself. The files changed in the last few minutes
/// read in ink; while the task is live and this tab is showing, the tree
/// reads itself again every few seconds. Editing is the desk's editor's.
class FilesScreen extends StatelessWidget {
  const FilesScreen({super.key, required this.engine, required this.briefs, required this.nav});
  final Engine engine;
  final BriefManager briefs;
  final AppNav nav;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([briefs, nav]),
      builder: (context, _) {
        final id = briefs.selectedId;
        final task = briefs.briefs.where((b) => b.id == id).firstOrNull;
        if (id == null || task == null) return const SizedBox.shrink();
        final watching = nav.tab == AppNav.files && task.state == 'live';
        return FolderBrowser(
          // A new task is a new tree.
          key: ValueKey(id),
          rootLabel: task.title,
          rootPath: Desk.short(task.path),
          list: (p) => engine.listTaskFiles(id, p),
          read: (p) => engine.readTaskFile(id, p),
          readBytes: (p) => engine.readTaskFileBytes(id, p),
          // The player reads from disk, so it plays at the desk only.
          mediaUri: kIsWeb ? null : (p) => Uri.file('${task.path}/$p').toString(),
          openInEditor: kIsWeb ? null : (p) => engine.openTaskInEditor(id, p),
          refreshEvery: watching ? const Duration(seconds: 4) : null,
          emptyHint: 'Pick a file to read it. What changed in the last five minutes is in ink.',
        );
      },
    );
  }
}
