"""A service's skeleton group: mounted by many apps, each under its own prefix."""

import parsed


@parsed.parsed
class Run(parsed.Command):
    """Run the service until it's stopped."""

    PHRASE = 'run'
    EFFECT = parsed.Effect.ACTION


@parsed.parsed
class Status(parsed.Command):
    """Say whether the service is up."""

    PHRASE = 'status'
    EFFECT = parsed.Effect.READ
