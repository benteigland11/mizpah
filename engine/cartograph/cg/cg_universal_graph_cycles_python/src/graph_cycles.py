def find_cycles(graph):
    """
    Find all simple cycles in a directed graph.
    :param graph: dict mapping nodes to list of (neighbor, metadata)
    :return: A list of cycles, where each cycle is a list of (node, metadata) tuples.
    """
    cycles = []
    
    def visit(node, stack, stack_set, visited):
        visited.add(node)
        
        for neighbor, meta in graph.get(node, []):
            if neighbor in stack_set:
                # Cycle detected
                cycle = []
                found_start = False
                for s_node, s_meta in stack:
                    if s_node == neighbor:
                        found_start = True
                    if found_start:
                        cycle.append((s_node, s_meta))
                # The stack contains the node and the metadata of the edge TO its neighbor.
                # So we add the current node and the metadata of the edge TO the neighbor that closed the cycle.
                cycle.append((node, meta))
                cycles.append(cycle)
            elif neighbor not in visited:
                stack.append((node, meta))
                stack_set.add(node)
                visit(neighbor, stack, stack_set, visited)
                stack_set.remove(node)
                stack.pop()

    overall_visited = set()
    # Use list() to avoid dictionary size changed during iteration if that were possible
    for node in list(graph.keys()):
        if node not in overall_visited:
            visit(node, [], set(), overall_visited)
            
    return cycles
