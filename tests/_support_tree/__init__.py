"""A small command tree for the tests, shaped like an app's own: a game's commands, and a service's skeleton group.

- ``game``: reads and writes at the root, one taking its fields from the fixture protocol's ``MoveRequest`` and one
  replying with its ``Station``, and an interactive command with the read an agent says instead;
- ``service``: a skeleton group every service carries, mounted by the fixture app twice, under ``relay`` and
  ``gateway`` (``_support_app``).
"""
