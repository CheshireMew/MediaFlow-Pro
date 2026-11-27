from __future__ import annotations


class Projector[ContextT]:
    def __init__(self, context: ContextT):
        self._context = context
