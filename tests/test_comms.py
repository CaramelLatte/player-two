from comms.intent import AddressDetector, Deduplicator
from comms.log_parser import parse_minecraft_line
from comms.messages import ChatMessage


def test_parse_vanilla_chat():
    m = parse_minecraft_line("[01:28:34] [Server thread/INFO]: <CinnamonLatte> hey PlayerTwo")
    assert m and m.sender == "CinnamonLatte" and m.text == "hey PlayerTwo"


def test_parse_paper_and_not_secure():
    m = parse_minecraft_line("[01:28:34 INFO]: [Not Secure] <Steve> get wood")
    assert m and m.sender == "Steve" and m.text == "get wood"


def test_parse_ignores_non_chat_and_server_say():
    assert parse_minecraft_line("[01:28:34] [Server thread/INFO]: Steve joined the game") is None
    assert parse_minecraft_line("[01:28:34] [Server thread/INFO]: [Server] restarting soon") is None
    assert parse_minecraft_line("[01:28:34] [Server thread/INFO]: [Rcon] hi") is None


def test_parse_strips_ansi():
    m = parse_minecraft_line("\x1b[0m[01:28:34] [Server thread/INFO]: <Steve> hi\x1b[0m")
    assert m and m.text == "hi"


def msg(text, sender="CinnamonLatte", private=False):
    return ChatMessage(sender=sender, text=text, source="test", private=private)


def detector(**kw):
    return AddressDetector("PlayerTwo", aliases=["player two", "p2"], owner="CinnamonLatte", **kw)


def test_name_and_alias_trigger():
    d = detector()
    assert d.decide(msg("PlayerTwo come here")).addressed
    assert d.decide(msg("hey player two, help")).addressed
    assert d.decide(msg("p2 stop")).addressed
    assert d.decide(msg("@playertwo hi")).addressed
    assert not d.decide(msg("playertwofold is not a word")).addressed


def test_owner_filter_and_self():
    d = detector()
    assert not d.decide(msg("PlayerTwo come here", sender="Griefer")).addressed
    assert not d.decide(msg("hello", sender="PlayerTwo")).addressed


def test_you_inference_depends_on_other_players():
    d = detector()
    alone = lambda: [{"name": "CinnamonLatte", "distance": 3}]
    crowd = lambda: [{"name": "CinnamonLatte", "distance": 3}, {"name": "Bob", "distance": 10}]
    far = lambda: [{"name": "Bob", "distance": 200}]
    assert d.decide(msg("can you grab some wood"), nearby_players=alone).addressed
    assert not d.decide(msg("can you grab some wood"), nearby_players=crowd).addressed
    assert d.decide(msg("can you grab some wood"), nearby_players=far).addressed
    assert not d.decide(msg("nice view"), nearby_players=alone).addressed


def test_followup_window():
    d = detector(followup_window_s=30)
    d.note_reply("CinnamonLatte", now=100)
    assert d.decide(msg("thanks"), now=120).addressed
    assert not d.decide(msg("thanks"), now=200).addressed


def test_whisper_is_addressed():
    assert detector().decide(msg("psst", private=True)).addressed


def test_dedup_window():
    dd = Deduplicator(window_s=5)
    assert not dd.is_duplicate(msg("hi"), now=0)
    assert dd.is_duplicate(msg("HI  "), now=2)  # same line from second source
    assert not dd.is_duplicate(msg("hi"), now=10)
