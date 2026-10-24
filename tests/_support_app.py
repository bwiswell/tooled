"""An app for the tests, as a policy's ``[app] factory`` names one: the game at the root, the service group twice."""

import parsed


def factory() -> parsed.App:
    """The fixture app: the game at the root, the service group under ``relay`` and ``gateway``."""
    app = parsed.App('demo')
    app.mount('_support_tree.game')
    app.mount('_support_tree.service', under='relay')
    app.mount('_support_tree.service', under='gateway')
    return app


def nested() -> parsed.App:
    """The service group under a prefix of two words."""
    app = parsed.App('demo')
    app.mount('_support_tree.service', under='rio relay')
    return app


def not_an_app() -> int:
    """A factory that returns something else."""
    return 5


def failing() -> parsed.App:
    """A factory that fails."""
    msg = 'no app today'
    raise RuntimeError(msg)


NOT_CALLABLE = 'an app'
