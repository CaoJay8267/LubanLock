"""Projection targets shipped with DotAgent."""

from __future__ import annotations

from dotagent.build.compiler import CompiledAgent

from .base import ProjectedFile, ProjectionResult, Projector
from .builtin import ClaudeProjector, JsonProjector, MarkdownProjector
from .registry import get_projector, list_projectors, register

register(JsonProjector())
register(MarkdownProjector())
register(ClaudeProjector())


def project_agent(compiled: CompiledAgent, target: str) -> ProjectionResult:
    return get_projector(target).project(compiled)


__all__ = [
    "CompiledAgent",
    "ProjectedFile",
    "ProjectionResult",
    "Projector",
    "get_projector",
    "list_projectors",
    "project_agent",
    "register",
]
