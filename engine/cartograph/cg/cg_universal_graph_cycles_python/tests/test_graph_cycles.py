from src.graph_cycles import find_cycles

def test_find_cycles():
    graph = {
        'A': [('B', {'line': 1})],
        'B': [('C', {'line': 2})],
        'C': [('A', {'line': 3})],
        'D': [('E', {'line': 4})]
    }
    cycles = find_cycles(graph)
    assert len(cycles) == 1
    assert [n for n, m in cycles[0]] == ['A', 'B', 'C']

def test_no_cycles():
    graph = {
        'A': [('B', {'line': 1})],
        'B': [('C', {'line': 2})]
    }
    cycles = find_cycles(graph)
    assert len(cycles) == 0
