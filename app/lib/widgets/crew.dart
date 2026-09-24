import 'package:flutter/material.dart';

import '../models/provider.dart';
import '../state/provider_manager.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import 'ops.dart';

/// The crew: which model sits in each seat for one task. Shared by the
/// new-task sheet and the brief, which ends in the crew it will run on.

/// The effort a seat runs at, as a chip of its own beside the model, so
/// the level moves without the model being picked again. Shown only for a
/// model that takes levels; nothing while the provider's list is unread.
class EffortChip extends StatelessWidget {
  const EffortChip({super.key, required this.providers, required this.role, required this.choice, this.projectPath, this.enabled = true, this.onSet});
  final ProviderManager providers;
  final ModelRole role;
  final ModelChoice choice;

  /// The task the seat belongs to; null for the default seat.
  final String? projectPath;
  final bool enabled;

  /// After the level is written: a task re-reads its own config.
  final Future<void> Function()? onSet;

  @override
  Widget build(BuildContext context) {
    final efforts = providers.effortsOf(choice.provider, choice.model);
    if (efforts.isEmpty) return const SizedBox.shrink();
    final value = choice.effort ?? 'default';
    final chip = OpsChip(
      value: value.toUpperCase(),
      options: enabled ? efforts : const [],
      hot: {if (choice.effort != null) choice.effort!.toUpperCase()},
      onSelected: (e) async {
        await providers.setEffort(role, choice, e, projectPath: projectPath);
        await onSet?.call();
      },
    );
    return enabled ? chip : Opacity(opacity: 0.45, child: chip);
  }
}

/// One seat: the role, the model it has (its own choice or the default),
/// and CHANGE, which opens [ModelPicker] for this task only. Without
/// [onChange] the row is read-only: the crew as it was locked in on issue.
class CrewRoleRow extends StatelessWidget {
  const CrewRoleRow({super.key, required this.role, required this.choice, this.changing = false, this.onChange, this.isDefault = false, this.effort});
  final ModelRole role;
  final ModelChoice choice;
  final bool changing;
  final VoidCallback? onChange;

  /// The effort chip, when the seat may still change.
  final Widget? effort;

  /// Riding on the user's default rather than a choice of the task's own.
  final bool isDefault;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    return Container(
      padding: const EdgeInsets.symmetric(vertical: Sp.m),
      decoration: BoxDecoration(border: Border(bottom: BorderSide(color: cs.outlineVariant))),
      child: Row(
        children: [
          SizedBox(width: 130, child: Text(role.label, style: opsLabelStyle(context))),
          Expanded(
            child: Text(
              choice.model == null
                  ? 'not set'
                  : '${choice.model}${choice.provider != null ? '  ·  ${choice.provider}' : ''}${choice.effort != null && effort == null ? '  ·  ${choice.effort}' : ''}',
              style: AppTheme.mono.copyWith(fontSize: 13, color: choice.model == null ? cs.onSurfaceVariant : ink),
            ),
          ),
          if (isDefault && choice.model != null)
            Padding(
              padding: const EdgeInsets.only(right: Sp.m),
              child: Text('YOUR DEFAULT', style: opsKeyStyle(context)),
            ),
          if (effort != null) Padding(padding: const EdgeInsets.only(right: Sp.s), child: effort),
          if (onChange != null) TextButton(onPressed: onChange, child: Text(changing ? 'DONE' : 'CHANGE', style: opsLabelStyle(context))),
        ],
      ),
    );
  }
}

/// The model picker: most of the window, fixed size. Providers down the
/// left (signed-in ones live, the rest greyed with why), the chosen
/// provider's models on the right with a search. Picking one sets it for
/// the role and closes.
class ModelPicker extends StatefulWidget {
  const ModelPicker({super.key, required this.providers, required this.role, required this.projectPath, this.current});
  final ProviderManager providers;
  final ModelRole role;

  /// The task the choice belongs to, and the seat it has now (null: the default).
  final String projectPath;
  final ModelChoice? current;

  @override
  State<ModelPicker> createState() => _ModelPickerState();
}

class _ModelPickerState extends State<ModelPicker> {
  String? provider;
  String q = '';
  bool busy = false;

  /// The model whose reasoning effort is being chosen before use.
  String? staged;

  @override
  void initState() {
    super.initState();
    provider = widget.current?.provider ??
        widget.providers.current[widget.role]?.provider ??
        widget.providers.selected;
    if (provider != null) widget.providers.select(provider!);
  }

  /// A model with effort levels is staged first so one can be chosen; a
  /// model without them is used at once.
  Future<void> _pick(String model, {bool hasEfforts = false}) async {
    if (hasEfforts && staged != model) return setState(() => staged = model);
    setState(() => busy = true);
    await widget.providers.useForTask(widget.projectPath, model, widget.role);
    if (mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final cs = theme.colorScheme;
    final ink = AppTheme.ink(context);
    final size = MediaQuery.sizeOf(context);
    final pm = widget.providers;
    return ListenableBuilder(
      listenable: pm,
      builder: (context, _) {
        final current = widget.current ?? pm.current[widget.role];
        final models = pm.selected == provider ? pm.models : null;
        final needle = q.trim().toLowerCase();
        final shown = models == null ? const <String>[] : models.models.where((m) => needle.isEmpty || m.toLowerCase().contains(needle)).toList();
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
                      Text('${widget.role.label} MODEL', style: opsLabelStyle(context).copyWith(color: cs.primary)),
                      const SizedBox(width: Sp.l),
                      if (current?.model != null)
                        Text('now ${current!.model} · ${current.provider}', style: opsKeyStyle(context)),
                    ],
                  ),
                  const SizedBox(height: Sp.m),
                  Expanded(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        SizedBox(
                          width: 260,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('PROVIDERS', style: opsKeyStyle(context)),
                              const SizedBox(height: Sp.s),
                              Expanded(
                                child: ListView(
                                  children: [
                                    for (final p in [...pm.providers.where((p) => p.signedIn), ...pm.providers.where((p) => !p.signedIn)])
                                      InkWell(
                                        onTap: p.signedIn
                                            ? () {
                                                setState(() {
                                                  provider = p.name;
                                                  q = '';
                                                });
                                                pm.select(p.name);
                                              }
                                            : null,
                                        child: Container(
                                          padding: const EdgeInsets.symmetric(horizontal: Sp.m, vertical: Sp.s),
                                          decoration: BoxDecoration(
                                            color: provider == p.name ? cs.surfaceContainerHigh : null,
                                            border: Border(left: BorderSide(color: provider == p.name ? ink : Colors.transparent, width: 3)),
                                          ),
                                          child: Row(
                                            children: [
                                              Expanded(
                                                child: Text(
                                                  p.name,
                                                  style: AppTheme.mono.copyWith(
                                                    fontSize: 13,
                                                    color: !p.signedIn ? cs.outline : (provider == p.name ? ink : cs.onSurface),
                                                    fontWeight: provider == p.name ? FontWeight.w700 : null,
                                                  ),
                                                ),
                                              ),
                                              if (!p.signedIn) Text('SIGN IN', style: opsKeyStyle(context).copyWith(color: cs.outline)),
                                            ],
                                          ),
                                        ),
                                      ),
                                    if (pm.providers.isEmpty)
                                      Padding(
                                        padding: const EdgeInsets.all(Sp.m),
                                        child: Text('Loading providers…', style: opsKeyStyle(context)),
                                      ),
                                  ],
                                ),
                              ),
                              Text('Sign in under Providers to add one.', style: opsKeyStyle(context)),
                            ],
                          ),
                        ),
                        VerticalDivider(width: Sp.xl, color: cs.outlineVariant),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              SizedBox(
                                height: 32,
                                child: TextField(
                                  autofocus: true,
                                  onChanged: (v) => setState(() => q = v),
                                  style: AppTheme.mono.copyWith(fontSize: 13),
                                  decoration: InputDecoration(
                                    isDense: true,
                                    contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                                    prefixIcon: Icon(Icons.search, size: 16, color: cs.onSurfaceVariant),
                                    prefixIconConstraints: const BoxConstraints(minWidth: 30),
                                    hintText: provider == null ? 'pick a provider' : 'search $provider models',
                                    enabledBorder: OutlineInputBorder(borderSide: BorderSide(color: cs.outlineVariant)),
                                    focusedBorder: OutlineInputBorder(borderSide: BorderSide(color: cs.outline)),
                                  ),
                                ),
                              ),
                              const SizedBox(height: Sp.s),
                              Expanded(
                                child: provider == null
                                    ? Center(child: Text('Pick a provider on the left.', style: opsKeyStyle(context)))
                                    : models == null
                                    ? Center(child: Text('Listing models…', style: opsKeyStyle(context)))
                                    : ListView.separated(
                                        itemCount: shown.length,
                                        separatorBuilder: (_, _) => Divider(height: 1, color: cs.outlineVariant),
                                        itemBuilder: (context, i) {
                                          final m = shown[i];
                                          final detail = models.details[m];
                                          final efforts = detail?.efforts ?? const <String>[];
                                          final isCurrent = current?.model == m && current?.provider == provider;
                                          final isStaged = staged == m;
                                          final effort = pm.effortFor(m) ?? detail?.defaultEffort;
                                          return InkWell(
                                            onTap: busy ? null : () => _pick(m, hasEfforts: efforts.isNotEmpty),
                                            child: Container(
                                              color: isStaged ? cs.surfaceContainerHigh : null,
                                              padding: const EdgeInsets.symmetric(vertical: Sp.m, horizontal: Sp.s),
                                              child: Column(
                                                crossAxisAlignment: CrossAxisAlignment.start,
                                                children: [
                                                  Row(
                                                    children: [
                                                      Expanded(
                                                        child: Text(m, style: AppTheme.mono.copyWith(fontSize: 13, color: isCurrent || isStaged ? ink : cs.onSurface, fontWeight: isCurrent || isStaged ? FontWeight.w700 : null)),
                                                      ),
                                                      if (efforts.isNotEmpty && !isStaged)
                                                        Text('reasoning ${efforts.join(' / ')}', style: opsKeyStyle(context)),
                                                      if (isCurrent) ...[
                                                        const SizedBox(width: Sp.m),
                                                        Text('CURRENT${current?.effort != null ? ' · ${current!.effort}' : ''}', style: opsKeyStyle(context).copyWith(color: ink)),
                                                      ],
                                                    ],
                                                  ),
                                                  // Staged: choose the reasoning effort, then USE.
                                                  if (isStaged)
                                                    Padding(
                                                      padding: const EdgeInsets.only(top: Sp.s),
                                                      child: Row(
                                                        children: [
                                                          Text('REASONING', style: opsKeyStyle(context)),
                                                          const SizedBox(width: Sp.m),
                                                          for (final e in efforts)
                                                            Padding(
                                                              padding: const EdgeInsets.only(right: Sp.s),
                                                              child: InkWell(
                                                                onTap: () => pm.pickEffort(m, e),
                                                                child: Container(
                                                                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                                                  decoration: BoxDecoration(
                                                                    border: Border.all(color: effort == e ? ink : cs.outline, width: effort == e ? 1.5 : 1),
                                                                    borderRadius: BorderRadius.circular(2),
                                                                    color: effort == e ? ink.withValues(alpha: 0.08) : null,
                                                                  ),
                                                                  child: Text(e, style: AppTheme.mono.copyWith(fontSize: 12, color: effort == e ? ink : cs.onSurface)),
                                                                ),
                                                              ),
                                                            ),
                                                          const Spacer(),
                                                          FilledButton(
                                                            onPressed: busy ? null : () => _pick(m, hasEfforts: false),
                                                            style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10)),
                                                            child: Text(busy ? 'SETTING…' : 'USE'),
                                                          ),
                                                        ],
                                                      ),
                                                    ),
                                                ],
                                              ),
                                            ),
                                          );
                                        },
                                      ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: Sp.m),
                  Row(children: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('CANCEL'))]),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
