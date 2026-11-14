"""End-to-end demo for the Data Pipeline Testing Platform.

Run from the project root with PYTHONPATH=src:
    PYTHONPATH=src python scripts/demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipetest.demo import run_demo  # noqa: E402

if __name__ == "__main__":
    result = run_demo()
    assert result["clean_gate_passed"], "clean run should pass its gate"
    assert result["bad_gate_blocked"], "corrupted run should be blocked by the gate"
    assert result["replay_deterministic"], "deterministic pipeline replay failed"
    assert result["diff_total"] == 0, "replayed output should match golden exactly"
    assert result["nondeterministic_detected"], "nondeterministic pipeline not caught"
    assert result["flaky_detected"], "flaky test not detected"
    assert result["exit_code_bad_run"] == 1, "blocked run should exit 1"
    print("\nDemo complete: all checks passed.")
