import 'package:flutter/material.dart';

import '../../state/app_nav.dart';
import '../../state/app_settings.dart';
import '../../state/brief_manager.dart';
import '../../state/provider_manager.dart';
import '../../state/route_manager.dart';
import '../proposal_view.dart';
import 'document.dart';

/// The selected project's brief as the reference document it is — or the
/// change request being read against it.
class BriefScreen extends StatelessWidget {
  const BriefScreen({super.key, required this.manager, this.route, this.nav, this.settings, this.providers});
  final BriefManager manager;
  final RouteManager? route;
  final AppNav? nav;

  /// The signer, for the signature line that issues a draft.
  final AppSettings? settings;
  final ProviderManager? providers;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: manager,
      builder: (context, _) => manager.viewingProposal == null
          ? BriefDocument(manager: manager, route: route, nav: nav, settings: settings, providers: providers)
          : ProposalView(manager: manager),
    );
  }
}
