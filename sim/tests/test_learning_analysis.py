"""Learning-experiment analysis (flybot_sim.learning): conditions, animals, PI, sign test, report."""
import pytest

from flybot_sim.config import check_training_starts, load_scenario, source_by_label
from flybot_sim.learning import (CONDITIONS, animal_scenario, preference_index, report, sign_test,
                                 summarize_phase, weight_means)


def test_preference_index():
    assert preference_index(6, 2) == pytest.approx(0.5)
    assert preference_index(0, 3) == -1.0
    assert preference_index(0, 0) is None


def test_sign_test_two_sided_exact():
    assert sign_test(10, 0) == pytest.approx(2 / 1024)
    assert sign_test(5, 5) == 1.0
    assert sign_test(8, 2) == pytest.approx(2 * (1 + 10 + 45) / 1024)
    assert sign_test(0, 0) == 1.0


def test_summarize_phase_counts_labels_timeouts_and_rewards():
    rows = [{"reached_label": "lit", "reward_sent": -1}, {"reached_label": "dark", "reward_sent": None},
            {"reached_label": None, "reward_sent": None}, {"reached_label": "lit", "reward_sent": ""}]
    s = summarize_phase(rows)
    assert (s.n, s.lit, s.dark, s.timeout, s.rewards) == (4, 2, 1, 1, 1)
    assert s.pi == pytest.approx(1 / 3) and s.rate("timeout") == 0.25


@pytest.mark.parametrize("condition", list(CONDITIONS))
def test_animals_alternate_sides_and_only_training_is_reinforced(condition):
    base = load_scenario("learning")
    for k in range(4):
        sc, mirrored = animal_scenario(base, condition, k)
        assert mirrored == (k % 2 == 1)
        assert (source_by_label(sc, "lit").x > sc.arena.width_m / 2) == mirrored
        assert (sc.light.patches[0].x > sc.arena.width_m / 2) == mirrored
        assert check_training_starts(sc) == []
        assert sc.seed == base.seed + 1000 * k
        assert sc.experiment.reward_delay_s == CONDITIONS[condition]["delay_s"]
        for ph in sc.experiment.phases:
            assert ph.rewards == (CONDITIONS[condition]["rewards"] if ph.name == "train" else {})
    assert base.experiment.phases[1].rewards == {"lit": -1}          # the base scenario is untouched


def test_weight_means_scale_to_w_max():
    assert weight_means({"n": 2, "sc": 1.0, "ap": [255, 0], "av": [255, 255]}) == (0.5, 1.0)
    assert weight_means(None) == (None, None)


def test_report_pairs_pre_and_post_per_animal():
    def rec(c, a, ph, lit, dark, to):
        pi = preference_index(lit, dark)
        return {"condition": c, "animal": str(a), "mirrored": str(a % 2 == 1), "brain_seed": str(a + 1),
                "phase": ph, "n": str(lit + dark + to), "lit": str(lit), "dark": str(dark), "timeout": str(to),
                "rewards": "0", "pi": "" if pi is None else str(pi), "pref_lt": "", "pref_odr": "0.1",
                "w_ap": "0.3", "w_av": "0.3"}
    recs = []
    for a in range(3):
        recs += [rec("punish_lit", a, "pre", 4, 4, 0), rec("punish_lit", a, "train", 15, 0, 0),
                 rec("punish_lit", a, "post", 1, 6, 1)]
    text = report(recs)
    row = next(l for l in text.splitlines() if l.startswith("| punish_lit | PI down"))
    assert "0 / 3 / 0" in row and "| 0.250 |" in row
