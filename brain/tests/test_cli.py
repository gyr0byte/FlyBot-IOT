"""The brain command line (flybot_brain.__main__)."""
import argparse

from flybot_brain import __main__ as cli


def args(**kw):
    base = dict(config=None, controller=None, reset_on_reboot=False, seed=None, no_persistence=False)
    return argparse.Namespace(**{**base, **kw})


def build(tmp_path, monkeypatch, **kw):
    """build() with snapshots in tmp_path and dummy broker settings; counts snapshot loads."""
    real = cli.load_config

    def load_config(path=None):
        cfg = real(path)
        cfg.persistence.dir = str(tmp_path)
        return cfg

    loads = []
    monkeypatch.setattr(cli, "load_config", load_config)
    monkeypatch.setattr(cli.MqttSettings, "from_env",
                        classmethod(lambda cls: cls("127.0.0.1", 1883, "brain", "x", "01")))
    monkeypatch.setattr(cli.Brain, "load", lambda self, name: loads.append(name) or True)
    brain, _ = cli.build(args(**kw))
    return brain, loads


def test_default_loads_latest_snapshot(tmp_path, monkeypatch):
    brain, loads = build(tmp_path, monkeypatch)
    assert loads == ["latest"] and brain.cfg.persistence.save_on_exit


def test_no_persistence_is_a_naive_brain_that_saves_nothing(tmp_path, monkeypatch):
    brain, loads = build(tmp_path, monkeypatch, no_persistence=True, controller="neural", seed=7)
    assert loads == []
    p = brain.cfg.persistence
    assert not p.load_on_start and not p.save_on_exit
    assert (brain.cfg.controller, brain.cfg.seed) == ("neural", 7)
