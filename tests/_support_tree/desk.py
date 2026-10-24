"""An editor and a viewer, neither marked INTERACTIVE: mounted by the fixture's desk app alone."""

import parsed


@parsed.parsed
class NoteEdit(parsed.Command):
    """Edit the note, in a prompt for a person: no effect declared, and not marked interactive."""

    PHRASE = 'note edit'


@parsed.parsed
class ScreenShow(parsed.Command):
    """Draw the screen until a key is pressed: a read, not marked interactive."""

    PHRASE = 'screen show'
    EFFECT = parsed.Effect.READ


@parsed.parsed
class BellRing(parsed.Command):
    """Ring the bell until someone answers: a read whose handler waits on a person without blocking its loop."""

    PHRASE = 'bell ring'
    EFFECT = parsed.Effect.READ


@parsed.parsed
class Clock(parsed.Parsed):
    """The desk's clock: how many times it's been read."""

    tick: int = parsed.Int(required=True, doc='the reads so far')


@parsed.parsed
class ClockShow(parsed.Command):
    """Read the desk's clock: a read whose reply changes each time, for a watch to follow."""

    PHRASE = 'clock show'
    EFFECT = parsed.Effect.READ
    REPLY = Clock
