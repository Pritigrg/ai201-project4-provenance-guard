"""
Calibration harness for the two detection signals + combined scoring.

Runs BOTH signals on a set of deliberately chosen inputs (clearly AI, clearly
human, and two borderline cases), prints each signal score separately alongside
the combined ai_likelihood_score / classification / confidence, and reports where
the two signals agree vs. diverge.

Signal 1 (llm_ai_score) makes a live Groq call; if GROQ_API_KEY is absent it
degrades to the lexical fallback (flagged), exactly like the /submit endpoint.

    python tests/calibrate_signals.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from detector import (
    classify,
    combine_signals,
    confidence_for,
    lexical_repetition_score,
    llm_ai_score,
    sentence_rhythm_score,
)

load_dotenv()

CASES = [
    (
        "clearly AI (expect high)",
        "Artificial intelligence represents a transformative paradigm shift in modern society. "
        "It is important to note that while the benefits of AI are numerous, it is equally "
        "essential to consider the ethical implications. Furthermore, stakeholders across "
        "various sectors must collaborate to ensure responsible deployment.",
    ),
    (
        "clearly human (expect low)",
        "ok so i finally tried that new ramen place downtown and honestly? "
        "underwhelming. the broth was fine but they put WAY too much sodium in it and "
        "i was thirsty for like three hours after. my friend got the spicy version and "
        "said it was better. probably won't go back unless someone drags me there",
    ),
    (
        "borderline: formal human (mid-high)",
        "The relationship between monetary policy and asset price inflation has been "
        "extensively studied in the literature. Central banks face a fundamental tension "
        "between their mandate for price stability and the unintended consequences of "
        "prolonged low interest rates on equity and real estate valuations.",
    ),
    (
        "borderline: lightly edited AI (mid)",
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs — "
        "flexibility and no commute on one side, isolation and blurred work-life boundaries "
        "on the other. Studies show productivity varies widely by individual and role type.",
    ),
    # Two extreme anchors to demonstrate that all three labels are reachable.
    (
        "anchor: dense uniform AI (expect likely_ai)",
        "Organizations today must carefully evaluate their existing operational capabilities "
        "against a range of established industry benchmarks. The systematic integration of data "
        "driven methodologies consistently enables leaders to make more informed strategic "
        "decisions. Furthermore, sustained competitive advantage requires continuous adaptation "
        "to the constantly evolving conditions of modern markets. Ultimately, responsible "
        "stakeholders across various sectors must collaborate to ensure the effective deployment "
        "of these frameworks.",
    ),
    (
        "anchor: choppy varied human (expect likely_human)",
        "Missed the bus. Again. So I walked, which honestly wasn't the worst thing in the "
        "world because the weather was actually kind of nice for once. Got a coffee. "
        "Spilled half of it on my sleeve like an absolute clown. Whatever.",
    ),
]


def side(score: float) -> str:
    """Which side of the 0.50 midpoint a 0-1 signal leans."""
    if score > 0.55:
        return "AI-leaning"
    if score < 0.45:
        return "human-leaning"
    return "neutral"


def run() -> None:
    header = f"{'case':<38}{'S1 llm':>8}{'S2 rhythm':>11}{'combined':>10}{'label':>16}{'conf':>7}"
    print(header)
    print("-" * len(header))

    for name, text in CASES:
        llm = llm_ai_score(text)
        if llm["available"]:
            s1 = llm["score"]
            s1_src = "groq"
        else:
            s1 = lexical_repetition_score(text)
            s1_src = "fallback"

        s2 = sentence_rhythm_score(text)
        combined = combine_signals(s1, s2)
        label = classify(combined)
        conf = confidence_for(combined)

        agree = "AGREE" if side(s1) == side(s2) else "DIVERGE"
        print(
            f"{name:<38}{s1:>8.2f}{s2:>11.2f}{combined:>10.2f}{label:>16}{conf:>7.2f}"
            f"   [S1={s1_src}, S1 {side(s1)} / S2 {side(s2)} -> {agree}]"
        )


if __name__ == "__main__":
    run()
