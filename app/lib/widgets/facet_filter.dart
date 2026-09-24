import 'package:flutter/material.dart';

import '../engine/desk.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import 'ops.dart';

/// One column to filter by: its name and the values in it with counts.
class Facet {
  const Facet({required this.key, required this.label, required this.values, this.paths = false});
  final String key;
  final String label;

  /// value → how many rows carry it.
  final Map<String, int> values;

  /// Values are filesystem paths: shown with `~` for home and the middle
  /// elided, since the end of a path is the part that tells you where.
  final bool paths;
}

/// A path for reading in a narrow place: `~` for home, then the middle
/// dropped so both the root and the leaf survive. The full path belongs
/// in a tooltip beside it.
String readablePath(String path, {int max = 44}) {
  var p = Desk.short(path);
  if (p.length <= max) return p;
  final tail = (max * 0.65).floor();
  final head = max - tail - 1;
  return '${p.substring(0, head)}…${p.substring(p.length - tail)}';
}

/// A spreadsheet-style column filter. The button is a funnel, filled when
/// anything is selected; it opens a sheet with every facet as a checklist
/// — search within the values, tick what to keep, All/None per facet.
/// An empty selection for a facet means "everything", so the list never
/// filters itself to nothing by accident.
class FacetFilterButton extends StatelessWidget {
  const FacetFilterButton({
    super.key,
    required this.facets,
    required this.selected,
    required this.onChanged,
  });
  final List<Facet> facets;

  /// facet key → chosen values (empty = all).
  final Map<String, Set<String>> selected;
  final ValueChanged<Map<String, Set<String>>> onChanged;

  bool get active => selected.values.any((s) => s.isNotEmpty);

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final ink = AppTheme.ink(context);
    return Tooltip(
      message: 'Filter',
      child: InkWell(
        onTap: () async {
          final result = await showDialog<Map<String, Set<String>>>(
            context: context,
            barrierColor: Colors.black.withValues(alpha: 0.7),
            builder: (_) => _FacetSheet(facets: facets, initial: selected),
          );
          if (result != null) onChanged(result);
        },
        child: Container(
          height: 32,
          padding: const EdgeInsets.symmetric(horizontal: 8),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(AppTheme.radius),
            color: active ? ink.withValues(alpha: 0.10) : null,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(active ? Icons.filter_alt : Icons.filter_alt_outlined, size: 18, color: active ? ink : cs.onSurfaceVariant),
              if (active) ...[
                const SizedBox(width: 6),
                Text(
                  '${selected.values.fold(0, (a, s) => a + s.length)}',
                  style: AppTheme.mono.copyWith(fontSize: 12, fontWeight: FontWeight.w700, color: ink),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _FacetSheet extends StatefulWidget {
  const _FacetSheet({required this.facets, required this.initial});
  final List<Facet> facets;
  final Map<String, Set<String>> initial;

  @override
  State<_FacetSheet> createState() => _FacetSheetState();
}

class _FacetSheetState extends State<_FacetSheet> {
  late final Map<String, Set<String>> picked = {
    for (final f in widget.facets) f.key: {...(widget.initial[f.key] ?? const <String>{})},
  };
  final Map<String, String> search = {};
  final Set<String> hideOnes = {};
  late String open = widget.facets.first.key;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final facet = widget.facets.firstWhere((f) => f.key == open);
    final q = (search[open] ?? '').toLowerCase();
    final sel = picked[open]!;
    final entries = facet.values.entries
        .where((e) => q.isEmpty || e.key.toLowerCase().contains(q))
        .where((e) => !hideOnes.contains(open) || e.value > 1 || sel.contains(e.key))
        .toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    final singles = facet.values.values.where((n) => n == 1).length;
    // Most of the window: this is a working surface, not a prompt.
    final size = MediaQuery.sizeOf(context);
    return Dialog(
      backgroundColor: Colors.transparent,
      insetPadding: const EdgeInsets.all(40),
      child: OpsSheet(
        maxWidth: size.width - 80,
        padding: const EdgeInsets.fromLTRB(36, 28, 36, 24),
        child: SizedBox(
          height: size.height - 80 - 52,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Text('FILTER', style: opsLabelStyle(context).copyWith(color: cs.primary)),
                  const Spacer(),
                  TextButton(
                    onPressed: () => setState(() {
                      for (final s in picked.values) {
                        s.clear();
                      }
                    }),
                    child: Text('CLEAR ALL', style: opsLabelStyle(context)),
                  ),
                ],
              ),
              const SizedBox(height: Sp.m),
              Expanded(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // Columns down the left.
                    SizedBox(
                      width: 240,
                      child: ListView(
                        children: [
                          for (final f in widget.facets)
                            InkWell(
                              onTap: () => setState(() => open = f.key),
                              child: Container(
                                padding: const EdgeInsets.symmetric(horizontal: Sp.m, vertical: Sp.s),
                                decoration: BoxDecoration(
                                  color: open == f.key ? cs.surfaceContainerHigh : null,
                                  border: Border(left: BorderSide(color: open == f.key ? ink : Colors.transparent, width: 3)),
                                ),
                                child: Row(
                                  children: [
                                    Expanded(
                                      child: Text(
                                        f.label.toUpperCase(),
                                        style: opsLabelStyle(context).copyWith(color: open == f.key ? ink : null),
                                      ),
                                    ),
                                    if (picked[f.key]!.isNotEmpty)
                                      Text(
                                        '${picked[f.key]!.length}',
                                        style: AppTheme.mono.copyWith(fontSize: 12, fontWeight: FontWeight.w700, color: ink),
                                      ),
                                  ],
                                ),
                              ),
                            ),
                        ],
                      ),
                    ),
                    VerticalDivider(width: Sp.l, color: cs.outlineVariant),
                    // Values with checkboxes on the right.
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          SizedBox(
                            height: 32,
                            child: TextField(
                              onChanged: (v) => setState(() => search[open] = v),
                              style: AppTheme.mono.copyWith(fontSize: 13),
                              decoration: InputDecoration(
                                isDense: true,
                                contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                                prefixIcon: Icon(Icons.search, size: 16, color: cs.onSurfaceVariant),
                                prefixIconConstraints: const BoxConstraints(minWidth: 30),
                                hintText: 'search ${facet.label.toLowerCase()}',
                                enabledBorder: OutlineInputBorder(borderSide: BorderSide(color: cs.outlineVariant)),
                                focusedBorder: OutlineInputBorder(borderSide: BorderSide(color: cs.outline)),
                              ),
                            ),
                          ),
                          const SizedBox(height: Sp.s),
                          Row(
                            children: [
                              InkWell(
                                onTap: () => setState(() => sel.addAll(entries.map((e) => e.key))),
                                child: Text('ALL', style: opsKeyStyle(context)),
                              ),
                              const SizedBox(width: Sp.m),
                              InkWell(
                                onTap: () => setState(() => sel.clear()),
                                child: Text('NONE', style: opsKeyStyle(context)),
                              ),
                              if (singles > 0) ...[
                                const SizedBox(width: Sp.l),
                                InkWell(
                                  onTap: () => setState(() => hideOnes.contains(open) ? hideOnes.remove(open) : hideOnes.add(open)),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Icon(
                                        hideOnes.contains(open) ? Icons.check_box : Icons.check_box_outline_blank,
                                        size: 14,
                                        color: hideOnes.contains(open) ? ink : cs.outline,
                                      ),
                                      const SizedBox(width: 4),
                                      Text('HIDE ONE-OFFS ($singles)', style: opsKeyStyle(context)),
                                    ],
                                  ),
                                ),
                              ],
                              const Spacer(),
                              Text(
                                sel.isEmpty ? 'showing all' : '${sel.length} of ${facet.values.length}',
                                style: opsKeyStyle(context),
                              ),
                            ],
                          ),
                          const SizedBox(height: Sp.xs),
                          Expanded(
                            child: ListView(
                              children: [
                                for (final e in entries)
                                  InkWell(
                                    onTap: () => setState(() => sel.contains(e.key) ? sel.remove(e.key) : sel.add(e.key)),
                                    child: Padding(
                                      padding: const EdgeInsets.symmetric(vertical: 3),
                                      child: Row(
                                        children: [
                                          Icon(
                                            sel.contains(e.key) ? Icons.check_box : Icons.check_box_outline_blank,
                                            size: 18,
                                            color: sel.contains(e.key) ? ink : cs.outline,
                                          ),
                                          const SizedBox(width: Sp.s),
                                          Expanded(
                                            child: Tooltip(
                                              message: facet.paths ? e.key : '',
                                              waitDuration: const Duration(milliseconds: 500),
                                              child: Text(
                                                facet.paths ? readablePath(e.key) : e.key,
                                                overflow: TextOverflow.ellipsis,
                                                style: (facet.paths
                                                        ? AppTheme.mono.copyWith(fontSize: 12.5)
                                                        : theme.textTheme.bodyMedium!)
                                                    .copyWith(color: cs.onSurface),
                                              ),
                                            ),
                                          ),
                                          Text('${e.value}', style: AppTheme.mono.copyWith(fontSize: 12, color: cs.onSurfaceVariant)),
                                        ],
                                      ),
                                    ),
                                  ),
                                if (entries.isEmpty)
                                  Padding(
                                    padding: const EdgeInsets.symmetric(vertical: 8),
                                    child: Text('Nothing matches.', style: opsKeyStyle(context)),
                                  ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: Sp.m),
              Row(
                children: [
                  FilledButton(onPressed: () => Navigator.pop(context, picked), child: const Text('APPLY')),
                  const SizedBox(width: Sp.m),
                  TextButton(onPressed: () => Navigator.pop(context), child: const Text('CANCEL')),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
