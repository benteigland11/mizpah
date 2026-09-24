import 'package:flutter_test/flutter_test.dart';
import 'package:mizpah_app/engine/deputy_plain.dart';

void main() {
  test('tool calls read as what was done', () {
    expect(describeStep('call', 'draft_new  python -m mizpah.draft new ornith-landing --title "Ornith landing page" --mission "m"').what,
        'Started a draft: Ornith landing page');
    expect(describeStep('call', 'draft_show  python -m mizpah.draft show ornith-landing').what, 'Pulled up ornith-landing on the desk');
    expect(describeStep('call', 'bash  cd /work/ornith-landing && terra brief set --need "The page loads in under a second" --need "It has a form"').what,
        'Added 2 needs');
    expect(describeStep('call', 'bash  cd /work/x && terra brief set --deliverable "index.html at the repo root" --budget-points 60').what,
        'Added a deliverable: index.html at the repo root, set the budget: 60 points');
    expect(describeStep('call', 'bash  terra brief show').what, 'Read the brief');
    expect(describeStep('call', 'bash  cat /work/ornith-landing/README.md').what, 'Read ornith-landing/README.md');
    expect(describeStep('call', 'bash  ls /work').what, 'Looked through /work');
    expect(describeStep('call', 'read  /work/x/notes.md').what, 'Read x/notes.md');
    final fallback = describeStep('call', 'bash  cd /work && frobnicate --now');
    expect(fallback.what, 'Ran a command');
    expect(fallback.raw, 'frobnicate --now');
    expect(describeStep('memory', 'writing its working memory (compaction)').what, 'Wrote its working memory');
  });
}
