from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    sender: str
    text: str
    source: str  # docker | bridge | ...
    private: bool = False  # whisper / direct message
    time: float = field(default_factory=time.time)
