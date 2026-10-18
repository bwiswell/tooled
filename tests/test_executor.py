import pytest
from _support_session import free_port
from zeared import Mode, SessionConfig

from parsed.errors import MeshError
from parsed.executor import open_session


def test_opens_the_session_the_mesh_describes(listening):
    session = open_session(SessionConfig(mode=Mode.CLIENT, router=listening))
    session.close()


def test_an_unreachable_router_is_a_mesh_error_with_zenoh_s_reason():
    router = f'tcp/127.0.0.1:{free_port()}'
    with pytest.raises(MeshError, match='the session would not open: Unable to connect') as exc:
        open_session(SessionConfig(mode=Mode.CLIENT, router=router))
    assert '/root/.cargo' not in exc.value.message
    assert exc.value.exit_code == 5
