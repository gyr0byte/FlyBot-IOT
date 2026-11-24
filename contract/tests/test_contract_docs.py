"""Contract consistency: docs/message-contract.md and docs/schemas/ must agree.

Runs with every test run of the brain and the simulator (both have flybot_contract installed):
  - every schema is valid draft 2020-12, strict (unknown keys rejected) and pins its version;
  - every JSON example in the contract validates and matches the schema's own `examples`;
  - the section 1 table names the right schema and current `v` for every topic;
  - the section 4 permission table and the Mosquitto ACL text in section 4.1 grant the same rights;
  - every topic the dashboard may publish requires a `src` that accepts a tab name;
  - a list of deliberately invalid messages is rejected.
"""
from __future__ import annotations

import json
import re

import pytest
from jsonschema import Draft202012Validator

from flybot_contract import CONTRACT_MD, SCHEMA_DIR, TOPIC_SCHEMA

SCHEMAS = {p.name.replace(".schema.json", ""): json.loads(p.read_text(encoding="utf-8"))
           for p in sorted(SCHEMA_DIR.glob("*.schema.json"))}
V = {name: Draft202012Validator(s) for name, s in SCHEMAS.items()}
TEXT = CONTRACT_MD.read_text(encoding="utf-8")
USERS = ["esp32", "sim", "brain", "dashboard", "bot"]


def contract_examples() -> dict[str, list[dict]]:
    parts = re.split(r"^### 3\.\d+ `flybot/01/([a-z/]+)`", TEXT, flags=re.M)
    out: dict[str, list[dict]] = {}
    for topic, body in zip(parts[1::2], parts[2::2]):
        body = body.split("\n---\n")[0]
        for block in re.findall(r"```json\n(.*?)```", body, flags=re.S):
            for line in block.strip().splitlines():
                out.setdefault(topic, []).append(json.loads(line))
    return out


EXAMPLES = contract_examples()


def test_every_topic_has_a_schema_and_a_contract_section():
    assert set(TOPIC_SCHEMA.values()) == set(SCHEMAS)
    assert set(TOPIC_SCHEMA) == set(EXAMPLES)


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_strict_and_versioned(name):
    s = SCHEMAS[name]
    Draft202012Validator.check_schema(s)
    assert s["examples"], "schema has no examples"
    for ex in s["examples"]:
        assert V[name].is_valid(ex), ex
        assert not V[name].is_valid({**ex, "zz": 1}), "unknown key accepted"
        assert not V[name].is_valid({**ex, "v": ex["v"] + 1}), "v+1 accepted"
        if ex["v"] > 1:
            assert not V[name].is_valid({**ex, "v": ex["v"] - 1}), "v-1 accepted"


@pytest.mark.parametrize("topic", sorted(TOPIC_SCHEMA))
def test_contract_examples_validate_and_match_schema_examples(topic):
    name = TOPIC_SCHEMA[topic]
    for msg in EXAMPLES[topic]:
        errs = list(V[name].iter_errors(msg))
        assert not errs, f"{msg}: {errs[0].message}"
        if topic in ("sensors", "cmd/motor", "cmd/manual"):
            assert len(json.dumps(msg, separators=(",", ":"))) <= 128
    doc = sorted(json.dumps(m, sort_keys=True) for t, ms in EXAMPLES.items() if TOPIC_SCHEMA[t] == name for m in ms)
    sch = sorted(json.dumps(m, sort_keys=True) for m in SCHEMAS[name]["examples"])
    assert doc == sch


@pytest.mark.parametrize("topic", sorted(TOPIC_SCHEMA))
def test_section1_table_names_schema_and_current_version(topic):
    name = TOPIC_SCHEMA[topic]
    m = re.search(r"^\| `" + re.escape(topic) + r"` \|.*\| `" + name + r"` \| (\d+) \|$", TEXT, flags=re.M)
    assert m, f"section 1 row for {topic} missing or malformed"
    assert int(m.group(1)) == SCHEMAS[name]["properties"]["v"]["const"]


def _section4():
    sec4 = TEXT.split("## 4. MQTT users")[1].split("## 5.")[0]
    table = {}
    for m in re.finditer(r"^\| `([a-z/]+)` \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \| ([^|]*) \|$", sec4, flags=re.M):
        for u, cell in zip(USERS, m.groups()[1:]):
            table[(u, m.group(1))] = ("P" in cell, "S" in cell)
    acl, cur = {}, None
    for line in re.search(r"```text\n(.*?)```", sec4, flags=re.S).group(1).splitlines():
        w = line.split()
        if not w or w[0].startswith("#"):
            continue
        if w[0] == "user":
            cur = w[1]
            continue
        acl.setdefault(cur, []).append((w[1], w[2]))
    return table, acl


def test_permission_table_matches_acl_text():
    table, acl = _section4()
    topics = {t for _, t in table}
    assert topics == set(TOPIC_SCHEMA)

    def allowed(u, t):
        full, r, w = "flybot/01/" + t, False, False
        for mode, pat in acl.get(u, []):
            if pat == full or (pat.endswith("/#") and full.startswith(pat[:-1])):
                r |= mode in ("read", "readwrite")
                w |= mode in ("write", "readwrite")
        return w, r

    wrong = [(u, t, table[(u, t)], allowed(u, t)) for u in USERS for t in sorted(topics) if table[(u, t)] != allowed(u, t)]
    assert not wrong


def test_dashboard_published_topics_require_tab_src():
    table, _ = _section4()
    for t in [t for (u, t), (p, _) in table.items() if u == "dashboard" and p]:
        s = SCHEMAS[TOPIC_SCHEMA[t]]
        assert "src" in s["required"], t
        assert Draft202012Validator(s["properties"]["src"]).is_valid("dash-3fa2c1"), t


D = "dash-3fa2c1"
INVALID = [
    ("sensors", {"v":2,"seq":1,"up":1,"bid":2847193551,"ol":1.2,"or":0,"d":10,"lt":None,"bat":7}),
    ("sensors", {"v":2,"seq":1,"up":1,"bid":2847193551,"ol":0,"or":0,"d":1.0,"lt":None,"bat":7}),
    ("sensors", {"v":2,"seq":1,"up":1,"bid":2847193551,"ol":0,"or":0,"d":10,"bat":7}),
    ("sensors", {"v":2,"seq":-1,"up":1,"bid":2847193551,"ol":0,"or":0,"d":10,"lt":None,"bat":7}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"escape","ttc":1,"d":10,"lat":1,"resp":1,"hab":0,"why":"wifi_lost"}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"obstacle"}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"obstacle","d":10,"dur":5}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"button"}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"escape","ttc":1,"d":10,"lat":1}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"escape","ttc":1,"d":10,"resp":0.5,"hab":0.5}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"escape","ttc":1,"d":10,"lat":1,"resp":0,"hab":0.9}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"escape","ttc":1,"d":10,"lat":1,"resp":1.2,"hab":0}),
    ("events", {"v":3,"seq":1,"up":1,"bid":2847193551,"e":"obstacle","d":10,"hab":0.1}),
    ("status", {"v":3,"seq":0,"up":1,"bid":2847193551,"st":"offline","m":"auto"}),
    ("status", {"v":3,"seq":0,"up":1,"bid":2847193551,"st":"cloud"}),
    ("status", {"v":3,"seq":0,"up":1,"bid":2847193551,"st":"cloud","m":"auto","rssi":5,"heap":1,"bat":7,"rtt":None,"crtt":None,"fw":"1","hab":0}),
    ("status", {"v":3,"seq":0,"up":1,"bid":2847193551,"st":"cloud","m":"auto","rssi":-5,"heap":1,"bat":7,"rtt":None,"crtt":None,"fw":"1"}),
    ("status", {"v":3,"seq":0,"up":1,"bid":2847193551,"st":"offline","hab":0}),
    ("cmd-motor", {"v":1,"seq":1,"up":1,"l":1.5,"r":0,"ttl":300,"rs":0}),
    ("cmd-motor", {"v":1,"seq":1,"up":1,"l":0,"r":0,"ttl":10,"rs":0}),
    ("cmd-motor", {"v":1,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0,"src":D}),
    ("cmd-manual", {"v":2,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0}),
    ("cmd-manual", {"v":2,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0,"src":"dash"}),
    ("cmd-manual", {"v":2,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0,"src":"bot"}),
    ("cmd-manual", {"v":2,"seq":1,"up":1,"l":-1.1,"r":0,"ttl":300,"rs":0,"src":D}),
    ("cmd-manual", {"v":1,"seq":1,"up":1,"l":0,"r":0,"ttl":300,"rs":0,"src":D}),
    ("cmd-mode", {"v":2,"seq":1,"up":1,"m":"sleep","src":D}),
    ("cmd-mode", {"v":2,"seq":1,"up":1,"m":"auto","src":"brain"}),
    ("cmd-mode", {"v":2,"seq":1,"up":1,"m":"auto","src":"dash"}),
    ("cmd-mode", {"v":2,"seq":1,"up":1,"m":"auto","src":"dash-3FA2C1"}),
    ("cmd-mode", {"v":2,"seq":1,"up":1,"m":"auto","src":"dash-3fa2c"}),
    ("cmd-mode", {"v":2,"seq":1,"up":1,"m":"auto","src":"dash-3fa2c1x"}),
    ("cmd-mode", {"v":2,"seq":1,"up":1,"m":"auto","src":"btn"}),
    ("cmd-sleep", {"v":1,"seq":1,"up":1,"s":"yes","why":"dark"}),
    ("reward", {"v":3,"seq":1,"up":1,"r":0,"src":"bot"}),
    ("reward", {"v":3,"seq":1,"up":1,"r":1.0001,"src":"bot"}),
    ("reward", {"v":3,"seq":1,"up":1,"r":True,"src":"bot"}),
    ("reward", {"v":3,"seq":1,"up":1,"r":1,"src":"dash"}),
    ("reward", {"v":3,"seq":1,"up":1,"r":1}),
    ("brain-sleep", {"v":2,"seq":1,"up":1,"s":True}),
    ("brain-sleep", {"v":2,"seq":1,"up":1,"s":True,"src":"btn"}),
    ("brain-sleep", {"v":2,"seq":1,"up":1,"s":True,"src":"dash"}),
    ("brain-sleep", {"v":2,"seq":1,"up":1,"s":"on","src":D}),
    ("brain-status", {"v":2,"seq":0,"up":1,"st":"offline","ctrl":"neural"}),
    ("brain-status", {"v":2,"seq":0,"up":1,"st":"online"}),
    ("brain-status", {"v":2,"seq":0,"up":1,"st":"online","ctrl":"neural","tick":1,"ovr":0}),
    ("brain-status", {"v":2,"seq":0,"up":1,"st":"online","ctrl":"neural","tick":1,"ovr":0,"sens":"late"}),
    ("brain-status", {"v":2,"seq":0,"up":1,"st":"offline","sens":"stale"}),
    ("brain-weights", {"v":1,"seq":0,"up":1,"n":2,"sc":0,"ap":[1,2],"av":[1,2]}),
    ("brain-weights", {"v":1,"seq":0,"up":1,"n":2,"sc":1,"ap":[1,256],"av":[1,2]}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"save","tgt":"mb"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"lesion","tgt":"mb"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"param","k":"Bad Name","val":1}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"param","k":"x","val":""}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"reset"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"sleep","s":True}),
    ("brain-control", {"v":4,"seq":1,"up":1,"c":"load"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":"bot","c":"load"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":"dash","c":"load"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"state"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"state","hun":1.5}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"state","hun":0.5,"what":"all"}),
    ("brain-control", {"v":4,"seq":1,"up":1,"src":D,"c":"reset","what":"all","fear":0.1}),
    ("brain-state", {"v":5,"seq":0,"up":1,"hun":0,"fear":0,"hab":0,"slp":False,"dec":"seek","why":[],"spk":{"xx":1},"les":[],"pref":{},"rs":None,"ack":None}),
    ("brain-state", {"v":5,"seq":0,"up":1,"hun":0,"fear":0,"hab":0,"slp":False,"dec":"seek","why":["a","b","c","d"],"spk":{},"les":[],"pref":{},"rs":None,"ack":None}),
    ("brain-state", {"v":5,"seq":0,"up":1,"hun":0,"fear":0,"hab":0,"slp":False,"dec":"seek","why":[],"spk":{},"les":["mb","mb"],"pref":{},"rs":None,"ack":None}),
    ("brain-state", {"v":5,"seq":0,"up":1,"hun":0,"fear":0,"hab":0,"slp":False,"dec":"seek","why":[],"spk":{},"les":[],"pref":{},"rs":None,"ack":{"seq":1,"ok":True}}),
    ("brain-state", {"v":5,"seq":0,"up":1,"hun":0,"fear":0,"hab":0,"slp":False,"dec":"seek","why":[],"spk":{},"les":[],"pref":{},"rs":None,"ack":{"src":"bot","seq":1,"ok":True}}),
    ("brain-state", {"v":5,"seq":0,"up":1,"hun":0,"fear":0,"hab":0,"slp":False,"dec":"seek","why":[],"spk":{},"les":[],"pref":{},"rs":None,"ack":{"src":D,"seq":1}}),
    ("pong", {"v":1,"seq":1,"up":1,"ps":1}),
    ("sensors", {"v":2,"seq":1,"up":1,"ol":0,"or":0,"d":10,"lt":None,"bat":7}),
    ("sensors", {"v":2,"seq":1,"up":1,"bid":0,"ol":0,"or":0,"d":10,"lt":None,"bat":7}),
    ("sensors", {"v":2,"seq":1,"up":1,"bid":4294967296,"ol":0,"or":0,"d":10,"lt":None,"bat":7}),
    ("ping", {"v":2,"seq":1,"up":1}),
    ("ping", {"v":1,"seq":1,"up":1,"bid":2847193551}),
    ("status", {"v":3,"seq":0,"up":1,"st":"offline"}),
    ("events", {"v":3,"seq":0,"up":1,"e":"boot","rst":"power_on","fw":"1"}),
    ("reward", {"v":3,"seq":1,"up":1,"r":1,"src":"btn"}),
    ("reward", {"v":3,"seq":1,"up":1,"bid":2847193551,"r":1,"src":"bot"}),
    ("reward", {"v":3,"seq":1,"up":1,"bid":2847193551,"r":1,"src":"dash-3fa2c1"}),
]


@pytest.mark.parametrize("name,msg", INVALID, ids=[f"{n}-{i}" for i, (n, _) in enumerate(INVALID)])
def test_invalid_message_is_rejected(name, msg):
    assert not V[name].is_valid(msg)
