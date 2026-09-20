import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'engine/fake_engine.dart';
import 'engine/host_watch.dart';
import 'engine/provider_client.dart';
import 'screens/brief/brief_screen.dart';
import 'screens/budget/budget_screen.dart';
import 'screens/databook/databook_screen.dart';
import 'screens/home/home_screen.dart';
import 'screens/inbox/inbox_screen.dart';
import 'screens/loop/loop_screen.dart';
import 'screens/providers/providers_screen.dart';
import 'screens/settings/settings_screen.dart';
import 'screens/workorders/work_orders_screen.dart';
import 'state/app_nav.dart';
import 'state/brief_manager.dart';
import 'state/budget_manager.dart';
import 'state/databook_manager.dart';
import 'state/home_manager.dart';
import 'state/inbox_manager.dart';
import 'state/loop_manager.dart';
import 'state/provider_manager.dart';
import 'state/route_manager.dart';
import 'state/app_settings.dart';
import 'state/work_orders_manager.dart';
import 'theme/app_theme.dart';
import 'theme/kit_styles.dart';
import 'widgets/project_sidebar.dart';

void main() {
  final settings = AppSettings();
  final engine = FakeEngine(
    runsRoot: Directory(settings.runsRoot),
    poll: Duration(seconds: settings.pollSeconds),
  );
  // Runs root and poll follow the settings live; engine tool paths are
  // read once, at launch.
  var root = settings.runsRoot, poll = settings.pollSeconds;
  settings.addListener(() {
    if (settings.runsRoot != root) {
      root = settings.runsRoot;
      engine.setRunsRoot(Directory(root));
    }
    if (settings.pollSeconds != poll) {
      poll = settings.pollSeconds;
      engine.setPoll(Duration(seconds: poll));
    }
  });
  final briefs = BriefManager(engine);
  // The app catches what a loop cannot: processes left on the host after
  // the loop that started them is gone.
  final host = HostWatch(
    runsRoot: Directory(settings.runsRoot),
    poll: Duration(seconds: settings.pollSeconds),
  );
  settings.addListener(() {
    host.setRunsRoot(Directory(settings.runsRoot));
    host.setPoll(Duration(seconds: settings.pollSeconds));
  });
  runApp(
    MizpahApp(
      manager: briefs,
      home: HomeManager(engine),
      host: host,
      inbox: InboxManager(engine, briefs),
      databook: DataBookManager(engine, briefs),
      budget: BudgetManager(engine, briefs),
      workOrders: WorkOrdersManager(engine, briefs),
      loop: LoopManager(engine, briefs),
      route: RouteManager(engine, briefs),
      providers: ProviderManager(
        ProcessProviderClient(
          executable: settings.providerScript,
          config: settings.engineConfig,
        ),
      ),
      nav: AppNav(),
      style: settings,
    ),
  );
}

class MizpahApp extends StatelessWidget {
  const MizpahApp({
    super.key,
    required this.manager,
    required this.home,
    required this.host,
    required this.inbox,
    required this.databook,
    required this.budget,
    required this.workOrders,
    required this.loop,
    required this.route,
    required this.providers,
    required this.nav,
    required this.style,
  });
  final BriefManager manager;
  final HomeManager home;
  final HostWatch host;
  final InboxManager inbox;
  final DataBookManager databook;
  final BudgetManager budget;
  final WorkOrdersManager workOrders;
  final LoopManager loop;
  final RouteManager route;
  final ProviderManager providers;
  final AppNav nav;
  final AppSettings style;

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
          home: home,
          host: host,
          inbox: inbox,
          databook: databook,
          budget: budget,
          workOrders: workOrders,
          loop: loop,
          route: route,
          providers: providers,
          nav: nav,
          style: style,
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
    required this.home,
    required this.host,
    required this.inbox,
    required this.databook,
    required this.budget,
    required this.workOrders,
    required this.loop,
    required this.route,
    required this.providers,
    required this.nav,
    required this.style,
  });
  final BriefManager manager;
  final HomeManager home;
  final HostWatch host;
  final InboxManager inbox;
  final DataBookManager databook;
  final BudgetManager budget;
  final WorkOrdersManager workOrders;
  final LoopManager loop;
  final RouteManager route;
  final ProviderManager providers;
  final AppNav nav;
  final AppSettings style;

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
    HardwareKeyboard.instance.addHandler(_onKey);
  }

  @override
  void dispose() {
    widget.nav.removeListener(_onNav);
    widget.manager.removeListener(_onProject);
    HardwareKeyboard.instance.removeHandler(_onKey);
    super.dispose();
  }

  void _onNav() => setState(() {});

  /// The strip depends on whether an office is open; and a project tab
  /// with no project under it goes back to Home.
  void _onProject() {
    final t = widget.nav.tab;
    final projectTab = t >= AppNav.inbox && t <= AppNav.agents;
    if (widget.manager.selectedId == null && projectTab) {
      widget.nav.go(AppNav.home);
    } else {
      setState(() {});
    }
  }

  /// Global Ctrl+S / Cmd+S, independent of where focus happens to be.
  bool _onKey(KeyEvent e) {
    if (e is! KeyDownEvent || e.logicalKey != LogicalKeyboardKey.keyS) {
      return false;
    }
    final k = HardwareKeyboard.instance;
    if (!(k.isControlPressed || k.isMetaPressed)) return false;
    widget.manager.save();
    return true;
  }

  @override
  Widget build(BuildContext context) {
    // Home is the hallway; the other tabs are one office, and only show
    // once you are in one. Tab indices match the IndexedStack below.
    final inProject = widget.manager.selectedId != null;
    const tabs = [
      (Icons.home_outlined, 'Home'),
      (Icons.inbox_outlined, 'Daily work'),
      (Icons.description_outlined, 'Brief'),
      (Icons.assignment_outlined, 'Work orders'),
      (Icons.menu_book_outlined, 'Data book'),
      (Icons.account_balance_outlined, 'Budget'),
    ];
    // In an office the Home tab goes; a Home button at the far left is
    // the way out (not styled as "back" — a real back may come later).
    final shown = inProject ? tabs.sublist(1) : tabs.sublist(0, 1);
    const trailing = [
      (Icons.key_outlined, 'Providers'),
      (Icons.settings_outlined, ''),
    ];
    // Strip position ⇄ IndexedStack index. Agents keeps its stack slot
    // (route deep-links still reach it) but has no tab.
    int toIndex(int i) {
      if (i < shown.length) return inProject ? i + 1 : i;
      return AppNav.providers + (i - shown.length);
    }
    int fromIndex(int stack) {
      if (stack < tabs.length) {
        if (!inProject) return stack == 0 ? 0 : -1;
        return stack == 0 ? -1 : stack - 1;
      }
      if (stack == AppNav.agents) return -1;
      return shown.length + (stack - AppNav.providers);
    }
    if (_index == AppNav.style) {
      // Settings is the user's, not a project's: the whole window, no
      // sidebar, no strip.
      return Scaffold(
        body: SettingsScreen(settings: widget.style, onClose: widget.nav.closeSettings),
      );
    }
    return Scaffold(
      body: Row(
        children: [
          ProjectSidebar(manager: widget.manager, nav: widget.nav),
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
                  leading: inProject
                      ? _HomeButton(
                          onHome: () {
                            widget.manager.deselect();
                            widget.nav.go(AppNav.home);
                          },
                        )
                      : null,
                ),
                Expanded(
                  child: IndexedStack(
                    index: _index,
                    children: [
                      HomeScreen(
                        manager: widget.home,
                        briefs: widget.manager,
                        nav: widget.nav,
                      ),
                      InboxScreen(
                        manager: widget.inbox,
                        briefs: widget.manager,
                        nav: widget.nav,
                      ),
                      BriefScreen(
                        manager: widget.manager,
                        route: widget.route,
                        nav: widget.nav,
                      ),
                      WorkOrdersScreen(
                        manager: widget.workOrders,
                        briefs: widget.manager,
                        nav: widget.nav,
                      ),
                      DataBookScreen(manager: widget.databook),
                      BudgetScreen(manager: widget.budget),
                      LoopScreen(manager: widget.loop),
                      ProvidersScreen(manager: widget.providers),
                      const SizedBox.shrink(), // settings renders full-window above
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
  });
  final int index;
  final ValueChanged<int> onSelect;
  final List<(IconData, String)> items;

  /// Sits before the tabs: the breadcrumb into the open project.
  final Widget? leading;

  /// Pinned to the right; indexed after [items].
  final List<(IconData, String)> trailing;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      height: 48,
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outlineVariant)),
      ),
      child: Row(
        children: [
          const SizedBox(width: Sp.l),
          if (leading != null) ...[
            leading!,
            Container(width: 1, height: 18, color: cs.outlineVariant),
            const SizedBox(width: Sp.s),
          ],
          for (final (i, (icon, label)) in items.indexed)
            _tab(context, i, icon, label),
          const Spacer(),
          for (final (i, (icon, label)) in trailing.indexed)
            _tab(context, items.length + i, icon, label),
          const SizedBox(width: Sp.l),
        ],
      ),
    );
  }

  Widget _tab(BuildContext context, int i, IconData icon, String label) {
    final cs = Theme.of(context).colorScheme;
    final on = i == index;
    final color = on ? cs.primary : cs.onSurfaceVariant;
    return InkWell(
      onTap: () => onSelect(i),
      child: Container(
        // Full strip height so the whole tab is the hit area.
        height: double.infinity,
        alignment: Alignment.center,
        padding: const EdgeInsets.symmetric(horizontal: Sp.l),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: label.isEmpty ? 18 : 16, color: color),
            if (label.isNotEmpty) ...[
              const SizedBox(width: Sp.s),
              Text(
                label.toUpperCase(),
                style: opsLabelStyle(context).copyWith(
                  color: color,
                  fontWeight: on ? FontWeight.w700 : FontWeight.w500,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// The door out of an office: a Home button at the strip's left. Not a
/// back arrow — history is a different thing and may get its own control.
class _HomeButton extends StatelessWidget {
  const _HomeButton({required this.onHome});
  final VoidCallback onHome;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onHome,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: Sp.m, vertical: Sp.m),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.home_outlined, size: 16, color: cs.onSurfaceVariant),
            const SizedBox(width: Sp.s),
            Text('HOME', style: opsLabelStyle(context)),
          ],
        ),
      ),
    );
  }
}
