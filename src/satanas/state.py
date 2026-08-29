import asyncio
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PHASE_IDLE = "idle"
PHASE_SYNC = "sync"


@dataclass
class UserState:
    phase: str = PHASE_IDLE
    waiting_captcha: bool = False
    captcha_msg_id: int = 0
    holder: dict = field(default_factory=dict)
    captcha_done: threading.Event = field(default_factory=threading.Event)
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    canvas_chat_id: int = 0
    canvas_msg_id: int = 0
    canvas_pinned: bool = False
    canvas_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task = field(default=None, repr=False, compare=False)


_states: dict[int, UserState] = {}
_states_lock = threading.Lock()


def get(user_id: int) -> UserState:
    with _states_lock:
        if user_id not in _states:
            _states[user_id] = UserState()
        return _states[user_id]


def reset(user_id: int) -> None:
    with _states_lock:
        st = _states.get(user_id)
        if st:
            st.cancel_requested.set()
            st.captcha_done.set()  # Destraba cualquier espera en CaptchaRelay
            if st.task and not st.task.done():
                st.task.cancel()
            _states[user_id] = UserState()
