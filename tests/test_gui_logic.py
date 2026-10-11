"""The GUI's question routing (no display needed)."""

import queue
import threading

from fm1_board_tool.gui import Asker, classify_prompt
from fm1_board_tool import hwtest


def test_prompt_kinds():
    assert classify_prompt("  LED test: ... Did it? [y/n/s] ") == "yns"
    assert classify_prompt("A1. Is anything wired to GP0 or GP1 right now? [y/n] ") == "yn"
    assert classify_prompt("A1. Text printed on the big chip: ") == "text"
    for item in hwtest.FM1_CHECKLIST:                 # every checklist item is a yes/no question
        assert classify_prompt("B0.1 %s? [y/n] " % item) == "yn"


def test_asker_hands_the_question_to_the_ui_thread():
    a = Asker()
    got = []
    t = threading.Thread(target=lambda: got.append(a("Q? [y/n] ")))
    t.start()
    prompt, reply = a.requests.get(timeout=5)
    assert prompt == "Q? [y/n] "
    reply.put("y")
    t.join(5)
    assert got == ["y"]
