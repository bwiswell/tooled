"""Two commands whose phrases read one line two ways: a tree parsed refuses to build."""

import parsed


@parsed.parsed
class StartOne(parsed.Command):
    """Start one way."""

    PHRASE = 'start'


@parsed.parsed
class StartTwo(parsed.Command):
    """Start another."""

    PHRASE = 'start'
