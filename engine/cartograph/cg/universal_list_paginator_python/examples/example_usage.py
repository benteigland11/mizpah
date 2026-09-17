"""Demonstrate paginate() over a synthetic list of widget-like dicts."""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.list_paginator import paginate

widgets = [
    {"id": f"widget-{i:03d}", "version": "1.0.0", "outdated": i % 7 == 0}
    for i in range(128)
]

first = paginate(widgets, page=1, size=20)
print(json.dumps({k: v for k, v in first.items() if k != "items"}, indent=2))
print(f"first page item count: {len(first['items'])}")

middle = paginate(widgets, page=4, size=20)
print(f"\npage 4 first id: {middle['items'][0]['id']}  has_prev={middle['has_prev']}  has_next={middle['has_next']}")

overflow = paginate(widgets, page=999, size=20)
print(f"\nrequested page 999, clamped to page {overflow['page']} ({len(overflow['items'])} items)")

empty = paginate([], page=1, size=20)
print(f"\nempty input: total={empty['total']} total_pages={empty['total_pages']} items={empty['items']}")
