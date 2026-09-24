import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import 'package:flutter/foundation.dart' show kIsWeb;

import '../engine/desk.dart';
import '../engine/engine.dart';
import '../state/app_nav.dart';
import '../models/provider.dart';
import '../state/brief_manager.dart';
import '../state/provider_manager.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import 'crew.dart';
import 'facet_filter.dart';
import 'ops.dart';
import 'unsaved_dialog.dart';

/// The project switcher. Search on top, then projects grouped by their
/// loop's state — live, stopped, completed — each group foldable.
/// Collapses to a thin rail so the desk gets the width; the rail still
/// shows which project is open and which ones want attention.
class ProjectSidebar extends StatefulWidget {
  const ProjectSidebar({
    super.key,
    required this.manager,
    required this.nav,
    required this.providers,
    this.startCollapsed = false,
    this.narrow = false,
  });
  final BriefManager manager;
  final AppNav nav;

  /// For the new-task sheet's second step: which models take the seats.
  final ProviderManager providers;

  /// The user's preference for how the list opens at launch.
  final bool startCollapsed;

  /// A reduced screen (an iPad, a small window): the task list and a
  /// page's own document list are not both open. Walking into a task
  /// folds this one to its rail; it can still be opened by hand, and then
  /// the page's list folds instead.
  final bool narrow;

  static const width = 240.0;
  static const railWidth = 44.0;

  @override
  State<ProjectSidebar> createState() => _ProjectSidebarState();
}

class _ProjectSidebarState extends State<ProjectSidebar> {
  late bool collapsed = widget.startCollapsed || widget.narrow;

  @override
  void didUpdateWidget(covariant ProjectSidebar old) {
    super.didUpdateWidget(old);
    if (widget.narrow && !old.narrow) collapsed = true;
  }

  /// Which groups are folded. Live and Stopped start open; the rest fold. A group
  /// with attention shows its red total on the fold so nothing hides.
  final folded = <String>{'completed', 'idle', 'archived'};
  final _search = TextEditingController();

  /// Column filters (a funnel beside +): the folder above the task — the
  /// project level, for free — and whether it waits on you. Empty = all.
  Map<String, Set<String>> picked = {'folder': {}, 'attention': {}};
  bool get filtering => _search.text.trim().isNotEmpty || picked.values.any((s) => s.isNotEmpty);

  /// The folder a task lives in: the full path of the directory above its
  /// project root. Every one is listed, singletons included — a task in
  /// /tmp/embed2 is filterable by /tmp.
  static String folderOf(BriefSummary b) {
    final p = b.path.replaceFirst(RegExp(r'/+$'), '');
    final i = p.lastIndexOf('/');
    return i <= 0 ? '/' : p.substring(0, i);
  }

  static String attentionOf(BriefSummary b) => b.attention > 0 ? 'needs me' : 'nothing owed';

  List<Facet> _facets(List<BriefSummary> all) {
    Map<String, int> count(String Function(BriefSummary) f) {
      final m = <String, int>{};
      for (final b in all) {
        m[f(b)] = (m[f(b)] ?? 0) + 1;
      }
      return m;
    }
    return [
      Facet(key: 'folder', label: 'Folder', values: count(folderOf), paths: true),
      Facet(key: 'attention', label: 'Attention', values: count(attentionOf)),
    ];
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  /// A new task starts as a brief. One sheet: what it is called, what it is
  /// for, and where it lives — in a repository of yours (Mizpah edits the
  /// real tree; git is its safety net) or as a gym with no repository.
  Future<void> _newTask(BuildContext context) async {
    final m = widget.manager;
    if (!await confirmLeave(context, m)) return;
    if (!context.mounted) return;
    final created = await showDialog<bool>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.6),
      builder: (_) => _NewTaskSheet(
        providers: widget.providers,
        // Step one's NEXT: make the task; the sheet then turns into step two,
        // which needs the task's path to write its own model choices.
        onCreate: (title, mission, repo) async {
          await m.create(title, mission: mission, repo: repo);
          return m.selectedPath;
        },
        inspect: m.inspectPath,
      ),
    );
    if (created == true) widget.nav.go(AppNav.brief);
  }

  /// Archive, unarchive or delete a project from its row. Delete confirms,
  /// and says what goes: a gym whole, a repository only its Mizpah tree.
  Future<void> _projectAction(BriefSummary b, String action) async {
    final m = widget.manager;
    try {
      switch (action) {
        case 'archive':
          await m.archiveProject(b.id);
        case 'unarchive':
          await m.unarchiveProject(b.id);
        case 'delete':
          final gym = b.id.startsWith('gyms/');
          final yes = await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
              title: Text('DELETE ${b.title.toUpperCase()}', style: opsHeadingStyle(ctx)),
              content: Text(
                b.state == 'idle'
                    ? 'The draft and everything in its folder go for good.'
                    : gym
                        ? 'Every run, its journals and library records, and the gym folder itself go for good.'
                        : 'Every run, its journals and library records, and the .mizpah tree go for good. Your repository and its files stay.',
              ),
              actions: [
                TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
                TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('DELETE')),
              ],
            ),
          );
          if (yes == true) await m.deleteProject(b.id);
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  /// Walk into an office: select it and land on its desk.
  Future<void> _pick(BriefSummary b) async {
    final m = widget.manager;
    if (b.id != m.selectedId) {
      if (!await confirmLeave(context, m)) return;
      await m.select(b.id);
    }
    // A draft has no paper but its brief: land there. Otherwise land on
    // the task's desk unless already on one of its tabs.
    final t = widget.nav.tab;
    if (b.state == 'idle') {
      widget.nav.go(AppNav.brief);
    } else if (t < AppNav.dailyWork || t > AppNav.agents) {
      widget.nav.go(AppNav.dailyWork);
    }
    // On a reduced screen the desk gets the room once you are in the office.
    if (widget.narrow && mounted) setState(() => collapsed = true);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final m = widget.manager;
    return ListenableBuilder(
      listenable: m,
      builder: (context, _) {
        final q = _search.text.trim().toLowerCase();
        // Filter by title, id or folder: "brand" shows every task under
        // ~/mizpah-runs/brand, "/tmp/embed2" the tasks of that repository.
        bool hit(BriefSummary b) =>
            (q.isEmpty ||
                b.title.toLowerCase().contains(q) ||
                b.id.contains(q) ||
                b.path.toLowerCase().contains(q) ||
                _short(b.path).toLowerCase().contains(q)) &&
            (picked['folder']!.isEmpty || picked['folder']!.contains(folderOf(b))) &&
            (picked['attention']!.isEmpty || picked['attention']!.contains(attentionOf(b)));
        final groups = <(String, String, List<BriefSummary>)>[
          for (final (key, label) in const [
            ('live', 'LIVE'),
            ('stopped', 'STOPPED'),
            ('completed', 'COMPLETED'),
            ('idle', 'DRAFT'),
            ('archived', 'ARCHIVED'),
          ])
            (key, label, m.briefs.where((b) => b.state == key).toList()),
        ];
        // Completed reads by when each finished, the latest on top — not in
        // the order they were started.
        final done = groups.firstWhere((g) => g.$1 == 'completed').$3;
        done.sort((a, b) => (b.endedAt ?? DateTime(0)).compareTo(a.endedAt ?? DateTime(0)));
        final anyHit = m.briefs.any(hit);

        return AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          width: collapsed ? ProjectSidebar.railWidth : ProjectSidebar.width,
          child: Material(
            color: cs.surfaceContainerLow,
            child: collapsed
                ? _Rail(
                    briefs: m.briefs,
                    selectedId: m.selectedId,
                    onExpand: () => setState(() => collapsed = false),
                    onPick: _pick,
                    onNew: () => _newTask(context),
                  )
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // Where you are, always: the open project's name under the
                      // title, or HOME when no project is open.
                      Padding(
                        padding: const EdgeInsets.fromLTRB(Sp.l, 14, Sp.l, 0),
                        child: Builder(builder: (context) {
                          final open = m.briefs.where((b) => b.id == m.selectedId).firstOrNull;
                          // One style and one height whatever it says, so the
                          // list below never jumps when you change rooms.
                          return SizedBox(
                            height: 24,
                            child: Align(
                              alignment: Alignment.centerLeft,
                              child: Text(
                                open?.title ?? 'Home',
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: Theme.of(context).textTheme.titleMedium!.copyWith(
                                  color: open == null ? cs.onSurfaceVariant : AppTheme.ink(context),
                                  fontWeight: FontWeight.w700,
                                  height: 1.0,
                                ),
                              ),
                            ),
                          );
                        }),
                      ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(Sp.l, 2, Sp.xs, 0),
                        child: Row(
                          children: [
                            Text('TASKS', style: opsLabelStyle(context)),
                            const Spacer(),
                            FacetFilterButton(
                              facets: _facets(m.briefs),
                              selected: picked,
                              onChanged: (v) => setState(() => picked = v),
                            ),
                            IconButton(
                              tooltip: 'New task',
                              iconSize: 18,
                              icon: const Icon(Icons.add),
                              onPressed: () => _newTask(context),
                            ),
                            IconButton(
                              tooltip: 'Collapse',
                              iconSize: 18,
                              icon: const Icon(Icons.keyboard_double_arrow_left),
                              onPressed: () => setState(() => collapsed = true),
                            ),
                          ],
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(Sp.m, 2, Sp.m, Sp.s),
                        child: _SearchField(
                          controller: _search,
                          onChanged: (_) => setState(() {}),
                        ),
                      ),
                      Expanded(
                        // Rows are described up front but only the visible
                        // ones are built: the board may list hundreds of tasks.
                        child: Builder(builder: (context) {
                          final rows = <Widget>[
                            // Every group is always listed, empty ones as
                            // `· 0`, so the board reads the same each day.
                            for (final (key, label, all) in groups) ...[
                                _GroupHead(
                                  label: label,
                                  count: all.length,
                                  // Archived is put away: no open-issue total,
                                  // just how many are in the drawer.
                                  attention: key == 'archived'
                                      ? 0
                                      : all.fold(0, (a, b) => a + b.attention),
                                  // A search reaches into a folded group —
                                  // except the archive, which only opens by hand.
                                  open: (filtering && key != 'archived') || !folded.contains(key),
                                  onTap: () => setState(() {
                                    if (!folded.remove(key)) folded.add(key);
                                  }),
                                ),
                                // A folded group still shows the open project beneath its
                                // header, alone: the sidebar says where you are, and the
                                // group folds like any other.
                                for (final b in all.where((b) =>
                                    ((filtering && key != 'archived') || !folded.contains(key))
                                        ? hit(b) || b.id == m.selectedId
                                        : b.id == m.selectedId))
                                    _ProjectRow(
                                      brief: b,
                                      selected: b.id == m.selectedId,
                                      onTap: () => _pick(b),
                                      quiet: key == 'completed' || key == 'archived',
                                      onAction: (a) => _projectAction(b, a),
                                    ),
                              ],
                            if (filtering && !anyHit)
                              Padding(
                                padding: const EdgeInsets.all(Sp.l),
                                child: Text(
                                  'No task matches.',
                                  style: opsLabelStyle(context),
                                ),
                              ),
                          ];
                          return ListView.builder(itemCount: rows.length, itemBuilder: (_, i) => rows[i]);
                        }),
                      ),
                    ],
                  ),
          ),
        );
      },
    );
  }
}

/// A group line: label · count, a red attention total when any project in
/// it wants you, a chevron for the fold.
class _GroupHead extends StatelessWidget {
  const _GroupHead({
    required this.label,
    required this.count,
    required this.attention,
    required this.open,
    required this.onTap,
  });
  final String label;
  final int count;
  final int attention;
  final bool open;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.fromLTRB(Sp.l, Sp.m, Sp.m, Sp.s),
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(
          children: [
            Text('$label · $count', style: opsLabelStyle(context)),
            if (attention > 0) ...[
              const SizedBox(width: Sp.s),
              Text(
                '$attention',
                style: opsLabelStyle(context).copyWith(
                  color: cs.primary,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
            const Spacer(),
            Icon(
              open ? Icons.expand_more : Icons.chevron_right,
              size: 18,
              color: cs.onSurfaceVariant,
            ),
          ],
        ),
      ),
    );
  }
}

class _SearchField extends StatelessWidget {
  const _SearchField({required this.controller, required this.onChanged});
  final TextEditingController controller;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SizedBox(
      height: 32,
      child: TextField(
        controller: controller,
        onChanged: onChanged,
        style: AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurface),
        decoration: InputDecoration(
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          prefixIcon: Icon(Icons.search, size: 16, color: cs.onSurfaceVariant),
          prefixIconConstraints: const BoxConstraints(minWidth: 30),
          hintText: 'Search',
          hintStyle: AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurfaceVariant),
          suffixIcon: controller.text.isEmpty
              ? null
              : IconButton(
                  iconSize: 14,
                  icon: const Icon(Icons.close),
                  tooltip: 'Clear search',
                  onPressed: () {
                    controller.clear();
                    onChanged('');
                  },
                ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(AppTheme.radius),
            borderSide: BorderSide(color: cs.outlineVariant),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(AppTheme.radius),
            borderSide: BorderSide(color: cs.outline),
          ),
        ),
      ),
    );
  }
}

/// Collapsed: no list at all. The expander, a new-task `+`, the open
/// task's initial so you know where you are, and two counts — live tasks
/// in ink, signatures owed in red. Everything else waits behind `»`.
class _Rail extends StatelessWidget {
  const _Rail({
    required this.briefs,
    required this.selectedId,
    required this.onExpand,
    required this.onPick,
    required this.onNew,
  });
  final List<BriefSummary> briefs;
  final String? selectedId;
  final VoidCallback onExpand;
  final ValueChanged<BriefSummary> onPick;
  final VoidCallback onNew;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    final selected = briefs.where((b) => b.id == selectedId).firstOrNull;
    final live = briefs.where((b) => b.running).length;
    final owed = briefs.where((b) => b.state != 'archived').fold(0, (a, b) => a + b.attention);
    Widget count(int n, Color color, String tip) => Tooltip(
      message: tip,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Text(
          '$n',
          style: AppTheme.mono.copyWith(fontSize: 13, fontWeight: FontWeight.w700, color: color),
        ),
      ),
    );
    return Column(
      children: [
        const SizedBox(height: 8),
        IconButton(
          tooltip: 'Tasks',
          iconSize: 18,
          icon: const Icon(Icons.keyboard_double_arrow_right),
          onPressed: onExpand,
        ),
        IconButton(
          tooltip: 'New task',
          iconSize: 18,
          icon: const Icon(Icons.add),
          onPressed: onNew,
        ),
        if (selected != null) ...[
          const SizedBox(height: Sp.m),
          Tooltip(
            message: selected.title,
            child: Container(
              width: 28,
              height: 28,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: cs.surfaceContainer,
                border: Border.all(color: ink, width: 1.5),
                borderRadius: BorderRadius.circular(3),
              ),
              child: Text(
                selected.title.isEmpty ? '?' : selected.title[0].toUpperCase(),
                style: AppTheme.mono.copyWith(fontSize: 14, fontWeight: FontWeight.w700, color: ink),
              ),
            ),
          ),
        ],
        const Spacer(),
        if (live > 0) count(live, ink, '$live live'),
        if (owed > 0) count(owed, cs.primary, '$owed awaiting signature'),
        const SizedBox(height: Sp.m),
      ],
    );
  }
}

/// A project in the list: accent bar when selected, its folder beneath the
/// title, a red count when documents wait on a person.
String _short(String path) => Desk.short(path);

/// The folder line of a row: a gym is named, not located — its path is
/// the app's own data folder and says nothing. The run's stamp
/// (`-20260922T173411Z`) is dropped; the full path stays in the tooltip.
String _place(BriefSummary b) => b.id.startsWith('gyms/')
    ? 'gym ${b.id.substring('gyms/'.length).replaceFirst(RegExp(r'-\d{8}T\d{6}Z$'), '')}'
    : _short(b.path);

class _ProjectRow extends StatelessWidget {
  const _ProjectRow({
    required this.brief,
    required this.selected,
    required this.onTap,
    this.quiet = false,
    this.onAction,
  });
  final BriefSummary brief;
  final bool selected;
  final VoidCallback onTap;

  /// archive · unarchive · delete, from the row's ··· menu.
  final ValueChanged<String>? onAction;

  /// Archived rows: smaller, greyer.
  final bool quiet;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return Tooltip(
      message: brief.path,
      waitDuration: const Duration(milliseconds: 600),
      child: InkWell(
        onTap: onTap,
        child: Container(
          padding: EdgeInsets.symmetric(
            horizontal: Sp.l,
            vertical: quiet ? Sp.s : Sp.m,
          ),
          decoration: BoxDecoration(
            color: selected ? cs.surfaceContainerHighest : null,
            border: Border(
              left: BorderSide(
                color: selected ? AppTheme.ink(context) : Colors.transparent,
                width: 3,
              ),
            ),
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      brief.title,
                      overflow: TextOverflow.ellipsis,
                      style: (quiet ? theme.textTheme.bodyMedium : theme.textTheme.titleMedium)!
                          .copyWith(
                            color: selected
                                ? AppTheme.ink(context)
                                : quiet
                                ? cs.onSurfaceVariant
                                : cs.onSurface,
                            fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                          ),
                    ),
                    // The folder, always: a project is a place on disk and the
                    // list says which.
                    if (brief.path.isNotEmpty)
                      Text(
                        _place(brief),
                        overflow: TextOverflow.ellipsis,
                        style: AppTheme.mono.copyWith(fontSize: quiet ? 10 : 11, color: cs.onSurfaceVariant),
                      ),
                  ],
                ),
              ),
              if (brief.running)
                Padding(
                  padding: const EdgeInsets.only(right: Sp.s),
                  child: Text(
                    'LIVE',
                    style: AppTheme.mono.copyWith(
                      fontSize: 10,
                      letterSpacing: 1.4,
                      fontWeight: FontWeight.w700,
                      color: AppTheme.ink(context),
                    ),
                  ),
                ),
              if (brief.attention > 0 && brief.state != 'archived')
                Text(
                  '${brief.attention}',
                  style: AppTheme.mono.copyWith(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: cs.primary,
                  ),
                ),
              if (onAction != null)
                SizedBox(
                  width: 24,
                  height: 24,
                  child: OverflowBox(
                    maxWidth: 36,
                    maxHeight: 36,
                    child: PopupMenuButton<String>(
                      tooltip: 'More',
                      padding: EdgeInsets.zero,
                      onSelected: onAction,
                      icon: Icon(Icons.more_horiz, size: 16, color: selected ? AppTheme.ink(context) : cs.outline),
                      itemBuilder: (_) => [
                        if (brief.state == 'archived')
                          PopupMenuItem(value: 'unarchive', child: Text('UNARCHIVE', style: opsKeyStyle(context)))
                        else if (brief.state != 'idle' && !brief.running)
                          PopupMenuItem(value: 'archive', child: Text('ARCHIVE', style: opsKeyStyle(context))),
                        if (!brief.running)
                          PopupMenuItem(
                            value: 'delete',
                            child: Text(brief.state == 'idle' ? 'DISCARD DRAFT…' : 'DELETE…', style: opsKeyStyle(context).copyWith(color: AppTheme.removed(context))),
                          ),
                        if (brief.running)
                          PopupMenuItem(enabled: false, value: '', child: Text('LIVE — HOLD IT FIRST', style: opsKeyStyle(context))),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The new-task sheet. Title and mission are the brief's first two lines;
/// the location decides whether this is work on a repository or a gym.
class _NewTaskSheet extends StatefulWidget {
  const _NewTaskSheet({required this.providers, required this.onCreate, required this.inspect});
  final ProviderManager providers;
  final Future<String> Function(String title, String mission, String? repo) onCreate;

  /// Whether a path is a folder, and a git repository, on the desk.
  final Future<Map<String, bool>> Function(String path) inspect;

  @override
  State<_NewTaskSheet> createState() => _NewTaskSheetState();
}

class _NewTaskSheetState extends State<_NewTaskSheet> {
  final title = TextEditingController();
  final mission = TextEditingController();
  final repo = TextEditingController();
  bool inRepo = false;
  String? problem;
  bool creating = false;
  bool created = false;
  String projectPath = '';

  /// 1: the brief · 2: the models. Same sheet, content swapped in place.
  int step = 1;

  @override
  void dispose() {
    title.dispose();
    mission.dispose();
    repo.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (title.text.trim().isEmpty) return setState(() => problem = 'A title is needed.');
    if (mission.text.trim().isEmpty) return setState(() => problem = 'Say what the task is for — one or two sentences.');
    if (inRepo) {
      final path = repo.text.trim();
      final at = path.isEmpty ? const {'exists': false} : await widget.inspect(path);
      if (!mounted) return;
      if (at['exists'] != true) return setState(() => problem = 'That folder does not exist.');
      if (at['git'] != true) {
        return setState(() => problem = 'That folder is not a git repository. Run git init there first.');
      }
    }
    await _create();
  }

  Future<void> _create() async {
    if (created) return setState(() => step = 2); // back, then next again
    setState(() {
      creating = true;
      problem = null;
    });
    try {
      projectPath = await widget.onCreate(title.text.trim(), mission.text.trim(), inRepo ? repo.text.trim() : null);
      created = true;
      if (mounted) setState(() => step = 2);
    } catch (e) {
      if (mounted) setState(() => problem = '$e');
    } finally {
      if (mounted) setState(() => creating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final field = theme.textTheme.bodyLarge!.copyWith(color: ink);
    if (step == 2) {
      return Dialog(
        backgroundColor: Colors.transparent,
        child: OpsSheet(
          maxWidth: 620,
          padding: const EdgeInsets.fromLTRB(40, 34, 40, 28),
          child: _ModelsStep(
            providers: widget.providers,
            taskTitle: title.text.trim(),
            projectPath: projectPath,
            onBack: () => setState(() => step = 1),
            onDone: () => Navigator.pop(context, true),
          ),
        ),
      );
    }
    return Dialog(
      backgroundColor: Colors.transparent,
      child: OpsSheet(
        maxWidth: 620,
        padding: const EdgeInsets.fromLTRB(40, 34, 40, 28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('NEW TASK', style: opsLabelStyle(context).copyWith(color: cs.primary)),
            const SizedBox(height: 12),
            Text('Open a Brief', style: theme.textTheme.headlineSmall!.copyWith(fontWeight: FontWeight.w700, color: ink, height: 1.25)),
            const SizedBox(height: Sp.xl),
            Text('TITLE', style: opsKeyStyle(context)),
            const SizedBox(height: 4),
            TextField(controller: title, autofocus: true, style: field, enabled: !created,
                decoration: const InputDecoration(isDense: true, hintText: 'Sales Ledger Toolkit')),
            const SizedBox(height: Sp.l),
            Text('MISSION', style: opsKeyStyle(context)),
            const SizedBox(height: 4),
            // Fixed at three lines; longer text scrolls within, the sheet holds still.
            TextField(controller: mission, minLines: 3, maxLines: 3, style: field,
                decoration: const InputDecoration(isDense: true, hintText: 'What this task is for, in a sentence or two.')),
            const SizedBox(height: Sp.xl),
            Text('WHERE', style: opsKeyStyle(context)),
            const SizedBox(height: 6),
            _Where(
              selected: !inRepo,
              title: 'Gym',
              body: 'Use for training. Best when you don\'t want to pollute a project but want the harness to gain skills.',
              onTap: () => setState(() => inRepo = false),
            ),
            const SizedBox(height: Sp.s),
            _Where(
              selected: inRepo,
              title: 'Repository',
              body: 'A folder on your PC. Mizpah works in it directly and keeps its record in .mizpah/ alongside. It must be a git repository.',
              onTap: () => setState(() => inRepo = true),
              // Always present so the sheet never changes size; live only
              // when Repository is the choice.
              child: Padding(
                padding: const EdgeInsets.only(top: Sp.s),
                child: Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: repo,
                        enabled: inRepo,
                        style: AppTheme.mono.copyWith(fontSize: 13, color: inRepo ? ink : cs.outline),
                        onSubmitted: (_) => _submit(),
                        onTap: () => setState(() => inRepo = true),
                        decoration: InputDecoration(
                          isDense: true,
                          hintText: '/home/you/projects/my-repo',
                          hintStyle: AppTheme.mono.copyWith(fontSize: 13, color: cs.outline),
                        ),
                      ),
                    ),
                    // A browser cannot open the desk's folders: type the path.
                    if (!kIsWeb) ...[
                      const SizedBox(width: Sp.s),
                      OutlinedButton(
                        onPressed: () async {
                          setState(() => inRepo = true);
                          // The platform's own folder dialog.
                          final picked = await getDirectoryPath(
                            initialDirectory: repo.text.trim().isEmpty ? null : repo.text.trim(),
                            confirmButtonText: 'Choose',
                          );
                          if (picked != null) setState(() => repo.text = picked);
                        },
                        child: const Text('BROWSE'),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            if (problem != null) ...[
              const SizedBox(height: Sp.l),
              Text(problem!, style: theme.textTheme.bodyMedium!.copyWith(color: cs.error)),
            ],
            const SizedBox(height: Sp.xl),
            Row(
              children: [
                FilledButton(onPressed: creating ? null : _submit, child: Text(creating ? 'OPENING…' : 'NEXT')),
                const SizedBox(width: Sp.m),
                TextButton(onPressed: () => Navigator.pop(context), child: const Text('CANCEL')),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Where extends StatelessWidget {
  const _Where({required this.selected, required this.title, required this.body, required this.onTap, this.child});
  final bool selected;
  final String title;
  final String body;
  final VoidCallback onTap;
  final Widget? child;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    return InkWell(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.fromLTRB(Sp.m, Sp.m, Sp.m, Sp.m),
        decoration: BoxDecoration(
          border: Border.all(color: selected ? ink : cs.outlineVariant, width: selected ? 1.5 : 1),
          borderRadius: BorderRadius.circular(AppTheme.radius),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Icon(selected ? Icons.radio_button_checked : Icons.radio_button_off, size: 16, color: selected ? ink : cs.outline),
            ),
            const SizedBox(width: Sp.m),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: theme.textTheme.titleMedium!.copyWith(color: ink, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 2),
                  Text(body, style: theme.textTheme.bodySmall!.copyWith(color: cs.onSurfaceVariant, height: 1.4)),
                  ?child,
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Step two of a new task: the seats. Each role shows the model this task
/// will use — its own choice when it has made one, else your default —
/// and CHANGE opens the picker, which writes to this task only.
class _ModelsStep extends StatefulWidget {
  const _ModelsStep({
    required this.providers,
    required this.taskTitle,
    required this.projectPath,
    required this.onBack,
    required this.onDone,
  });
  final ProviderManager providers;
  final String taskTitle;
  final String projectPath;
  final VoidCallback onBack;
  final VoidCallback onDone;

  @override
  State<_ModelsStep> createState() => _ModelsStepState();
}

class _ModelsStepState extends State<_ModelsStep> {
  /// The task's own seats, read once and after every change.
  Map<ModelRole, ModelChoice> task = const {};

  @override
  void initState() {
    super.initState();
    if (widget.providers.providers.isEmpty) widget.providers.load();
    _readTask();
  }

  Future<void> _readTask() async {
    final t = await widget.providers.taskChoices(widget.projectPath);
    if (mounted) setState(() => task = t);
  }

  Future<void> _change(ModelRole role) async {
    await showDialog<void>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.7),
      builder: (_) => ModelPicker(providers: widget.providers, role: role, projectPath: widget.projectPath, current: task[role]),
    );
    await _readTask(); // re-read the task's config
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final pm = widget.providers;
    return ListenableBuilder(
      listenable: pm,
      builder: (context, _) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('NEW TASK · ${widget.taskTitle.toUpperCase()}', style: opsLabelStyle(context).copyWith(color: cs.primary)),
          const SizedBox(height: 12),
          Text('Models', style: theme.textTheme.headlineSmall!.copyWith(fontWeight: FontWeight.w700, color: ink, height: 1.25)),
          const SizedBox(height: Sp.m),
          for (final role in ModelRole.values)
            CrewRoleRow(
              role: role,
              choice: task[role] ?? pm.current[role] ?? ModelChoice.none,
              isDefault: task[role] == null,
              changing: false,
              onChange: () => _change(role),
              effort: EffortChip(
                providers: pm,
                role: role,
                choice: task[role] ?? pm.current[role] ?? ModelChoice.none,
                projectPath: widget.projectPath,
                onSet: _readTask,
              ),
            ),
          if (pm.error != null) ...[
            const SizedBox(height: Sp.m),
            OpsError(pm.error!),
          ],
          const SizedBox(height: Sp.xxl),
          Row(
            children: [
              TextButton(onPressed: widget.onBack, child: const Text('BACK')),
              const SizedBox(width: Sp.m),
              FilledButton(onPressed: widget.onDone, child: const Text('CONTINUE TO THE BRIEF')),
            ],
          ),
        ],
      ),
    );
  }
}
