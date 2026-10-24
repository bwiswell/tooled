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
