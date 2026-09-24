import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/provider_client.dart';
import 'package:mizpah_app/models/provider.dart';
import 'package:mizpah_app/state/provider_manager.dart';

void main() {
  test('load selects the first provider and reads its models', () async {
    final m = ProviderManager(FakeProviderClient());
    await m.load();
    expect(m.selected, 'openai_chatgpt');
    expect(m.models?.signedOut, isTrue);
    expect(m.models?.models, isEmpty);
    expect(m.current[ModelRole.worker], ModelChoice.none);
  });

  test('login shows the prompt, then the signed-in status', () async {
    final m = ProviderManager(FakeProviderClient());
    await m.load();
    await m.select('xai_grok');
    final seen = <String>[];
    m.addListener(() {
      seen.add('${m.signingIn}:${m.prompt?.userCode}:${m.selectedStatus?.badge}');
    });
    m.login();
    await Future<void>.delayed(const Duration(milliseconds: 600));
    expect(seen.first, 'true:null:SIGNED OUT');
    expect(seen, contains('true:WXYZ-1234:SIGNED OUT'));
    expect(seen.last, 'false:null:SIGNED IN');
    expect(m.selectedStatus?.account['email'], 'user@example.org');
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(m.models?.live, isTrue);
    expect(m.models?.models, ['model-large', 'model-small']);
  });

  test('use records the choice per role; logout clears sign-in', () async {
    final m = ProviderManager(FakeProviderClient());
    await m.load();
    m.login(apiKey: 'k');
    await Future<void>.delayed(const Duration(milliseconds: 50));
    await m.use('model-small', ModelRole.worker);
    expect(m.current[ModelRole.worker]?.model, 'model-small');
    expect(m.current[ModelRole.worker]?.effort, isNull); // no ladder
    expect(m.current[ModelRole.controller], ModelChoice.none);
    await m.use('model-large', null);
    expect(m.current.values.map((c) => c.model).toSet(), {'model-large'});
    await m.logout();
    expect(m.selectedStatus?.signedIn, isFalse);
  });

  test('effort follows the row pick and the model default', () async {
    final m = ProviderManager(FakeProviderClient());
    await m.load();
    m.login(apiKey: 'k');
    // The fake's default provider signs in over OAuth: a prompt, then done.
    while (m.signingIn || m.models?.live != true) {
      await Future<void>.delayed(const Duration(milliseconds: 20));
    }
    expect(m.effortFor('model-large'), 'high'); // the model's default
    expect(m.effortFor('model-small'), isNull);
    await m.use('model-large', ModelRole.controller);
    expect(m.current[ModelRole.controller]?.effort, 'high');
    await m.pickEffort('model-large', 'low');
    expect(m.current[ModelRole.controller]?.effort, 'low');
    expect(m.current[ModelRole.worker], ModelChoice.none);
    expect(m.effortFor('model-large'), 'low');
  });

  test('local servers are added and removed by the person', () async {
    final m = ProviderManager(FakeProviderClient());
    await m.load();
    await m.addLocal('My Box', 'http://127.0.0.1:8080');
    expect(m.selected, 'my_box');
    expect(m.selectedStatus?.custom, isTrue);
    expect(m.selectedStatus?.badge, 'NOT ANSWERING');
    await m.remove('my_box');
    expect(m.providers.where((p) => p.name == 'my_box'), isEmpty);
    expect(m.selected, isNot('my_box'));
  });

  test('status json maps badges', () {
    final s = ProviderStatus.fromJson({
      'provider': 'p',
      'display_name': 'P',
      'auth_kind': 'device_code',
      'signed_in': false,
      'quarantined': true,
      'quarantine_reason': 'invalid_grant',
    });
    expect(s.badge, 'REAUTH');
    expect(ProviderStatus.fromJson({'provider': 'p', 'signed_in': true, 'expired': true}).badge, 'SIGNED IN');
    expect(ProviderStatus.fromJson({'provider': 'p', 'signed_in': false, 'blocked': 'x'}).badge, 'BLOCKED');
    final local = ProviderStatus.fromJson({'provider': 'l', 'auth_kind': 'none', 'signed_in': true, 'reachable': true,
                                           'api_base_url': 'http://127.0.0.1:1'});
    expect(local.badge, 'ANSWERING');
    expect(local.local && local.baseUrl == 'http://127.0.0.1:1', isTrue);
  });
}
