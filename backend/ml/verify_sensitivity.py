"""
Sensitivity/ablation check for simulator calibration changes.
Read-only -- inspects an already-generated training_corpus.csv.
Checks:
1. Monotonic decrease in mean y as retry_count increases (candidate_action='retry')
2. Probability/outcome ranges are not collapsing to 0 or 1
3. Basic outcome distribution sanity by candidate_action
"""

from pathlib import Path
import pandas as pd

# Anchored on this file's location, not on the caller's cwd (see
# simulate_training_data.py for the same fix and rationale).
CORPUS_PATH = Path(__file__).resolve().parent / "data" / "training_corpus.csv"


def main():
    df = pd.read_csv(CORPUS_PATH)

    print("=== Mean y by retry_count (candidate_action='retry') ===")
    retry_df = df[df["candidate_action"] == "retry"]
    means = retry_df.groupby("retry_count")["y"].mean()
    print(means)

    diffs = means.diff().dropna()
    is_monotonic_decreasing = (diffs <= 1e-9).all()
    print(f"\nMonotonic non-increasing across retry_count: {is_monotonic_decreasing}")
    if not is_monotonic_decreasing:
        print("WARNING: retry_count effect is not monotonic. Review magnitudes.")

    print("\n=== Overall y distribution ===")
    print(df["y"].value_counts(normalize=True))

    print("\n=== Mean y by candidate_action ===")
    print(df.groupby("candidate_action")["y"].mean())

    print("\n=== Mean y by retry_count, for reminder/escalate (boundary penalty check) ===")
    boundary_df = df[df["candidate_action"].isin(["reminder", "escalate"])]
    print(boundary_df.groupby(["candidate_action", "retry_count"])["y"].mean())

    extreme_low = (df.groupby("candidate_action")["y"].mean() < 0.05).any()
    extreme_high = (df.groupby("candidate_action")["y"].mean() > 0.95).any()
    if extreme_low or extreme_high:
        print("WARNING: an action's outcome rate is near 0 or 1 -- check for collapse.")
    else:
        print("\nNo collapse to extreme probabilities detected.")


if __name__ == "__main__":
    main()