import 'package:flutter/material.dart';

import '../../engine/procedure_history.dart';
import '../../models/procedure.dart';
import '../../state/procedures_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';
import '../../widgets/document_sheet.dart' show whenLabel;
import '../../widgets/facet_filter.dart';
import '../../widgets/inline_text.dart';
import '../../widgets/ops.dart';

/// The procedures manual. The shop's methods, searchable on the left; the
/// open one on the right as a document a person can correct in place —
/// title, when to pick it, tags, and every step. Where a worker's bad
/// step gets fixed before the next worker follows it.
class ProceduresScreen extends StatelessWidget {
  const ProceduresScreen({super.key, required this.manager});
  final ProceduresManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        final shown = manager.shown;
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(
              width: 380,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(Sp.xl, Sp.l, Sp.xl, Sp.s),
                    child: Row(
                      children: [
                        Text('PROCEDURES', style: opsHeadingStyle(context)),
                        const SizedBox(width: Sp.s),
                        FacetFilterButton(
                          facets: manager.facets,
                          selected: manager.picked,
                          onChanged: manager.setPicked,
                        ),
                        const SizedBox(width: Sp.xs),
                        IconButton(
                          tooltip: 'New procedure',
                          iconSize: 18,
                          icon: const Icon(Icons.add),
                          onPressed: () => _newProcedure(context),
                        ),
                        const Spacer(),
                        Text(
                          manager.loading
                              ? 'READING…'
                              : manager.filtered
                              ? '${shown.length} OF ${manager.all.length}'
                              : '${manager.all.length}',
                          style: opsLabelStyle(context),
                        ),
                      ],
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(Sp.xl, 0, Sp.xl, Sp.s),
                    child: _Search(onChanged: manager.search),
                  ),
                  Divider(height: 1, color: cs.outlineVariant),
                  Expanded(child: _List(shown: shown, manager: manager)),
                ],
              ),
            ),
            VerticalDivider(width: 1, color: cs.outlineVariant),
            Expanded(
              child: manager.opened == null
                  ? Center(
                      child: Text(
                        manager.loading ? 'Opening the manual…' : 'No procedure matches.',
                        style: Theme.of(context).textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant),
                      ),
                    )
                  : _Sheet(p: manager.opened!, manager: manager),
            ),
          ],
        );
      },
    );
  }
}

extension on ProceduresScreen {
  /// Ask for the title on a sheet; the rest is written on the document.
  Future<void> _newProcedure(BuildContext context) async {
    final controller = TextEditingController();
    final title = await showOpsDialog<String>(
      context,
      tag: 'NEW PROCEDURE',
      title: 'What is the method called?',
      body: 'A blank procedure opens for you to write: when to pick it, then the steps.',
      field: (ctx) => TextField(
        controller: controller,
        autofocus: true,
        onSubmitted: (v) => Navigator.pop(ctx, v),
        decoration: const InputDecoration(hintText: 'Verify a CLI against the map by subprocess'),
      ),
      actions: (ctx) => Row(
        children: [
          FilledButton(onPressed: () => Navigator.pop(ctx, controller.text), child: const Text('CREATE')),
          const SizedBox(width: Sp.m),
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('CANCEL')),
        ],
      ),
    );
    controller.dispose();
    if (title == null || title.trim().isEmpty) return;
    await manager.create(title.trim());
  }
}

/// The list, which can scroll a row into view when asked (a procedure
/// just created lands wherever the order puts it).
class _List extends StatefulWidget {
  const _List({required this.shown, required this.manager});
  final List<Procedure> shown;
  final ProceduresManager manager;

  @override
  State<_List> createState() => _ListState();
}

class _ListState extends State<_List> {
  final _keys = <String, GlobalKey>{};
  final _scroll = ScrollController();

  @override
  void didUpdateWidget(covariant _List old) {
    super.didUpdateWidget(old);
    _maybeReveal();
  }

  @override
  void initState() {
    super.initState();
    _maybeReveal();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  /// A row off screen is not built, so its key has no context. Jump by
  /// index to roughly where it is, then settle on it once it exists.
  void _maybeReveal() {
    final id = widget.manager.reveal;
    if (id == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      final i = widget.shown.indexWhere((p) => p.id == id);
      if (i < 0) return;
      final ctx = _keys[id]?.currentContext;
      if (ctx != null) {
        Scrollable.ensureVisible(ctx, alignment: 0.3, duration: const Duration(milliseconds: 260));
        widget.manager.reveal = null;
        return;
      }
      final n = widget.shown.length;
      final approx = n <= 1 ? 0.0 : _scroll.position.maxScrollExtent * (i / (n - 1));
      _scroll.jumpTo(approx.clamp(0.0, _scroll.position.maxScrollExtent));
      // Now it is built; settle next frame.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        final c = _keys[id]?.currentContext;
        if (c != null) Scrollable.ensureVisible(c, alignment: 0.3, duration: const Duration(milliseconds: 200));
        widget.manager.reveal = null;
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final m = widget.manager;
    final shown = widget.shown;
    // Decayed procedures sit in a drawer under the list, folded until
    // opened by hand, the way the archive does on the task board.
    final retired = m.retiredShown;
    final drawer = m.policy.enabled || m.retiredCount > 0;
    final rows = <Widget>[
      for (final p in shown) _row(context, p),
      if (drawer)
        _DrawerHead(
          count: m.retiredCount,
          matching: retired.length,
          open: m.showRetired,
          onTap: m.toggleRetired,
        ),
      if (drawer && m.showRetired) for (final p in retired) _row(context, p),
    ];
    return ListView.separated(
      controller: _scroll,
      itemCount: rows.length,
      separatorBuilder: (_, _) => Divider(height: 1, color: cs.outlineVariant),
      itemBuilder: (context, i) => rows[i],
    );
  }

  Widget _row(BuildContext context, Procedure p) => _Row(
          key: _keys.putIfAbsent(p.id, () => GlobalKey()),
          p: p,
          selected: p.id == widget.manager.openId,
          decay: widget.manager.policy.enabled,
          onTap: () async {
            final m = widget.manager;
            if (m.dirty && p.id != m.openId) {
              final leave = await showOpsDialog<bool>(
                context,
                tag: 'UNSAVED',
                title: 'Leave ${m.opened?.title ?? 'this procedure'} without saving?',
                body: 'Your edits have not been written to the playbook.',
                actions: (ctx) => Row(
                  children: [
                    FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('DISCARD')),
                    const SizedBox(width: Sp.m),
                    TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('STAY')),
                  ],
                ),
              );
              if (leave != true) return;
            }
            m.open(p);
          },
        );
}

/// The fold between live procedures and the decayed ones.
class _DrawerHead extends StatelessWidget {
  const _DrawerHead({required this.count, required this.matching, required this.open, required this.onTap});
  final int count;
  final int matching;
  final bool open;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      child: Container(
        color: cs.surfaceContainerLow,
        padding: const EdgeInsets.fromLTRB(Sp.l, Sp.s, Sp.l, Sp.s),
        child: Row(
          children: [
            Icon(open ? Icons.expand_more : Icons.chevron_right, size: 16, color: cs.onSurfaceVariant),
            const SizedBox(width: Sp.xs),
            Text('DECAYED · $count', style: opsLabelStyle(context)),
            const Spacer(),
            if (open && matching != count) Text('$matching shown', style: opsKeyStyle(context)),
          ],
        ),
      ),
    );
  }
}

class _Search extends StatelessWidget {
  const _Search({required this.onChanged});
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return SizedBox(
      height: 32,
      child: TextField(
        onChanged: onChanged,
        style: AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurface),
        decoration: InputDecoration(
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          prefixIcon: Icon(Icons.search, size: 16, color: cs.onSurfaceVariant),
          prefixIconConstraints: const BoxConstraints(minWidth: 30),
          hintText: 'find a procedure',
          hintStyle: AppTheme.mono.copyWith(fontSize: 13, color: cs.onSurfaceVariant),
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

class _Row extends StatelessWidget {
  const _Row({super.key, required this.p, required this.selected, required this.onTap, this.decay = false});
  final Procedure p;
  final bool selected;
  final VoidCallback onTap;

  /// Whether use-it-or-lose-it is on: then the gates left are worth a word.
  final bool decay;

  /// The standing as one short phrase, or nothing worth saying.
  static String standingLabel(Procedure p, {required bool decay}) {
    final s = p.standing;
    if (s.retired) return 'decayed${s.retiredReason == 'manual' ? ' by hand' : ''}${s.retiredAt != null ? ' at gate ${s.retiredAt}' : ''}';
    if (s.pinned) return 'pinned';
    if (s.protected) return 'core';
    if (decay && s.graceLeft != null) return '${s.graceLeft} gate${s.graceLeft == 1 ? '' : 's'} left';
    return '';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    return InkWell(
      onTap: onTap,
      child: Container(
        decoration: BoxDecoration(
          color: selected ? cs.surfaceContainer : null,
          border: Border(left: BorderSide(color: selected ? ink : Colors.transparent, width: 3)),
        ),
        padding: const EdgeInsets.fromLTRB(Sp.xl - 3, Sp.m, Sp.l, Sp.m),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(p.id, overflow: TextOverflow.ellipsis,
                      style: AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant)),
                ),
                if (p.standing.pinned) Icon(Icons.push_pin, size: 12, color: cs.onSurfaceVariant),
              ],
            ),
            const SizedBox(height: 4),
            Text(p.title, maxLines: 2, overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyMedium!.copyWith(
                  color: p.retired ? cs.onSurfaceVariant : selected ? ink : cs.onSurface,
                  height: 1.35,
                )),
            const SizedBox(height: 4),
            Builder(builder: (context) {
              final standing = standingLabel(p, decay: decay);
              final low = decay && !p.retired && (p.standing.graceLeft ?? 99) <= 3;
              return Text(
                '${p.steps.length} steps'
                '${p.usedBy.isNotEmpty ? ' · used ${p.usedBy.length}×' : ''}'
                ' · ${whenLabel(p.updatedAt)}'
                '${standing.isEmpty ? '' : ' · $standing'}',
                // Amber (colorScheme.error in this theme) when it is about to go: a watch, not a stop.
                style: opsKeyStyle(context).copyWith(color: low ? Theme.of(context).colorScheme.error : null),
              );
            }),
          ],
        ),
      ),
    );
  }
}

/// The procedure as a document: editable in place, the way the brief is.
class _Sheet extends StatelessWidget {
  const _Sheet({required this.p, required this.manager});
  final Procedure p;
  final ProceduresManager manager;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final body = theme.textTheme.bodyLarge!.copyWith(fontSize: 16, color: ink, height: 1.5);
    final key = '${p.id}:${p.updatedAt.millisecondsSinceEpoch}';
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
                  Text('PROCEDURE  ${p.id}', style: opsLabelStyle(context)),
                  const SizedBox(width: Sp.l),
                  if (manager.dirty) ...[
                    Text('• UNSAVED', style: opsLabelStyle(context).copyWith(color: cs.primary)),
                    const SizedBox(width: Sp.m),
                    FilledButton(
                      onPressed: manager.saving ? null : manager.save,
                      style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12)),
                      child: Text(manager.saving ? 'SAVING…' : 'SAVE  ⌃S'),
                    ),
                    const SizedBox(width: Sp.s),
                    TextButton(onPressed: manager.saving ? null : manager.revert, child: const Text('REVERT')),
                  ],
                  const Spacer(),
                  // Use it or lose it: keep it for good, put it away, or bring
                  // it back. Core procedures cannot be put away.
                  if (p.retired)
                    FilledButton(
                      onPressed: () => manager.revive(p.id),
                      style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12)),
                      child: const Text('REVIVE'),
                    )
                  else if (!p.standing.protected) ...[
                    Tooltip(
                      message: p.standing.pinned ? 'Let it decay again' : 'Never decay',
                      child: TextButton(
                        onPressed: () => manager.pin(p.id, !p.standing.pinned),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(p.standing.pinned ? Icons.push_pin : Icons.push_pin_outlined, size: 14),
                            const SizedBox(width: 6),
                            Text(p.standing.pinned ? 'PINNED' : 'PIN'),
                          ],
                        ),
                      ),
                    ),
                    TextButton(onPressed: () => manager.retire(p.id), child: const Text('DECAY')),
                  ],
                  const SizedBox(width: Sp.s),
                  OpsRemove(onPressed: () => _delete(context), tooltip: 'Delete procedure'),
                ],
              ),
              if (p.retired) ...[
                const SizedBox(height: Sp.s),
                Text(
                  'DECAYED · ${p.standing.retiredReason == 'manual' ? 'by hand' : 'nobody touched it for long enough'}'
                  '${p.standing.retiredAt != null ? ' · gate ${p.standing.retiredAt}' : ''} · workers cannot find it',
                  style: opsLabelStyle(context).copyWith(color: AppTheme.removed(context)),
                ),
              ],
              const SizedBox(height: Sp.m),
              InlineText(
                value: p.title,
                resetKey: key,
                style: theme.textTheme.headlineSmall!.copyWith(fontWeight: FontWeight.w700, color: ink, height: 1.25),
                placeholder: 'Title',
                onChanged: (_) {},
                onCommit: (v) => manager.rename(p.id, v),
              ),
              const SizedBox(height: Sp.l),
              _Provenance(p: p, decay: manager.policy.enabled),
              const SizedBox(height: Sp.m),
              Divider(color: cs.outline, thickness: 1.5),
              const SizedBox(height: Sp.xl),
              const OpsLabel('WHEN TO PICK IT'),
              const SizedBox(height: Sp.m),
              InlineText(
                value: p.description,
                resetKey: key,
                style: body,
                placeholder: 'When a worker should choose this procedure over another.',
                onChanged: (_) {},
                onCommit: (v) => manager.describe(p.id, v),
              ),
              const SizedBox(height: Sp.l),
              _Tags(p: p, manager: manager, resetKey: key),
              const SizedBox(height: Sp.xxl),
              const OpsLabel('STEPS'),
              const SizedBox(height: Sp.s),
              // Drag the grip under a step's number to reorder; the playbook
              // moves it and the list re-reads.
              ReorderableListView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                buildDefaultDragHandles: false,
                itemCount: p.steps.length,
                onReorderItem: (from, to) {
                  if (to == from) return;
                  manager.moveStep(p.id, p.steps[from].title, to + 1);
                },
                proxyDecorator: (child, _, _) => Material(color: cs.surfaceContainer, child: child),
                itemBuilder: (context, i) => _Step(
                  key: ValueKey('${p.id}/${p.steps[i].id}'),
                  p: p,
                  i: i,
                  step: p.steps[i],
                  manager: manager,
                  resetKey: key,
                ),
              ),
              OpsAddLine(label: 'step', onTap: () => _addStep(context)),
              if (manager.error != null) ...[
                const SizedBox(height: Sp.l),
                OpsError(manager.error!),
              ],
              const SizedBox(height: Sp.xxl),
              _History(manager: manager),
            ],
          ),
        ),
      ),
    );
  }

  /// Delete, with the one warning that matters: who still runs this one.
  Future<void> _delete(BuildContext context) async {
    final linkers = manager.linkersOf(p.id);
    final used = p.usedBy.length;
    final ok = await showOpsDialog<bool>(
      context,
      tag: 'DELETE PROCEDURE',
      title: linkers.isEmpty ? 'Delete ${p.title}?' : '${p.title} is still linked',
      body: linkers.isNotEmpty
          ? 'A step of ${linkers.map((l) => l.id).join(', ')} runs this procedure. '
              'Unlink ${linkers.length == 1 ? 'that step' : 'those steps'} first; deleting now would leave '
              '${linkers.length == 1 ? 'it' : 'them'} pointing at nothing.'
          : used > 0
          ? 'Workers have opened it $used time${used == 1 ? '' : 's'}. It leaves the store; '
              'the runs that used it keep their records.'
          : 'It leaves the store. Nothing links it and no run has used it.',
      actions: (ctx) => Row(
        children: [
          if (linkers.isEmpty)
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              style: FilledButton.styleFrom(backgroundColor: AppTheme.removed(context), foregroundColor: Theme.of(context).colorScheme.surface),
              child: const Text('DELETE'),
            ),
          if (linkers.isEmpty) const SizedBox(width: Sp.m),
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(linkers.isEmpty ? 'KEEP' : 'CLOSE')),
        ],
      ),
    );
    if (ok == true) await manager.delete(p.id);
  }

  Future<void> _addStep(BuildContext context) async {
    final title = TextEditingController();
    final do_ = TextEditingController();
    final ok = await showOpsDialog<bool>(
      context,
      tag: 'NEW STEP',
      title: 'Add a step to ${p.title}',
      body: 'A short trail label, then the imperative: do this.',
      field: (ctx) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(controller: title, autofocus: true, decoration: const InputDecoration(hintText: 'Validate the probe')),
          const SizedBox(height: Sp.m),
          TextField(controller: do_, minLines: 2, maxLines: 5,
              decoration: const InputDecoration(hintText: 'Run terra probe validate <id>; fix INPUT/EXECUTE/OUTPUT before any run.')),
        ],
      ),
      actions: (ctx) => Row(
        children: [
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('ADD')),
          const SizedBox(width: Sp.m),
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('CANCEL')),
        ],
      ),
    );
    final t = title.text.trim(), d = do_.text.trim();
    title.dispose();
    do_.dispose();
    if (ok == true && t.isNotEmpty && d.isNotEmpty) manager.addStep(p.id, t, d);
  }
}

class _Provenance extends StatelessWidget {
  const _Provenance({required this.p, this.decay = false});
  final Procedure p;
  final bool decay;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final k = opsKeyStyle(context);
    final v = AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurface);
    final m = p.mintedBy;
    return Wrap(
      spacing: Sp.xl,
      runSpacing: 4,
      children: [
        Text.rich(TextSpan(children: [
          TextSpan(text: 'MINTED  ', style: k),
          TextSpan(
            text: m == null
                ? 'not by a run on this machine'
                : '${m.taskTitle} · ${m.workOrder}${m.at != null ? ' · ${whenLabel(m.at!)}' : ''}',
            style: v,
          ),
        ])),
        Text.rich(TextSpan(children: [
          TextSpan(text: 'USED  ', style: k),
          TextSpan(text: p.usedBy.isEmpty ? 'not yet' : '${p.usedBy.length}× — last ${p.usedBy.last.taskTitle} · ${p.usedBy.last.workOrder}', style: v),
        ])),
        Text.rich(TextSpan(children: [
          TextSpan(text: 'CHANGED  ', style: k),
          TextSpan(text: whenLabel(p.updatedAt), style: v),
        ])),
        if (decay || p.standing.pinned || p.standing.protected)
          Text.rich(TextSpan(children: [
            TextSpan(text: 'STANDING  ', style: k),
            TextSpan(text: _standing(), style: v),
          ])),
      ],
    );
  }

  /// What the ledger will do with it, in a phrase.
  String _standing() {
    final s = p.standing;
    if (s.pinned) return 'pinned: never decays';
    if (s.protected) return 'core: never decays';
    if (s.retired) return 'decayed';
    final last = s.touched.entries.toList()..sort((a, b) => b.value.compareTo(a.value));
    final touch = last.isEmpty ? 'never touched' : 'last ${last.first.key} at gate ${last.first.value}';
    return '${s.graceLeft ?? '?'} green gate${s.graceLeft == 1 ? '' : 's'} before it decays · $touch';
  }
}

class _Step extends StatelessWidget {
  const _Step({super.key, required this.p, required this.i, required this.step, required this.manager, required this.resetKey});
  final Procedure p;
  final int i;
  final ProcedureStep step;
  final ProceduresManager manager;
  final Object resetKey;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    return Container(
      padding: const EdgeInsets.symmetric(vertical: Sp.m),
      decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outlineVariant))),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 36,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                OpsIndex(i),
                ReorderableDragStartListener(
                  index: i,
                  child: MouseRegion(
                    cursor: SystemMouseCursors.grab,
                    child: Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Icon(Icons.drag_handle, size: 16, color: cs.outline),
                    ),
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                InlineText(
                  value: step.title,
                  resetKey: resetKey,
                  compact: true,
                  style: theme.textTheme.titleMedium!.copyWith(color: ink, fontWeight: FontWeight.w700),
                  placeholder: 'Step title',
                  onChanged: (_) {},
                  onCommit: (v) => manager.editStep(p.id, step.title, rename: v),
                ),
                const SizedBox(height: 4),
                InlineText(
                  value: step.do_,
                  resetKey: resetKey,
                  style: theme.textTheme.bodyLarge!.copyWith(color: ink, height: 1.5),
                  placeholder: 'Do this.',
                  onChanged: (_) {},
                  onCommit: (v) => manager.editStep(p.id, step.title, do_: v),
                ),
                const SizedBox(height: 6),
                _LinkLine(p: p, step: step, manager: manager),
              ],
            ),
          ),
          OpsRemove(
            tooltip: 'Remove step',
            onPressed: () async {
              final yes = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: Text('REMOVE STEP', style: opsHeadingStyle(ctx)),
                  content: Text('"${step.title}" goes from the procedure, in the library, now.'),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('KEEP')),
                    TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('REMOVE')),
                  ],
                ),
              );
              if (yes == true) manager.removeStep(p.id, step.title);
            },
          ),
        ],
      ),
    );
  }
}

/// Under a step: the procedure it runs, if any. Click the name to open
/// it; × to unlink; or LINK A PROCEDURE to pick one.
class _LinkLine extends StatelessWidget {
  const _LinkLine({required this.p, required this.step, required this.manager});
  final Procedure p;
  final ProcedureStep step;
  final ProceduresManager manager;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final linked = step.procedure;
    final mono = AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant);
    if (linked != null && linked.isNotEmpty) {
      return Row(
        children: [
          Text('RUNS  ', style: opsKeyStyle(context)),
          InkWell(
            onTap: () => manager.openById(linked),
            child: Text(linked, style: mono.copyWith(color: AppTheme.ink(context), decoration: TextDecoration.underline)),
          ),
          OpsRemove(onPressed: () => manager.linkStep(p.id, step.title, null), tooltip: 'Unlink'),
        ],
      );
    }
    return InkWell(
      onTap: () => _pick(context),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Text('+ LINK A PROCEDURE', style: opsKeyStyle(context)),
      ),
    );
  }

  Future<void> _pick(BuildContext context) async {
    final chosen = await showDialog<String>(
      context: context,
      barrierColor: Colors.black.withValues(alpha: 0.6),
      builder: (ctx) => _Picker(candidates: manager.all.where((q) => q.id != p.id).toList()),
    );
    if (chosen != null) manager.linkStep(p.id, step.title, chosen);
  }
}

/// Pick a procedure to link: search, then a short list.
class _Picker extends StatefulWidget {
  const _Picker({required this.candidates});
  final List<Procedure> candidates;

  @override
  State<_Picker> createState() => _PickerState();
}

class _PickerState extends State<_Picker> {
  String q = '';

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final needle = q.trim().toLowerCase();
    final hits = widget.candidates
        .where((p) => needle.isEmpty || p.id.contains(needle) || p.title.toLowerCase().contains(needle) || p.tags.any((t) => t.contains(needle)))
        .take(12)
        .toList();
    return Dialog(
      backgroundColor: Colors.transparent,
      child: OpsSheet(
        maxWidth: 560,
        padding: const EdgeInsets.fromLTRB(36, 32, 36, 28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('LINK A PROCEDURE', style: opsLabelStyle(context).copyWith(color: cs.primary)),
            const SizedBox(height: 12),
            Text('Walking this step will mean opening the one you pick.',
                style: theme.textTheme.bodyLarge!.copyWith(color: cs.onSurfaceVariant)),
            const SizedBox(height: 16),
            TextField(
              autofocus: true,
              onChanged: (v) => setState(() => q = v),
              style: AppTheme.mono.copyWith(fontSize: 13),
              decoration: const InputDecoration(isDense: true, hintText: 'find a procedure'),
            ),
            const SizedBox(height: 12),
            ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 320),
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final p in hits)
                    InkWell(
                      onTap: () => Navigator.pop(context, p.id),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(p.id, style: AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant)),
                            Text(p.title, style: theme.textTheme.bodyMedium!.copyWith(color: AppTheme.ink(context))),
                          ],
                        ),
                      ),
                    ),
                  if (hits.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text('No procedure matches.', style: opsKeyStyle(context)),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            Row(children: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('CANCEL'))]),
          ],
        ),
      ),
    );
  }
}

/// Tags as chips: × on each to remove, + to add one. No free-text line
/// to mis-separate. The last tag cannot be removed — the playbook needs
/// one to search by.
class _Tags extends StatefulWidget {
  const _Tags({required this.p, required this.manager, required this.resetKey});
  final Procedure p;
  final ProceduresManager manager;
  final Object resetKey;

  @override
  State<_Tags> createState() => _TagsState();
}

class _TagsState extends State<_Tags> {
  bool adding = false;
  final _new = TextEditingController();

  @override
  void dispose() {
    _new.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    final t = _new.text.trim().toLowerCase().replaceAll(RegExp(r'[,\s;]+'), '-');
    setState(() => adding = false);
    _new.clear();
    if (t.isEmpty || widget.p.tags.contains(t)) return;
    widget.manager.retag(widget.p.id, [...widget.p.tags, t]);
  }

  Future<void> _rename(String from, String to) async {
    final t = to.trim().toLowerCase().replaceAll(RegExp(r'[,\s;]+'), '-');
    if (t.isEmpty || t == from) return;
    final tags = [for (final x in widget.p.tags) x == from ? t : x];
    widget.manager.retag(widget.p.id, {...tags}.toList());
  }

  Future<void> _remove(String t) async {
    if (widget.p.tags.length <= 1) return;
    widget.manager.retag(widget.p.id, widget.p.tags.where((x) => x != t).toList());
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    final p = widget.p;
    final last = p.tags.length <= 1;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Text('TAGS', style: opsKeyStyle(context)),
        const SizedBox(width: Sp.m),
        Expanded(
          child: Wrap(
            spacing: 6,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              for (final t in p.tags)
                _Chip(
                  text: t,
                  onRemove: last ? null : () => _remove(t),
                  onRename: (v) => _rename(t, v),
                ),
              if (adding)
                SizedBox(
                  width: 160,
                  height: 24,
                  child: TextField(
                    controller: _new,
                    autofocus: true,
                    style: AppTheme.mono.copyWith(fontSize: 12, color: ink),
                    decoration: const InputDecoration(
                      isDense: true,
                      contentPadding: EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                      hintText: 'one tag',
                      border: OutlineInputBorder(),
                    ),
                    onSubmitted: (_) => _add(),
                    onTapOutside: (_) => setState(() => adding = false),
                  ),
                )
              else
                InkWell(
                  onTap: () => setState(() => adding = true),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                    child: Text('+', style: AppTheme.mono.copyWith(fontSize: 14, color: cs.onSurfaceVariant)),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.only(left: Sp.s),
                child: Text('double-click a tag to rename', style: opsKeyStyle(context)),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// A tag chip whose × appears only under the pointer; the chip keeps its
/// width so the row does not shuffle on hover.
class _Chip extends StatefulWidget {
  const _Chip({required this.text, required this.onRemove, required this.onRename});
  final String text;
  final VoidCallback? onRemove;
  final ValueChanged<String> onRename;

  @override
  State<_Chip> createState() => _ChipState();
}

class _ChipState extends State<_Chip> {
  bool hover = false;
  bool editing = false;
  late final _c = TextEditingController(text: widget.text);
  final _focus = FocusNode();

  @override
  void dispose() {
    _c.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _done() {
    setState(() => editing = false);
    widget.onRename(_c.text);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    final removable = widget.onRemove != null;
    final style = AppTheme.mono.copyWith(fontSize: 12, color: ink);
    if (editing) {
      // Double-clicked: the same box, now a field, sized to its text as
      // it is typed so it never reads narrower than the chip did.
      return ListenableBuilder(
        listenable: _c,
        builder: (context, _) {
          final painter = TextPainter(
            text: TextSpan(text: _c.text.isEmpty ? widget.text : _c.text, style: style),
            textDirection: TextDirection.ltr,
          )..layout();
          return Container(
            width: painter.width + 22,
            height: 22,
            decoration: BoxDecoration(
              border: Border.all(color: ink),
              borderRadius: BorderRadius.circular(2),
            ),
            alignment: Alignment.centerLeft,
            child: EditableText(
              controller: _c,
              focusNode: _focus,
              autofocus: true,
              style: style,
              cursorColor: ink,
              backgroundCursorColor: cs.surface,
              maxLines: 1,
              onSubmitted: (_) => _done(),
              onTapOutside: (_) => _done(),
            ),
          );
        },
      );
    }
    return Tooltip(
      message: 'Double-click to rename',
      waitDuration: const Duration(milliseconds: 600),
      child: GestureDetector(
      onDoubleTap: () => setState(() {
        _c.text = widget.text;
        editing = true;
      }),
      child: MouseRegion(
      cursor: SystemMouseCursors.text,
      onEnter: (_) => setState(() => hover = true),
      onExit: (_) => setState(() => hover = false),
      child: Container(
        height: 22,
        padding: const EdgeInsets.fromLTRB(7, 0, 4, 0),
        decoration: BoxDecoration(
          border: Border.all(color: hover ? cs.onSurfaceVariant : cs.outline),
          borderRadius: BorderRadius.circular(2),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(widget.text, style: style),
            // The × slides in under the pointer; the chip is text-wide otherwise.
            AnimatedSize(
              duration: const Duration(milliseconds: 110),
              curve: Curves.easeOut,
              child: removable && hover
                  ? InkWell(
                      onTap: widget.onRemove,
                      child: Padding(
                        padding: const EdgeInsets.only(left: 4),
                        child: Icon(Icons.close, size: 12, color: cs.onSurfaceVariant),
                      ),
                    )
                  : const SizedBox(width: 3),
            ),
          ],
        ),
      ),
      ),
      ),
    );
  }
}

/// Every version of this procedure: one line per commit, newest first —
/// a worker's work order or a person's save. Click one to read its
/// change; RESTORE puts that version back as a new commit.
class _History extends StatelessWidget {
  const _History({required this.manager});
  final ProceduresManager manager;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final versions = manager.history;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OpsLabel('HISTORY', trailing: Text('${versions.length}', style: opsKeyStyle(context))),
        const SizedBox(height: Sp.s),
        if (versions.isEmpty)
          Text('No versions yet — the first save starts the record.',
              style: theme.textTheme.bodyMedium!.copyWith(color: cs.onSurfaceVariant))
        else
          for (final (i, v) in versions.indexed) ...[
            InkWell(
              onTap: () => manager.view(v),
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: Sp.s),
                decoration: BoxDecoration(
                  color: manager.viewing?.sha == v.sha ? cs.surfaceContainer : null,
                  border: Border(bottom: BorderSide(color: cs.outlineVariant)),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.baseline,
                  textBaseline: TextBaseline.alphabetic,
                  children: [
                    SizedBox(width: 64, child: Text(v.short, style: AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant))),
                    Expanded(
                      child: Text(v.message, style: theme.textTheme.bodyMedium!.copyWith(color: i == 0 ? ink : cs.onSurface)),
                    ),
                    const SizedBox(width: Sp.m),
                    Text(v.author.split(' <').first, style: opsKeyStyle(context)),
                    const SizedBox(width: Sp.m),
                    Text(whenLabel(v.at), style: opsKeyStyle(context)),
                  ],
                ),
              ),
            ),
            if (manager.viewing?.sha == v.sha) _DiffView(lines: manager.diff, isCurrent: i == 0, onRestore: () => manager.restore(v)),
          ],
      ],
    );
  }
}

class _DiffView extends StatelessWidget {
  const _DiffView({required this.lines, required this.isCurrent, required this.onRestore});
  final List<DiffHunkLine> lines;
  final bool isCurrent;
  final VoidCallback onRestore;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final mono = AppTheme.mono.copyWith(fontSize: 12, height: 1.45);
    return Container(
      margin: const EdgeInsets.fromLTRB(0, Sp.s, 0, Sp.l),
      padding: const EdgeInsets.fromLTRB(Sp.m, Sp.s, Sp.m, Sp.s),
      decoration: BoxDecoration(
        color: cs.surfaceContainer,
        border: Border(left: BorderSide(color: cs.outline, width: 2)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (lines.isEmpty)
            Text('No textual change recorded.', style: opsKeyStyle(context))
          else
            for (final l in lines.take(400))
              Text(
                switch (l.kind) { 'added' => '+ ', 'removed' => '- ', 'hunk' => '', _ => '  ' } + l.text,
                style: mono.copyWith(
                  color: switch (l.kind) {
                    'added' => AppTheme.added(context),
                    'removed' => AppTheme.removed(context),
                    'hunk' => cs.onSurfaceVariant,
                    _ => cs.onSurface,
                  },
                  fontWeight: l.kind == 'hunk' ? FontWeight.w700 : null,
                ),
              ),
          if (lines.length > 400) Text('… ${lines.length - 400} more lines', style: opsKeyStyle(context)),
          if (!isCurrent) ...[
            const SizedBox(height: Sp.m),
            Row(
              children: [
                OutlinedButton(onPressed: onRestore, child: const Text('RESTORE THIS VERSION')),
                const SizedBox(width: Sp.m),
                Text('as a new commit; nothing is rewritten', style: opsKeyStyle(context)),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
