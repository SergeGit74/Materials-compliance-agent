import pytest

from src import run

NXT = {"Item_ID": "INV-0002", "Internal_Description": "Contactor 3 pole 40A coil 230V"}


def feed(monkeypatch, replies, prompts=None):
    it = iter(replies)

    def fake_input(q=""):
        if prompts is not None:
            prompts.append(q)
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)


@pytest.mark.parametrize("reply", ["yes", "y", "next", "ok", "YES", " Ok "])
def test_yes_variants(monkeypatch, reply):
    feed(monkeypatch, [reply])
    assert run.ask_permission(NXT) == ("yes", None)


def test_skip_stop_redo(monkeypatch):
    feed(monkeypatch, ["skip"])
    assert run.ask_permission(NXT) == ("skip", None)
    feed(monkeypatch, ["stop"])
    assert run.ask_permission(NXT) == ("stop", None)
    feed(monkeypatch, ["redo inv-0001"])
    assert run.ask_permission(NXT) == ("redo", "INV-0001")


def test_other_reply_asks_again(monkeypatch):
    prompts = []
    feed(monkeypatch, ["", "sure", "do all", "redo", "yes"], prompts)
    assert run.ask_permission(NXT) == ("yes", None)
    assert len(prompts) == 5


def test_eof_is_stop(monkeypatch):
    feed(monkeypatch, [])
    assert run.ask_permission(NXT) == ("stop", None)


def test_exact_question(monkeypatch):
    prompts = []
    feed(monkeypatch, ["stop"], prompts)
    run.ask_permission(NXT)
    assert prompts[0].startswith(
        'Process next item INV-0002 ("Contactor 3 pole 40A coil 230V")? Reply: yes / skip / stop / redo INV-XXXX')
