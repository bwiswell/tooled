"""A protocol module whose one contract declares an EFFECT that is no effect."""

from __future__ import annotations

from typing import ClassVar

import zeared


@zeared.zeared
class Broken(zeared.Message):
    """A contract whose EFFECT is no effect."""

    TOPIC: ClassVar[str] = 'other/broken'
    EFFECT = 'sideways'
