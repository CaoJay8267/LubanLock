"""Deterministic graph helpers shared by validation, runtime and tests."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def adjacency(
    nodes: Sequence[str], edges: Iterable[tuple[str, str]]
) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        if source in graph and target in graph:
            graph[source].append(target)
    for targets in graph.values():
        targets.sort()
    return graph


def topological_order(
    nodes: Sequence[str], edges: Iterable[tuple[str, str]]
) -> list[str] | None:
    """Kahn's algorithm with deterministic tie-breaking; None on a cycle."""

    graph = adjacency(nodes, edges)
    indegree = {node: 0 for node in nodes}
    for targets in graph.values():
        for target in targets:
            indegree[target] += 1
    ready = sorted(node for node in nodes if indegree[node] == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target in graph[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
        ready.sort()
    return order if len(order) == len(nodes) else None


def layers(
    nodes: Sequence[str], edges: Iterable[tuple[str, str]]
) -> tuple[tuple[str, ...], ...] | None:
    """Group nodes into parallel execution layers; None on a cycle."""

    graph = adjacency(nodes, edges)
    order = topological_order(nodes, edges)
    if order is None:
        return None
    level: dict[str, int] = {node: 0 for node in nodes}
    for node in order:
        for target in graph[node]:
            level[target] = max(level[target], level[node] + 1)
    grouped: dict[int, list[str]] = {}
    for node in nodes:
        grouped.setdefault(level[node], []).append(node)
    return tuple(tuple(sorted(grouped[key])) for key in sorted(grouped))


def reachable(
    entry: str, nodes: Sequence[str], edges: Iterable[tuple[str, str]]
) -> set[str]:
    graph = adjacency(nodes, edges)
    seen: set[str] = set()
    stack = [entry]
    while stack:
        current = stack.pop()
        if current in seen or current not in graph:
            continue
        seen.add(current)
        stack.extend(graph[current])
    return seen
