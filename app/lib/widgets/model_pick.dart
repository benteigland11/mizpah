import 'package:flutter/material.dart';

import '../cg/ops_typography_kit/ops_typography_kit.dart';
import '../models/provider.dart';
import '../state/app_nav.dart';
import '../state/app_settings.dart';
import '../state/provider_manager.dart';
import '../theme/app_theme.dart';
import '../theme/kit_styles.dart';
import 'crew.dart';

/// The short model picker: the model a role runs on, as a chip; the menu
/// offers the models chosen lately and, under a rule, "see all", which
/// steps into the Providers page. Any surface that has a seat on it
/// (the Deputy's office; a task's crew) can carry one.
class ModelPick extends StatelessWidget {
  const ModelPick({
    super.key,
    required this.role,
    required this.providers,
    required this.settings,
    required this.nav,
    this.enabled = true,
  });
  final ModelRole role;
  final ProviderManager providers;
  final AppSettings settings;
  final AppNav nav;

  /// Off while a turn runs: the seat cannot change model mid-answer.
  final bool enabled;

  static const _seeAll = '\u0000see-all';

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([providers, settings]),
      builder: (context, _) {
        final cs = Theme.of(context).colorScheme;
        final choice = providers.current[role] ?? ModelChoice.none;
        // The engine runs a seat with no model of its own on the controller's.
        final fallback = choice.model == null && role == ModelRole.deputy
            ? providers.current[ModelRole.controller]
            : null;
        final shown = fallback ?? choice;
        final label = shown.model == null
            ? 'NO MODEL'
            : '${shown.model}${fallback != null ? ' · controller\'s' : ''}';
        final badge = Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            StatusBadge(label, style: opsStyle(context), hot: shown.model == null),
            const SizedBox(width: 4),
            Icon(Icons.arrow_drop_down, size: 18, color: cs.onSurfaceVariant),
          ],
        );
        // Lately chosen first; then whatever the seats run on now, so the
        // menu is never empty on a fresh install.
        final recents = [...settings.recentModels];
        for (final c in providers.current.values) {
          if (c.provider != null && c.model != null && !recents.contains((c.provider!, c.model!))) {
            recents.add((c.provider!, c.model!));
          }
        }
        // The effort beside the model, its own chip: the level changes
        // without the model being picked again. A fallback seat's effort is
        // the controller's to set.
        final effort = fallback == null
            ? EffortChip(providers: providers, role: role, choice: choice, enabled: enabled)
            : const SizedBox.shrink();
        Widget withEffort(Widget model) => Row(
              mainAxisSize: MainAxisSize.min,
              children: [model, if (effort is! SizedBox) ...[const SizedBox(width: Sp.s), effort]],
            );
        if (!enabled) return withEffort(Opacity(opacity: 0.45, child: badge));
        return withEffort(PopupMenuButton<String>(
          tooltip: 'Model for the ${role.label.toLowerCase()}',
          onSelected: (v) {
            if (v == _seeAll) {
              nav.go(AppNav.providers);
              return;
            }
            final i = v.indexOf('\u0000');
            providers.pick(v.substring(0, i), v.substring(i + 1), role);
          },
          itemBuilder: (_) => [
            if (recents.isEmpty)
              PopupMenuItem<String>(
                enabled: false,
                child: Text('Nothing chosen yet', style: opsKeyStyle(context)),
              ),
            for (final (provider, model) in recents)
              PopupMenuItem<String>(
                value: '$provider\u0000$model',
                child: Row(
                  children: [
                    Text(
                      model,
                      style: AppTheme.mono.copyWith(
                        fontSize: 12,
                        fontWeight: provider == choice.provider && model == choice.model ? FontWeight.w700 : FontWeight.w500,
                      ),
                    ),
                    const SizedBox(width: Sp.m),
                    Text(provider.toUpperCase(), style: opsKeyStyle(context)),
                  ],
                ),
              ),
            const PopupMenuDivider(),
            PopupMenuItem<String>(
              value: _seeAll,
              child: Text('See all models…', style: AppTheme.mono.copyWith(fontSize: 12)),
            ),
          ],
          child: badge,
        ));
      },
    );
  }
}
