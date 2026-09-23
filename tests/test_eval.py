"""The eval gate, kept in the test suite so a grounding hole fails CI."""
from eval.run_eval import main


def test_stub_eval_has_no_assumed_values(tmp_path):
    report = main(["--provider", "stub", "--out", str(tmp_path / "r.json")])
    assert report["conversations"] >= 20
    assert report["outcomes"]["assumed"] == 0


def test_switching_the_guard_off_is_caught_by_the_eval(tmp_path):
    report = main(["--provider", "stub", "--no-grounding", "--out", str(tmp_path / "r.json")])
    assert report["outcomes"]["assumed"] > 0
