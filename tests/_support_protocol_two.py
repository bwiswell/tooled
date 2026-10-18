"""A second protocol module that also defines a ``Station``: what makes a bare name ambiguous."""

from __future__ import annotations

from typing import ClassVar

import zeared


@zeared.zeared
class Station(zeared.Message):
    """Another protocol's station."""

    TOPIC: ClassVar[str] = 'other/{world}/station/{station}'
    RETAINED: ClassVar[bool] = True

    world: str = zeared.Str(required=True, doc="the world's name")
    station: str = zeared.Str(required=True, doc="the station's key")
