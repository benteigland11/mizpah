"""
Example usage of Graph Cycles.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.graph_cycles import find_cycles

# Simple A -> B -> A cycle
graph = {
    'A': [('B', {'line': 1, 'module': 'b'})],
    'B': [('A', {'line': 2, 'module': 'a'})]
}
cycles = find_cycles(graph)
print(f"Found {len(cycles)} cycles.")
for i, cycle in enumerate(cycles):
    print(f"Cycle {i+1}: {' -> '.join(node for node, meta in cycle)}")
