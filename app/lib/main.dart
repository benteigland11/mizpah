import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:media_kit/media_kit.dart';

import 'boot/boot.dart';
import 'engine/desk_share.dart';
import 'engine/engine.dart';
import 'engine/host_watch.dart';
import 'screens/agents/agents_screen.dart';
import 'screens/brief/brief_screen.dart';
import 'screens/budget/budget_screen.dart';
import 'screens/databook/databook_screen.dart';
import 'screens/inbox/inbox_screen.dart';
import 'screens/deputy/deputy_screen.dart';
import 'screens/files/files_screen.dart';
import 'screens/gyms/gyms_screen.dart';
import 'screens/procedures/procedures_screen.dart';
import 'screens/daily_work/daily_work_screen.dart';
import 'screens/providers/providers_screen.dart';
import 'screens/settings/settings_screen.dart';
import 'screens/workorders/work_orders_screen.dart';
import 'state/agents_manager.dart';
import 'state/app_nav.dart';
import 'state/brief_manager.dart';
import 'state/budget_manager.dart';
import 'state/databook_manager.dart';
import 'state/deputy_manager.dart';
import 'state/inbox_manager.dart';
import 'state/daily_work_manager.dart';
import 'state/gyms_manager.dart';
import 'state/procedures_manager.dart';
import 'state/loop_manager.dart';
import 'state/provider_manager.dart';
import 'state/route_manager.dart';
import 'state/storage_manager.dart';
import 'state/app_settings.dart';
import 'state/work_orders_manager.dart';
import 'theme/app_theme.dart';
import 'theme/kit_styles.dart';
import 'widgets/document_sheet.dart';
import 'widgets/project_bar.dart';
import 'widgets/project_sidebar.dart';
import 'widgets/unsaved_dialog.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // The native player (libmpv) behind audio and video in the file browser.
  MediaKit.ensureInitialized();
  // On the desk the engine is in this process; in a browser it is the desk
  // that served the page. Either way the shell sees one Engine.
  final b = await boot();
  final settings = b.settings;
  final engine = b.engine;
  final briefs = BriefManager(engine);
  // The Deputy's desk is a brief manager of its own: a draft read there
  // never opens a project the way the sidebar does.
  final deputy = DeputyManager(engine, BriefManager(engine));
  runApp(
    MizpahApp(
      manager: briefs,
      inbox: InboxManager(engine),
      deputy: deputy,
      agents: AgentsManager(engine, briefs),
      procedures: ProceduresManager(engine),
      gyms: GymsManager(engine),
      engine: engine,
      host: b.host,
      dailyWork: DailyWorkManager(engine, briefs),
      databook: DataBookManager(engine, briefs),
      budget: BudgetManager(engine, briefs),
      workOrders: WorkOrdersManager(engine, briefs),
      loop: LoopManager(engine, briefs),
      route: RouteManager(engine, briefs),
      providers: ProviderManager(b.providers)..onUsed = settings.noteModelUsed,
      nav: AppNav(),
      style: settings,
      storage: StorageManager(engine),
      remote: b.remote,
      share: b.share,
    ),
  );
}

class MizpahApp extends StatelessWidget {
  const MizpahApp({
    super.key,
    required this.manager,
    required this.inbox,
    required this.deputy,
    required this.agents,
    required this.procedures,
    required this.gyms,
    required this.engine,
    required this.host,
    required this.dailyWork,
    required this.databook,
    required this.budget,
    required this.workOrders,
    required this.loop,
    required this.route,
    required this.providers,
    required this.nav,
    required this.style,
    required this.storage,
    this.remote = false,
    this.share,
  });
  final BriefManager manager;
  final InboxManager inbox;
  final DeputyManager deputy;
  final AgentsManager agents;
  final ProceduresManager procedures;
  final GymsManager gyms;

  /// For the pages that read the engine directly (a task's Files).
  final Engine engine;
  final HostWatch? host;
  final DailyWorkManager dailyWork;
  final DataBookManager databook;
  final BudgetManager budget;
  final WorkOrdersManager workOrders;
  final LoopManager loop;
  final RouteManager route;
  final ProviderManager providers;
  final AppNav nav;
  final AppSettings style;

  /// In a browser: no app close, no folder picker.
  final bool remote;
  final StorageManager storage;

  /// Sharing this desk on the network; null in a browser.
  final DeskShare? share;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: style,
      builder: (context, _) => MaterialApp(
        title: 'Mizpah',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light(),
        darkTheme: AppTheme.dark(),
        themeMode: style.mode,
        home: Shell(
          manager: manager,
          inbox: inbox,
          deputy: deputy,
          agents: agents,
          procedures: procedures,
          gyms: gyms,
          engine: engine,
          host: host,
          dailyWork: dailyWork,
          databook: databook,
          budget: budget,
          workOrders: workOrders,
          loop: loop,
          route: route,
          providers: providers,
          nav: nav,
          style: style,
          storage: storage,
          remote: remote,
          share: share,
        ),
      ),
    );
  }
}

/// Nav rail on the far left; one surface at a time on the right.
class Shell extends StatefulWidget {
  const Shell({
    super.key,
    required this.manager,
    required this.inbox,
    required this.deputy,
    required this.agents,
    required this.procedures,
    required this.gyms,
    required this.engine,
    required this.host,
    required this.dailyWork,
    required this.databook,
    required this.budget,
    required this.workOrders,
    required this.loop,
    required this.route,
    required this.providers,
    required this.nav,
    required this.style,
    required this.storage,
    this.remote = false,
    this.share,
  });
  final BriefManager manager;
  final InboxManager inbox;
  final DeputyManager deputy;
  final AgentsManager agents;
  final ProceduresManager procedures;
  final GymsManager gyms;

  /// For the pages that read the engine directly (a task's Files).
  final Engine engine;
  final HostWatch? host;
  final DailyWorkManager dailyWork;
  final DataBookManager databook;
  final BudgetManager budget;
  final WorkOrdersManager workOrders;
  final LoopManager loop;
  final RouteManager route;
  final ProviderManager providers;
  final AppNav nav;
  final AppSettings style;
  final StorageManager storage;
  final bool remote;
  final DeskShare? share;

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int get _index => widget.nav.tab;

  @override
  void initState() {
    super.initState();
    widget.manager.load();
    widget.manager.addListener(_onProject);
    widget.nav.addListener(_onNav);
    widget.inbox.visible = widget.nav.tab == AppNav.inbox;
    widget.agents.setWatching(widget.nav.tab == AppNav.agents);
    HardwareKeyboard.instance.addHandler(_onKey);
  }

  @override
  void dispose() {
    widget.nav.removeListener(_onNav);
    widget.manager.removeListener(_onProject);
    HardwareKeyboard.instance.removeHandler(_onKey);
    super.dispose();
  }

  void _onNav() {
    // The agents tail only polls while the Agents tab is the one showing.
    widget.agents.setWatching(widget.nav.tab == AppNav.agents);
    // The in-tray opens its first sheet only while it is the page showing.
    widget.inbox.visible = widget.nav.tab == AppNav.inbox;
    // Environments change outside the app (the Deputy builds them): read
    // them again on walking in; it is a tenth of a second.
    if (widget.nav.tab == AppNav.gyms && _lastTab != AppNav.gyms) widget.gyms.reload();
    _lastTab = widget.nav.tab;
    setState(() {});
  }

  int _lastTab = -1;

  /// The strip depends on whether an office is open; and a project tab
  /// with no project under it goes back to Home.
  void _onProject() {
    final t = widget.nav.tab;
    final projectTab =
        (t >= AppNav.dailyWork && t <= AppNav.agents) || t == AppNav.files; // procedures/gyms/chat live outside
    final draft = widget.manager.selectedId != null &&
        widget.manager.briefs.where((b) => b.id == widget.manager.selectedId).firstOrNull?.state == 'idle';
    if (widget.manager.selectedId == null && projectTab) {
      widget.nav.go(AppNav.chat);
    } else if (draft && projectTab && t != AppNav.brief) {
      widget.nav.go(AppNav.brief); // a draft's one page
    } else {
      setState(() {});
    }
  }

  /// The pages of a project in strip order, for Ctrl+1…6. Kept beside the
  /// strip's own list in [build]; the two must agree.
  static const _pages = [AppNav.dailyWork, AppNav.agents, AppNav.files, AppNav.route, AppNav.map, AppNav.budget, AppNav.brief];

  /// Whether a text field has the focus: the list keys (↑ ↓ j k Enter ×)
  /// are for the lists, not for someone typing.
  static bool get _typing => FocusManager.instance.primaryFocus?.context?.widget is EditableText;

  /// The keyboard, independent of where focus happens to be.
  ///
  ///   Ctrl+S        save (brief, procedure, or the Deputy's desk)
  ///   Ctrl+H        Home (the Deputy)        Ctrl+I   Inbox
  ///   Ctrl+1…6      a project's pages, in strip order
  ///   Ctrl+M        a memo to the loop (in a project that has run)
  ///   ↑ ↓ / j k     the previous / next sheet (Inbox, Daily work)
  ///   Delete / ×    set the open notice aside (Inbox)
  ///   Escape        back out of a full-window page
  bool _onKey(KeyEvent e) {
    if (e is! KeyDownEvent) return false;
    final k = HardwareKeyboard.instance;
    final ctrl = k.isControlPressed || k.isMetaPressed;
    final key = e.logicalKey;
    final tab = widget.nav.tab;
    final inProject = widget.manager.selectedId != null;
    if (ctrl) {
      if (key == LogicalKeyboardKey.keyS) {
        if (tab == AppNav.procedures) {
          widget.procedures.save();
        } else if (tab == AppNav.chat) {
          widget.deputy.desk.save();
        } else {
          widget.manager.save();
        }
        return true;
      }
      if (key == LogicalKeyboardKey.keyH) {
        if (inProject) widget.manager.deselect();
        widget.nav.go(AppNav.chat);
        return true;
      }
      if (key == LogicalKeyboardKey.keyI) {
        if (inProject) widget.manager.deselect();
        widget.nav.go(AppNav.inbox);
        return true;
      }
      if (key == LogicalKeyboardKey.keyM && inProject && widget.dailyWork.sessions.isNotEmpty) {
        ProjectBar.openMemo(context, widget.manager, widget.dailyWork);
        return true;
      }
      final n = key.keyLabel.length == 1 ? int.tryParse(key.keyLabel) : null;
      if (n != null && n >= 1 && n <= _pages.length && inProject) {
        final draft = widget.manager.briefs.where((b) => b.id == widget.manager.selectedId).firstOrNull?.state == 'idle';
        widget.nav.go(draft ? AppNav.brief : _pages[n - 1]);
        return true;
      }
      return false;
    }
    if (_typing) return false;
    if (key == LogicalKeyboardKey.escape && AppNav.overlays.contains(tab)) {
      widget.nav.closeOverlay();
      return true;
    }
    final down = key == LogicalKeyboardKey.arrowDown || key == LogicalKeyboardKey.keyJ;
    final up = key == LogicalKeyboardKey.arrowUp || key == LogicalKeyboardKey.keyK;
    if (down || up) {
      if (tab == AppNav.inbox) {
        widget.inbox.step(down ? 1 : -1);
        return true;
      }
      if (tab == AppNav.dailyWork) {
        widget.dailyWork.step(down ? 1 : -1);
        return true;
      }
      return false;
    }
    if ((key == LogicalKeyboardKey.delete || key == LogicalKeyboardKey.keyX) && tab == AppNav.inbox) {
      widget.inbox.dismissOpen();
      return true;
    }
    return false;
  }

  @override
  Widget build(BuildContext context) {
    // Home is the hallway; the other tabs are one office, and only show
    // once you are in one. Tab indices match the IndexedStack below.
    final inProject = widget.manager.selectedId != null;
    // A draft has one page, its brief: nothing has run, so there is no
    // daily work, no budget spent, no orders, no data, no agents.
    final isDraft = inProject &&
        (widget.manager.briefs.where((b) => b.id == widget.manager.selectedId).firstOrNull?.state == 'idle');
    // The pages of a project, by stack slot. The strip shows them in the
    // order a day uses them, most-visited first: the day's paper, the
    // agents at work, the orders, what they found, the money — and last
    // the brief, signed once at the start and rarely opened after.
    const pages = <int, (IconData, String)>{
      AppNav.dailyWork: (Icons.inbox_outlined, 'Daily work'),
      AppNav.agents: (Icons.terminal, 'Agents'),
      // The task's folder as the agents leave it: what is being worked on.
      AppNav.files: (Icons.folder_outlined, 'Files'),
      AppNav.route: (Icons.assignment_outlined, 'Work orders'),
      AppNav.map: (Icons.menu_book_outlined, 'Data book'),
      AppNav.budget: (Icons.account_balance_outlined, 'Budget'),
      AppNav.brief: (Icons.description_outlined, 'Brief'),
    };
    // Outside a task: Home (the orchestrator's desk) and the Inbox are
    // the doors, at the far left, drawn by the same widget inside an
    // office so the strip never shifts when you walk in; after them, the
    // shop's standing manuals.
    final order = isDraft ? const [AppNav.brief] : inProject ? _pages : const [AppNav.procedures, AppNav.gyms];
    const home = <int, (IconData, String)>{
      AppNav.procedures: (Icons.menu_book_outlined, 'Procedures'),
      // The environments a gym runs in, and what each is made of.
      AppNav.gyms: (Icons.fitness_center_outlined, 'Gyms'),
    };
    final shown = [
      for (final slot in order) home[slot] ?? pages[slot]!,
    ];
    const trailing = [
      (Icons.key_outlined, 'Providers'),
      (Icons.settings_outlined, ''),
    ];
    // Strip position ⇄ IndexedStack index.
    int toIndex(int i) {
      if (i < order.length) return order[i];
      return AppNav.providers + (i - order.length);
    }

    int fromIndex(int stack) {
      // The doors, not tabs.
      if (stack == AppNav.chat || stack == AppNav.inbox) return -1;
      if (stack >= AppNav.providers && stack < AppNav.chat) return order.length + (stack - AppNav.providers);
      return order.indexOf(stack);
    }

    if (_index == AppNav.style) {
      // Settings is the user's, not a project's: the whole window, no
      // sidebar, no strip.
      return Scaffold(
        body: SettingsScreen(
          settings: widget.style,
          storage: widget.storage,
          onClose: widget.nav.closeOverlay,
          onOpenProviders: () => widget.nav.go(AppNav.providers),
          procedures: widget.procedures,
          share: widget.share,
          closeApp: _closeApp(),
          openAt: widget.nav.settingsSection,
          openRequest: widget.nav.settingsRequest,
        ),
      );
    }
    if (_index == AppNav.providers) {
      // Providers likewise: a page you step into and back out of.
      return Scaffold(
        body: _OverlayPage(
          title: 'PROVIDERS',
          onBack: widget.nav.closeOverlay,
          closeApp: _closeApp(),
          child: ProvidersScreen(manager: widget.providers),
        ),
      );
    }
    return Scaffold(
      body: Row(
        children: [
          ProjectSidebar(
            manager: widget.manager,
            nav: widget.nav,
            providers: widget.providers,
            startCollapsed: widget.style.sidebarCollapsed,
            // Room for the task list, a page's list and the paper at full
            // width; short of that, one list at a time.
            narrow:
                MediaQuery.sizeOf(context).width <
                ProjectSidebar.width + 400 + DocumentSheet.sheetWidth + 60,
          ),
          const VerticalDivider(width: 1),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                OpsTabs(
                  index: fromIndex(_index),
                  onSelect: (i) => widget.nav.go(toIndex(i)),
                  items: shown,
                  trailing: trailing,
                  edge: _closeApp(),
                  // The Inbox door carries what waits on you; only the doors
                  // rebuild when the in-tray changes, not the whole shell.
                  leading: ListenableBuilder(
                    listenable: widget.inbox,
                    builder: (context, _) => _Doors(
                      tray: widget.inbox.unread,
                      // One underline at a time: Home's only on Home itself; on
                      // Procedures or Gyms the tab carries it.
                      active: inProject
                          ? null
                          : _index == AppNav.inbox
                          ? 'inbox'
                          : _index == AppNav.chat
                          ? 'home'
                          : null,
                      onHome: () {
                        if (inProject) widget.manager.deselect();
                        widget.nav.go(AppNav.chat);
                      },
                      onInbox: () {
                        if (inProject) widget.manager.deselect();
                        widget.nav.go(AppNav.inbox);
                      },
                    ),
                  ),
                ),
                // The project's line: on every page of a project that has
                // run. A draft has only its brief, and the doors have none.
                if (inProject && !isDraft) ProjectBar(briefs: widget.manager, work: widget.dailyWork),
                Expanded(
                  child: IndexedStack(
                    index: _index,
                    children: [
                      InboxScreen(
                        manager: widget.inbox,
                        briefs: widget.manager,
                        nav: widget.nav,
                        settings: widget.style,
                      ),
                      DailyWorkScreen(
                        manager: widget.dailyWork,
                        briefs: widget.manager,
                        nav: widget.nav,
                        settings: widget.style,
                      ),
                      BriefScreen(
                        manager: widget.manager,
                        route: widget.route,
                        nav: widget.nav,
                        settings: widget.style,
                        providers: widget.providers,
                      ),
                      BudgetScreen(manager: widget.budget),
                      WorkOrdersScreen(
                        manager: widget.workOrders,
                        briefs: widget.manager,
                        nav: widget.nav,
                        settings: widget.style,
                      ),
                      DataBookScreen(manager: widget.databook),
                      AgentsScreen(manager: widget.agents),
                      const SizedBox.shrink(), // providers renders full-window above
                      const SizedBox.shrink(), // settings renders full-window above
                      DeputyScreen(
                        manager: widget.deputy,
                        settings: widget.style,
                        providers: widget.providers,
                        nav: widget.nav,
                      ),
                      ProceduresScreen(manager: widget.procedures),
                      GymsScreen(manager: widget.gyms, briefs: widget.manager, nav: widget.nav),
                      FilesScreen(engine: widget.engine, briefs: widget.manager, nav: widget.nav),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// The parts of a project, as tabs across the top of its content. The
/// accent underlines the selected part; [trailing] tabs sit at the right.
class OpsTabs extends StatelessWidget {
  const OpsTabs({
    super.key,
    required this.index,
    required this.onSelect,
    required this.items,
    this.trailing = const [],
    this.leading,
    this.badges = const {},
    this.edge,
  });
  final int index;
  final ValueChanged<int> onSelect;
  final List<(IconData, String)> items;

  /// Strip position → count shown after the label (the Inbox's tray).
  final Map<int, int> badges;

  /// Pinned after the trailing tabs, at the very edge: the app's close.
  final Widget? edge;

  /// Sits before the tabs: the breadcrumb into the open project.
  final Widget? leading;

  /// Pinned to the right; indexed after [items].
  final List<(IconData, String)> trailing;

  /// Whether every tab fits with its label at [width]; when not, the strip
  /// goes to icons only (an iPad, a narrow window) and the names move to
  /// tooltips. Estimated from the label text, not measured: close enough
  /// for a strip that only ever has a dozen entries.
  bool _labelsFit(BuildContext context, double width) {
    final style = opsLabelStyle(context);
    double tab(String label, int badge) {
      final t = TextPainter(
        text: TextSpan(text: label.toUpperCase(), style: style),
        textDirection: TextDirection.ltr,
      )..layout();
      return Sp.m * 2 +
          16 +
          (label.isEmpty ? 0 : 6 + t.width) +
          (badge > 0 ? 30 : 0);
    }

    var need =
        Sp.l * 2 + (leading != null ? 220 : 0) + (edge != null ? 34 : 0) + 24;
    for (final (i, (_, label)) in items.indexed) {
      need += tab(label, badges[i] ?? 0);
    }
    for (final (i, (_, label)) in trailing.indexed) {
      need += tab(label, badges[items.length + i] ?? 0);
    }
    return need <= width;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      height: 48,
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: LayoutBuilder(
        builder: (context, c) {
          final labels = _labelsFit(context, c.maxWidth);
          return Row(
            children: [
              const SizedBox(width: Sp.l),
              if (leading != null) ...[
                leading!,
                if (items.isNotEmpty) ...[
                  // A group boundary is wider than the gap between tabs:
                  // 16 each side of the hairline against 24 between two
                  // tabs, so the groups read as groups.
                  const SizedBox(width: Sp.l),
                  Container(width: 1, height: 18, color: cs.outlineVariant),
                  const SizedBox(width: Sp.l),
                ],
              ],
              for (final (i, (icon, label)) in items.indexed)
                _tab(context, i, icon, label, labels: labels),
              const Spacer(),
              for (final (i, (icon, label)) in trailing.indexed)
                _tab(context, items.length + i, icon, label, labels: labels),
              if (edge != null) ...[const SizedBox(width: Sp.s), edge!],
              const SizedBox(width: Sp.l),
            ],
          );
        },
      ),
    );
  }

  Widget _tab(
    BuildContext context,
    int i,
    IconData icon,
    String label, {
    required bool labels,
  }) {
    final cs = Theme.of(context).colorScheme;
    final on = i == index;
    // Selection is ink, not the red accent: red means stopped now.
    final color = on ? AppTheme.ink(context) : cs.onSurfaceVariant;
    final showLabel = labels && label.isNotEmpty;
    return Tooltip(
      message: showLabel ? '' : label.toUpperCase(),
      waitDuration: const Duration(milliseconds: 400),
      child: InkWell(
        onTap: () => onSelect(i),
        child: Container(
          // Full strip height so the whole tab is the hit area.
          height: double.infinity,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: Sp.m),
          // The chosen tab is marked by an underline and ink, never by
          // weight: a heavier label is a wider one, and the tabs to its
          // right shifted on every click.
          decoration: BoxDecoration(
            border: Border(
              bottom: BorderSide(color: on ? AppTheme.ink(context) : Colors.transparent, width: 2),
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: showLabel ? 16 : 18, color: color),
              if (showLabel) ...[
                const SizedBox(width: 6),
                Text(
                  label.toUpperCase(),
                  style: opsLabelStyle(context).copyWith(color: color, fontWeight: FontWeight.w500),
                ),
              ],
              if ((badges[i] ?? 0) > 0) ...[
                const SizedBox(width: Sp.s),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 6,
                    vertical: 1,
                  ),
                  decoration: BoxDecoration(
                    color: cs.primary,
                    borderRadius: BorderRadius.circular(9),
                  ),
                  child: Text(
                    '${badges[i]}',
                    style: AppTheme.mono.copyWith(
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                      color: cs.onPrimary,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// The doors out of an office, at the strip's left: Home, and the Inbox
/// with its count. Not styled as "back" — history is a different thing.
class _Doors extends StatelessWidget {
  const _Doors({
    required this.tray,
    required this.onHome,
    required this.onInbox,
    this.active,
  });
  final int tray;
  final VoidCallback onHome;
  final VoidCallback onInbox;

  /// `home` | `inbox` when one of them is the page showing; null in a task.
  final String? active;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    Widget door(
      IconData icon,
      String label,
      VoidCallback onTap, {
      int badge = 0,
      bool on = false,
    }) => InkWell(
      onTap: onTap,
      child: Container(
        height: double.infinity,
        alignment: Alignment.center,
        padding: const EdgeInsets.symmetric(horizontal: Sp.m),
        decoration: BoxDecoration(
          border: Border(
            bottom: BorderSide(color: on ? AppTheme.ink(context) : Colors.transparent, width: 2),
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              icon,
              size: 16,
              color: on ? AppTheme.ink(context) : cs.onSurfaceVariant,
            ),
            const SizedBox(width: 6),
            Text(
              label,
              style: opsLabelStyle(context).copyWith(
                color: on ? AppTheme.ink(context) : null,
                fontWeight: FontWeight.w500,
              ),
            ),
            if (badge > 0) ...[
              const SizedBox(width: Sp.s),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  color: cs.primary,
                  borderRadius: BorderRadius.circular(9),
                ),
                child: Text(
                  '$badge',
                  style: AppTheme.mono.copyWith(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: cs.onPrimary,
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        door(Icons.home_outlined, 'HOME', onHome, on: active == 'home'),
        door(
          Icons.mail_outline,
          'INBOX',
          onInbox,
          badge: tray,
          on: active == 'inbox',
        ),
      ],
    );
  }
}

/// The frame for a full-window page: a thin header with ← BACK and the
/// page's name, then the page. Settings draws its own; Providers uses this.
class _OverlayPage extends StatelessWidget {
  const _OverlayPage({
    required this.title,
    required this.onBack,
    required this.child,
    this.closeApp,
  });
  final String title;
  final VoidCallback onBack;
  final Widget child;
  final Widget? closeApp;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          height: 48,
          // Right inset matches the tab strip so the close button never moves.
          padding: const EdgeInsets.fromLTRB(Sp.xl, 0, Sp.l, 0),
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: cs.outlineVariant)),
          ),
          child: Row(
            children: [
              InkWell(
                onTap: onBack,
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: Sp.s,
                    vertical: Sp.m,
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Icons.arrow_back,
                        size: 16,
                        color: cs.onSurfaceVariant,
                      ),
                      const SizedBox(width: Sp.s),
                      Text('BACK', style: opsLabelStyle(context)),
                    ],
                  ),
                ),
              ),
              const SizedBox(width: Sp.l),
              Text(
                title,
                style: opsLabelStyle(context)
                    .copyWith(color: AppTheme.ink(context)),
              ),
              const Spacer(),
              ?closeApp,
            ],
          ),
        ),
        Expanded(child: child),
      ],
    );
  }
}

/// The window has no frame, so the app closes itself: a soft round
/// button at the far right — a quiet grey disc with an ×, a shade
/// brighter under the pointer. Unsaved brief edits get the usual
/// question first.
extension on _ShellState {
  /// The round close at the edge: only where there is an app to close.
  Widget? _closeApp() =>
      widget.remote ? null : _CloseApp(manager: widget.manager);
}

class _CloseApp extends StatefulWidget {
  const _CloseApp({required this.manager});
  final BriefManager manager;

  @override
  State<_CloseApp> createState() => _CloseAppState();
}

class _CloseAppState extends State<_CloseApp> {
  bool hover = false;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Tooltip(
      message: 'Close Mizpah',
      waitDuration: const Duration(milliseconds: 500),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => hover = true),
        onExit: (_) => setState(() => hover = false),
        child: GestureDetector(
          onTap: () async {
            if (await confirmLeave(context, widget.manager)) {
              await SystemNavigator.pop();
            }
          },
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 120),
            width: 26,
            height: 26,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: cs.onSurface.withValues(alpha: hover ? 0.18 : 0.10),
              shape: BoxShape.circle,
            ),
            child: Icon(Icons.close, size: 14, color: cs.onSurface),
          ),
        ),
      ),
    );
  }
}
