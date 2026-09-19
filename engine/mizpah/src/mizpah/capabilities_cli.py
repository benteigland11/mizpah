"""`python -m mizpah.capabilities_cli --config <cfg>`: what the registry says this installation can do."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import capabilities


def main() -> None:
    parser = argparse.ArgumentParser(description='List graduated enablers across projects')
    parser.add_argument('--config', required=True)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    rows = capabilities.registered(config)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    if not rows:
        print('(no enablers registered yet)')
    for cap in rows:
        print(cap['id']+' ['+str(cap.get('kind'))+', '+str(cap.get('status'))+'] '+str(cap.get('title'))
              +(' → '+str(cap['graduates_to']) if cap.get('graduates_to') else ' at '+str(cap.get('path')))
              +' — by '+str(cap.get('project'))+', used '+str(cap.get('uses'))+'×')
        if cap.get('notes'):
            print('    '+str(cap['notes'])[:200])


if __name__ == '__main__':
    main()
