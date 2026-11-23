"""Broker access-control test for the FlyBot development broker.

Checks the running broker against the permission table in docs/message-contract.md
(section 4), so it follows the contract automatically:

  * every user x every topic: a publish gets through exactly where the table says P, and
    messages are delivered to a subscriber exactly where it says S;
  * wildcard subscriptions (#, flybot/01/#, flybot/01/+, flybot/01/brain/#) deliver exactly the
    topics the user may read, nothing more;
  * publishing anywhere outside the contract's topics is dropped;
  * anonymous, wrong-password, swapped-password and unknown-user logins are refused;
  * the WebSocket listener (9001) applies the same rules;
  * the committed ACL file matches the contract.

How Mosquitto enforces reads: with an ACL file it does NOT refuse a SUBSCRIBE from an MQTT 3.1.1
client (it grants it), but checks the ACL before delivering every message and silently drops
what the client may not read (Mosquitto ChangeLog: "checks are still made before a message is
sent to a client"). So forbidden reads are tested by delivery, not by the SUBACK.

Needs: the broker running (infra\\start-broker.cmd or "... local"), .env with the MQTT
passwords. Run from the repo root:

  infra\\.venv\\Scripts\\python -m pytest infra\\tests -v

Test messages are NOT contract-valid JSON and are never retained, so they cannot overwrite
saved state (e.g. an e-stop). If the brain is running it will count them as invalid messages.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "infra" / "mosquitto"))
import gen_acl  # noqa: E402

USERS = gen_acl.USERS
PREFIX = gen_acl.PREFIX
TOPICS, PERMS = gen_acl.parse_table()
DELIVERY_WAIT = 1.5  # seconds to wait for messages to arrive
RUN = uuid.uuid4().hex[:8]  # marks this run's messages


def load_env() -> dict[str, str]:
    path = REPO / ".env"
    if not path.exists():
        pytest.exit(".env not found: run infra/mosquitto/make_passwords.py first", returncode=2)
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
HOST = os.environ.get("MQTT_HOST", ENV.get("MQTT_HOST", "127.0.0.1"))
if HOST == "localhost":
    HOST = "127.0.0.1"  # avoid Windows trying IPv6 ::1 first (2 s stall per connection)
PORT = int(ENV.get("MQTT_PORT", "1883"))
WS_PORT = int(ENV.get("MQTT_WS_PORT", "9001"))
CREDS = {u: (ENV[f"MQTT_{u.upper()}_USER"], ENV[f"MQTT_{u.upper()}_PASS"]) for u in USERS}


class Conn:
    """A paho client with blocking helpers. MQTT 3.1.1, like the ESP32 and dashboard."""

    def __init__(self, name: str, creds: tuple[str, str] | None, transport: str = "tcp"):
        self.c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"flybot-acltest-{name}-{RUN}",
                             protocol=mqtt.MQTTv311, transport=transport)
        if creds:
            self.c.username_pw_set(*creds)
        self.port = WS_PORT if transport == "websockets" else PORT
        self.connected = threading.Event()
        self.rc = None
        self.lock = threading.Lock()
        self.msgs: list[tuple[str, str]] = []
        self.subacks: dict[int, object] = {}
        self.sub_done = threading.Condition(self.lock)
        self.c.on_connect = self._on_connect
        self.c.on_message = self._on_message
        self.c.on_subscribe = self._on_subscribe

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self.rc = reason_code
        self.connected.set()

    def _on_message(self, client, userdata, msg):
        with self.lock:
            self.msgs.append((msg.topic, msg.payload.decode("utf-8", "replace")))

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        with self.lock:
            self.subacks[mid] = reason_codes[0]
            self.sub_done.notify_all()

    def connect(self, timeout: float = 5.0) -> bool:
        """Return True if the broker accepted the connection."""
        try:
            self.c.connect(HOST, self.port, keepalive=30)
        except OSError as e:
            pytest.exit(f"cannot reach broker at {HOST}:{self.port}: {e}. Is it running?", returncode=2)
        self.c.loop_start()
        if not self.connected.wait(timeout):
            raise TimeoutError("no CONNACK")
        return not self.rc.is_failure

    def subscribe(self, topic: str, timeout: float = 5.0) -> bool:
        """Return True if the broker granted the subscription."""
        _, mid = self.c.subscribe(topic, qos=1)
        with self.lock:
            if not self.sub_done.wait_for(lambda: mid in self.subacks, timeout):
                raise TimeoutError(f"no SUBACK for {topic}")
            return not self.subacks[mid].is_failure

    def publish(self, topic: str, payload: str) -> None:
        info = self.c.publish(topic, payload, qos=1, retain=False)
        info.wait_for_publish(5.0)

    def received(self, marker: str) -> set[str]:
        with self.lock:
            return {t for t, p in self.msgs if marker in p}

    def close(self) -> None:
        self.c.disconnect()
        self.c.loop_stop()


def connect_ok(name: str, user: str) -> Conn:
    conn = Conn(name, CREDS[user])
    assert conn.connect(), f"{user} could not log in"
    return conn


# --------------------------------------------------------------------------- full matrix

@pytest.fixture(scope="module")
def matrix():
    """Run every publish and subscribe once; return what happened."""
    observer = connect_ok("observer", "brain")  # brain may read flybot/01/#
    assert observer.subscribe(PREFIX + "#")
    clients = {u: connect_ok(u, u) for u in USERS}

    # Publish phase: every user publishes to every topic; the observer sees what got through.
    pub_marker = f"acltest {RUN} pub"
    for u, conn in clients.items():
        for t in TOPICS:
            conn.publish(PREFIX + t, f"{pub_marker} {u} {t}")
    time.sleep(DELIVERY_WAIT)
    with observer.lock:
        delivered_pub = {(p.split()[3], p.split()[4]) for _, p in observer.msgs if p.startswith(pub_marker)}

    # Subscribe phase: every user subscribes to every topic, then one allowed publisher per
    # topic publishes a marker; record SUBACK result and actual delivery.
    granted = {(u, t): clients[u].subscribe(PREFIX + t) for u in USERS for t in TOPICS}
    sub_marker = f"acltest {RUN} sub"
    publish_on_every_topic(clients, sub_marker)
    delivered_sub = {(u, t[len(PREFIX):]) for u in USERS for t in clients[u].received(sub_marker)}

    # Wildcard phase: a fresh connection per (user, pattern), then a marker on every topic.
    wild = {(u, w): connect_ok(f"wild-{u}-{i}", u) for u in USERS for i, w in enumerate(WILDCARD_PATTERNS)}
    for (u, w), conn in wild.items():
        conn.subscribe(w)
    wild_marker = f"acltest {RUN} wild"
    publish_on_every_topic(clients, wild_marker)
    delivered_wild = {key: {t[len(PREFIX):] for t in conn.received(wild_marker)} for key, conn in wild.items()}

    # Foreign-topic phase: every user publishes outside the contract's topics; the observer
    # (brain, reads flybot/01/#) must see none of it.
    foreign_marker = f"acltest {RUN} foreign"
    for u, conn in clients.items():
        for topic in FOREIGN_TOPICS:
            conn.publish(topic, f"{foreign_marker} {u}")
    time.sleep(DELIVERY_WAIT)
    with observer.lock:
        delivered_foreign = [(t, p) for t, p in observer.msgs if p.startswith(foreign_marker)]

    yield {"pub": delivered_pub, "granted": granted, "sub": delivered_sub,
           "wild": delivered_wild, "foreign": delivered_foreign}
    for conn in [observer, *clients.values(), *wild.values()]:
        conn.close()


def publish_on_every_topic(clients: dict[str, Conn], marker: str) -> None:
    """Publish a marker on every contract topic, each by a user allowed to publish it."""
    for t in TOPICS:
        publisher = next(u for u in USERS if PERMS[(u, t)][0])
        clients[publisher].publish(PREFIX + t, f"{marker} {t}")
    time.sleep(DELIVERY_WAIT)


WILDCARD_PATTERNS = ["#", PREFIX + "#", PREFIX + "+", PREFIX + "brain/#"]
# Topics outside the contract. Readable by brain's flybot/01/# grant, so delivery to the
# observer would prove the write got through. (flybot/02/... cannot be observed by any user.)
FOREIGN_TOPICS = ["flybot/01", PREFIX + "unknown", PREFIX + "sensors/extra", PREFIX + "cmd", PREFIX + "brain"]


CASES = [(u, t) for u in USERS for t in TOPICS]
IDS = [f"{u}-{t}" for u, t in CASES]


@pytest.mark.parametrize("user,topic", CASES, ids=IDS)
def test_publish(matrix, user, topic):
    allowed = PERMS[(user, topic)][0]
    got_through = (user, topic) in matrix["pub"]
    assert got_through == allowed, (
        f"{user} publish {topic}: contract says {'allowed' if allowed else 'forbidden'}, "
        f"broker {'delivered' if got_through else 'dropped'} it")


@pytest.mark.parametrize("user,topic", CASES, ids=IDS)
def test_subscribe(matrix, user, topic):
    allowed = PERMS[(user, topic)][1]
    delivered = (user, topic) in matrix["sub"]
    if allowed:
        assert matrix["granted"][(user, topic)], f"{user} subscribe {topic}: allowed but SUBACK refused"
    assert delivered == allowed, (
        f"{user} read {topic}: contract says {'allowed' if allowed else 'forbidden'}, "
        f"message {'delivered' if delivered else 'not delivered'}")


WILD_CASES = [(u, w) for u in USERS for w in WILDCARD_PATTERNS]


@pytest.mark.parametrize("user,pattern", WILD_CASES, ids=[f"{u}-{w}" for u, w in WILD_CASES])
def test_wildcard_subscribe(matrix, user, pattern):
    """A wildcard delivers exactly the matching topics the user may read."""
    expected = {t for t in TOPICS if PERMS[(user, t)][1] and mqtt.topic_matches_sub(pattern, PREFIX + t)}
    got = matrix["wild"][(user, pattern)]
    assert got == expected, (f"{user} subscribed {pattern}: unexpected {sorted(got - expected)}, "
                             f"missing {sorted(expected - got)}")


def test_foreign_topics_dropped(matrix):
    """No user can publish outside the contract's topics."""
    assert matrix["foreign"] == [], f"writes outside the contract got through: {matrix['foreign']}"


# --------------------------------------------------------------------------- logins

def test_anonymous_refused():
    conn = Conn("anonymous", None)
    try:
        assert not conn.connect(), "anonymous login was accepted"
    finally:
        conn.close()


@pytest.mark.parametrize("user", USERS)
def test_wrong_password_refused(user):
    conn = Conn(f"wrongpw-{user}", (CREDS[user][0], "wrong-" + uuid.uuid4().hex))
    try:
        assert not conn.connect(), f"{user} logged in with a wrong password"
    finally:
        conn.close()


def test_unknown_user_refused():
    conn = Conn("intruder", ("intruder", uuid.uuid4().hex))
    try:
        assert not conn.connect(), "unknown user was accepted"
    finally:
        conn.close()


def test_other_users_password_refused():
    conn = Conn("swap", (CREDS["esp32"][0], CREDS["brain"][1]))
    try:
        assert not conn.connect(), "esp32 logged in with brain's password"
    finally:
        conn.close()


# --------------------------------------------------------------------------- WebSockets listener

def test_websockets_dashboard():
    """Same rules on port 9001: allowed read/write work, forbidden ones are dropped."""
    ws = Conn("ws-dashboard", CREDS["dashboard"], transport="websockets")
    observer = connect_ok("ws-observer", "brain")
    esp32, bot = connect_ok("ws-esp32", "esp32"), connect_ok("ws-bot", "bot")
    try:
        assert ws.connect(), "dashboard could not log in over WebSockets"
        assert observer.subscribe(PREFIX + "#")
        assert ws.subscribe(PREFIX + "sensors")
        ws.subscribe(PREFIX + "brain/sleep")  # granted by Mosquitto, but must never deliver
        marker = f"acltest {RUN} ws"
        esp32.publish(PREFIX + "sensors", marker)       # dashboard may read
        bot.publish(PREFIX + "brain/sleep", marker)     # dashboard may NOT read
        ws.publish(PREFIX + "cmd/manual", marker)       # dashboard may write
        ws.publish(PREFIX + "cmd/motor", marker)        # dashboard may NOT write
        time.sleep(DELIVERY_WAIT)
        assert ws.received(marker) == {PREFIX + "sensors"}
        seen = observer.received(marker)
        assert PREFIX + "cmd/manual" in seen
        assert PREFIX + "cmd/motor" not in seen
    finally:
        for c in (ws, observer, esp32, bot):
            c.close()


def test_websockets_anonymous_refused():
    ws = Conn("ws-anonymous", None, transport="websockets")
    try:
        assert not ws.connect(), "anonymous WebSocket login was accepted"
    finally:
        ws.close()


# --------------------------------------------------------------------------- ACL file

def test_acl_file_matches_contract():
    committed = (REPO / "infra" / "mosquitto" / "acl").read_text(encoding="utf-8")
    assert committed == gen_acl.render(), "infra/mosquitto/acl is out of date: run gen_acl.py"
