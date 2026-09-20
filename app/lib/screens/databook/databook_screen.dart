import 'package:flutter/material.dart';

import '../../cg/layered_ledger_graph/layered_ledger_graph.dart';
import '../../models/databook.dart';
import '../../state/databook_manager.dart';
import '../../theme/app_theme.dart';
import '../../theme/kit_styles.dart';

/// The data book: the brief's needs and deliverables in order, each with
/// the value the map holds for it. A row unfolds into its test report —
/// claim, method, runs, agreement. Read like a lab notebook, not a grid.
class DataBookScreen extends StatelessWidget {
  const DataBookScreen({super.key, required this.manager});
  final DataBookManager manager;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) {
        final book = manager.book;
        if (manager.loading || book.entries.isEmpty) {
          return Padding(
            padding: const EdgeInsets.fromLTRB(32, 28, 32, 0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('DATA BOOK', style: opsHeadingStyle(context)),
                const SizedBox(height: Sp.m),
                Text(
                  manager.loading
                      ? 'Opening the map…'
                      : 'The brief names nothing yet; the data book has no rows.',
                  style: Theme.of(context).textTheme.bodyLarge!.copyWith(
                    color: cs.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          );
        }
        return SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(40, 28, 40, 48),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: AppTheme.contentWidth),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      Text('DATA BOOK', style: opsHeadingStyle(context)),
                      const SizedBox(width: Sp.xl),
                      _ViewToggle(
                        view: manager.view,
                        onChanged: manager.setView,
                      ),
                      const Spacer(),
                      Text(
                        manager.view == 'map'
                            ? '${book.maps.length} MAPS · ${book.maps.isEmpty ? 0 : book.maps.first.knowns.length} GLOBAL KNOWNS'
                            : book.verdict,
                        style: opsLabelStyle(context).copyWith(
                          color: manager.view == 'map'
                              ? null
                              : book.failed > 0
                              ? AppTheme.removed(context)
                              : book.answered != book.entries.length
                              ? cs.primary
                              : null,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: Sp.xl),
                  if (manager.view == 'map') ...[
                    const _ColumnHeads(),
                    for (final m in book.maps) _MapSection(node: m),
                  ] else ...[
                    _Graph(
                      book: book,
                      selected: manager.selectedNode,
                      onSelect: manager.selectNode,
                    ),
                    if (manager.selectedEntry(book) case final e?) ...[
                      const SizedBox(height: Sp.l),
                      _Row(entry: e, open: true, onTap: () => manager.selectNode(null)),
                    ],
                  ],
                  if (manager.view != 'map' && book.orphans.isNotEmpty) ...[
                    const _GroupHead('KNOWN, UNCITED'),
                    for (final k in book.orphans)
                      _Cells(
                        index: '',
                        item: Text(
                          k.claim,
                          style: theme.textTheme.bodyMedium!.copyWith(
                            color: cs.onSurfaceVariant,
                          ),
                        ),
                        known: k,
                        dim: true,
                      ),
                  ],
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// Column widths, shared by the head and every row so the rules line up.
const _wIndex = 36.0, _wValue = 190.0, _wN = 48.0, _wConf = 56.0;

/// BY BRIEF · BY MAP, the selected one underlined in the accent.
class _ViewToggle extends StatelessWidget {
  const _ViewToggle({required this.view, required this.onChanged});
  final String view;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    Widget one(String v, String label) {
      final on = view == v;
      return InkWell(
        onTap: () => onChanged(v),
        child: Container(
          padding: const EdgeInsets.fromLTRB(2, 6, 2, 4),
          margin: const EdgeInsets.only(right: Sp.l),
          decoration: BoxDecoration(
            border: Border(
              bottom: BorderSide(
                color: on ? AppTheme.ink(context) : Colors.transparent,
                width: 2,
              ),
            ),
          ),
          child: Text(
            label,
            style: opsLabelStyle(context).copyWith(
              color: on ? cs.onSurface : null,
            ),
          ),
        ),
      );
    }
    return Row(children: [one('graph', 'THREADS'), one('map', 'BY MAP')]);
  }
}

/// One map of the tree: its heading line (id, purpose, counts), then its
/// knowns in the book's columns. In a task map, LOCAL marks a known that
/// never went up; in global, the item cell says which map it came from.
class _MapSection extends StatelessWidget {
  const _MapSection({required this.node});
  final MapNode node;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final m = node;
    final isGlobal = m.kind == 'global';
    final local = m.knowns.length - m.adopted;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: EdgeInsets.fromLTRB(isGlobal ? 0 : _wIndex, Sp.l, 0, 2),
          child: Row(
            children: [
              if (!isGlobal)
                Padding(
                  padding: const EdgeInsets.only(right: Sp.s),
                  child: Text('└', style: AppTheme.mono.copyWith(color: cs.outline)),
                ),
              Text(m.id.toUpperCase(), style: opsLabelStyle(context)),
              const SizedBox(width: Sp.m),
              Expanded(
                child: Text(
                  m.purpose,
                  overflow: TextOverflow.ellipsis,
                  style: opsKeyStyle(context),
                ),
              ),
              Text(
                isGlobal
                    ? '${m.knowns.length} knowns · ${m.runs} runs'
                          '${m.openUnknowns > 0 ? ' · ${m.openUnknowns} open' : ''}'
                    : '${m.adopted} of ${m.knowns.length} adopted · ${m.runs} runs'
                          '${m.openUnknowns > 0 ? ' · ${m.openUnknowns} open' : ''}',
                style: opsKeyStyle(context).copyWith(
                  color: local > 0 || m.openUnknowns > 0 ? cs.primary : null,
                ),
              ),
            ],
          ),
        ),
        for (final k in m.knowns)
          _Cells(
            index: '',
            item: Padding(
              padding: EdgeInsets.only(left: isGlobal ? 0 : _wIndex),
              child: Row(
                children: [
                  Text(
                    k.report.id,
                    style: AppTheme.mono.copyWith(
                      fontSize: 13,
                      color: k.adopted ? AppTheme.ink(context) : cs.onSurfaceVariant,
                    ),
                  ),
                  const SizedBox(width: Sp.m),
                  if (isGlobal && k.from != null)
                    Text('← ${k.from}', style: opsKeyStyle(context))
                  else if (!isGlobal)
                    _Stamp(k.adopted ? 'ADOPTED' : 'LOCAL', hot: !k.adopted),
                ],
              ),
            ),
            known: k.report,
            dim: !k.adopted,
          ),
      ],
    );
  }
}

class _ColumnHeads extends StatelessWidget {
  const _ColumnHeads();

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final k = opsKeyStyle(context);
    Widget h(String t, double w, {TextAlign a = TextAlign.left}) =>
        SizedBox(width: w, child: Text(t, style: k, textAlign: a));
    return Container(
      padding: const EdgeInsets.only(bottom: 6),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: cs.outline, width: 1.5)),
      ),
      child: Row(
        children: [
          h('#', _wIndex),
          Expanded(child: Text('ITEM', style: k)),
          h('VALUE', _wValue, a: TextAlign.right),
          const SizedBox(width: Sp.m),
          h('N', _wN, a: TextAlign.right),
          const SizedBox(width: Sp.m),
          h('CONF', _wConf),
        ],
      ),
    );
  }
}

class _GroupHead extends StatelessWidget {
  const _GroupHead(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(0, Sp.l, 0, 2),
    child: Text(text, style: opsLabelStyle(context)),
  );
}

/// One ruled line of the book. A row with several knowns stacks them in
/// the same cells; a row with none shows its stamp across the value cells.
class _Cells extends StatelessWidget {
  const _Cells({
    required this.index,
    required this.item,
    this.known,
    this.stamp,
    this.dim = false,
    this.onTap,
  });
  final String index;
  final Widget item;
  final KnownReport? known;
  final Widget? stamp;
  final bool dim;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final k = known;
    final mono = AppTheme.mono.copyWith(
      fontSize: 13,
      color: dim ? cs.onSurfaceVariant : cs.onSurface,
    );
    final dimMono = mono.copyWith(color: cs.onSurfaceVariant);
    Widget c(String t, double w, {TextStyle? s, TextAlign a = TextAlign.left}) =>
        SizedBox(width: w, child: Text(t, style: s ?? dimMono, textAlign: a, overflow: TextOverflow.ellipsis));
    return InkWell(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 9),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(width: _wIndex, child: Text(index, style: dimMono)),
            Expanded(child: item),
            if (k != null) ...[
              // A false boolean is the row a person acts on: the artifact
              // exists and fails its need.
              // The state of the number: value (with its unit), how many
              // readings, how sure. Method and runs live in the report.
              c(k.unit.isEmpty ? k.value : '${k.value} ${k.unit}', _wValue,
                  s: mono.copyWith(
                    fontWeight: FontWeight.w700,
                    fontSize: 14,
                    color: k.type == 'boolean' && k.value == 'false'
                        ? AppTheme.removed(context)
                        : null,
                  ),
                  a: TextAlign.right),
              const SizedBox(width: Sp.m),
              c('n ${k.n}', _wN, a: TextAlign.right),
              const SizedBox(width: Sp.m),
              c(k.confidence, _wConf),
            ] else if (stamp != null)
              SizedBox(
                width: _wValue + Sp.m + _wN + Sp.m + _wConf,
                child: Align(alignment: Alignment.centerLeft, child: stamp),
              ),
          ],
        ),
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.entry, required this.open, required this.onTap});
  final DataBookEntry entry;
  final bool open;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final e = entry;
    final item = Text(
      e.text,
      style: theme.textTheme.bodyMedium!.copyWith(
        fontSize: 14.5,
        color: AppTheme.ink(context),
        height: 1.4,
      ),
    );
    final blank = Text('', style: theme.textTheme.bodyMedium);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (e.answers.isNotEmpty)
          for (final (i, k) in e.answers.indexed)
            _Cells(
              index: i == 0 ? '${e.index}' : '',
              item: i == 0 ? item : blank,
              known: k,
              onTap: onTap,
            )
        else
          _Cells(
            index: '${e.index}',
            item: item,
            stamp: e.state == 'open'
                ? _Stamp('OPEN · ${e.open.map((u) => u.id).join(', ')}', hot: true)
                : const _Stamp('UNCOVERED', hot: false),
            onTap: onTap,
          ),
        if (open) _Report(entry: e),
      ],
    );
  }
}

class _Stamp extends StatelessWidget {
  const _Stamp(this.text, {required this.hot});
  final String text;
  final bool hot;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final color = hot ? cs.primary : cs.onSurfaceVariant;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        border: Border.all(color: color, width: 1.2),
        borderRadius: BorderRadius.circular(2),
      ),
      child: Text(
        text,
        style: AppTheme.mono.copyWith(
          fontSize: 11,
          letterSpacing: 1.2,
          fontWeight: FontWeight.w600,
          color: color,
        ),
      ),
    );
  }
}

/// The test report under a row: what was claimed, how it was measured,
/// which runs, and what is still owed.
class _Report extends StatelessWidget {
  const _Report({required this.entry});
  final DataBookEntry entry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final body = theme.textTheme.bodyMedium!.copyWith(
      color: cs.onSurface,
      height: 1.5,
    );
    final mono = AppTheme.mono.copyWith(fontSize: 12.5, color: cs.onSurfaceVariant);
    Widget line(String k, String v, {bool m = false}) => Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.baseline,
        textBaseline: TextBaseline.alphabetic,
        children: [
          SizedBox(width: 120, child: Text(k.toUpperCase(), style: opsKeyStyle(context))),
          Expanded(child: Text(v, style: m ? mono : body)),
        ],
      ),
    );
    return Container(
      margin: const EdgeInsets.only(left: 36),
      padding: const EdgeInsets.fromLTRB(Sp.l, Sp.m, Sp.l, Sp.s),
      decoration: BoxDecoration(
        color: cs.surfaceContainer,
        border: Border(left: BorderSide(color: cs.outline, width: 2)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final k in entry.answers) ...[
            Text(k.id, style: AppTheme.mono.copyWith(fontSize: 13, color: AppTheme.ink(context), fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            line('Claim', k.claim),
            line('Method', k.probes.isEmpty ? '—' : k.probes.join(', '), m: true),
            line(
              'Readings',
              '${k.runs.length} run(s)'
                  '${k.agreement != null ? ' · agreement ${(k.agreement! * 100).round()}%' : ''}'
                  '${k.methods > 0 ? ' · ${k.methods} method(s)' : ''}',
            ),
            line('Runs', k.runs.join('\n'), m: true),
            if (k.adoptedFrom != null) line('Adopted from', k.adoptedFrom!, m: true),
            const SizedBox(height: Sp.s),
          ],
          for (final u in entry.open) ...[
            Text(u.id, style: AppTheme.mono.copyWith(fontSize: 13, color: cs.primary, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            line('Claim', u.claim),
            line('Evidence needed', u.evidenceNeeded),
            line('Status', u.status, m: true),
            const SizedBox(height: Sp.s),
          ],
          if (entry.answers.isEmpty && entry.open.isEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: Sp.s),
              child: Text('No unknown cites this entry yet.', style: body),
            ),
        ],
      ),
    );
  }
}


/// The map as threads: brief entries on the left, the readings that
/// answer them in the middle, the work orders (task maps) that took them
/// on the right. Colour is state — met, false, open, uncovered — and the
/// value sits on the reading. Tap anything to follow its threads.
class _Graph extends StatelessWidget {
  const _Graph({required this.book, required this.selected, required this.onSelect});
  final DataBook book;
  final String? selected;
  final ValueChanged<String?> onSelect;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    const met = 0, failed = 1, open = 2, uncovered = 3, order = 4;
    final nodes = <LedgerNode>[];
    final edges = <LedgerEdge>[];
    final seenKnown = <String>{};
    for (final e in book.entries) {
      final id = '${e.kind}:${e.index}';
      final tone = switch (e.state) { 'false' => failed, 'answered' => met, 'open' => open, _ => uncovered };
      nodes.add(LedgerNode(
        id: id,
        layer: 0,
        label: '${e.kind == 'need' ? 'N' : 'D'}${e.index}  ${e.text}',
        tone: tone,
        weight: 1.4,
      ));
      for (final k in e.answers) {
        if (seenKnown.add(k.id)) {
          final bad = k.type == 'boolean' && k.value == 'false';
          nodes.add(LedgerNode(
            id: 'k:${k.id}',
            layer: 1,
            label: k.id,
            detail: '${k.unit.isEmpty ? k.value : '${k.value} ${k.unit}'} · n ${k.n} · ${k.confidence}',
            tone: bad ? failed : met,
          ));
          if (k.adoptedFrom != null) {
            nodes.add(LedgerNode(id: 'm:${k.adoptedFrom}', layer: 2, label: k.adoptedFrom!, tone: order));
            edges.add(LedgerEdge(from: 'k:${k.id}', to: 'm:${k.adoptedFrom}', tone: bad ? failed : 0));
          }
        }
        edges.add(LedgerEdge(from: id, to: 'k:${k.id}', tone: k.type == 'boolean' && k.value == 'false' ? failed : 0));
      }
      for (final u in e.open) {
        nodes.add(LedgerNode(id: 'u:${u.id}', layer: 1, label: u.id, detail: u.status.toUpperCase(), tone: open));
        edges.add(LedgerEdge(from: id, to: 'u:${u.id}', tone: open, dashed: true));
      }
    }
    // One node per task map, whichever known first named it.
    final unique = <String, LedgerNode>{};
    for (final n in nodes) {
      unique.putIfAbsent(n.id, () => n);
    }
    final style = LedgerGraphStyle(
      tones: [ink, AppTheme.removed(context), cs.primary, cs.outline, cs.onSurfaceVariant],
      labelStyle: theme.textTheme.bodyMedium!.copyWith(fontSize: 13, color: ink, height: 1.25),
      detailStyle: AppTheme.mono.copyWith(fontSize: 11.5, color: cs.onSurfaceVariant),
      headingStyle: opsLabelStyle(context),
      edgeColor: cs.outline,
      nodeFill: cs.surface,
      nodeBorder: cs.outlineVariant,
      nodeWidth: 250,
      layerGap: 70,
    );
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: LayeredLedgerGraph(
        nodes: unique.values.toList(),
        edges: edges,
        headings: const ['BRIEF', 'READINGS', 'WORK ORDERS'],
        style: style,
        selected: selected,
        onSelect: onSelect,
      ),
    );
  }
}
