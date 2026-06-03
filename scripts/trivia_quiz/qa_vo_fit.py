"""QA: measure real Piper VO durations per riddle round and report the tempo
each [start,end] window would need (mirrors build.py VO assembly + audio.py
fit_to_window). Read-only — generates throwaway WAVs in a temp dir, prints a
report, deletes them. Run: .venv/bin/python scripts/trivia_quiz/qa_vo_fit.py 7 8 9 ...
"""
import sys, subprocess, tempfile, json
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[2]
PIPER_MODEL = REPO / ".piper_voices" / "en_US-ryan-high.onnx"

# Windows from build.py build_script() — questions read aloud + short answer lines.
WINDOWS = {
    "q1_question": (0.10, 2.80), "q1_answer": (4.50, 6.30),
    "q2_question": (7.80, 10.50), "q2_answer": (12.70, 14.50),
    "q3_question": (16.20, 18.90), "q3_answer": (20.60, 22.40),
}


def spoken_answer(ans: str) -> str:
    return ans.split(")", 1)[-1].strip() if ")" in ans else ans


def spoken_q(text: str) -> str:
    return text.strip().rstrip("?") + "?"


def piper(text: str, out: Path):
    subprocess.run([sys.executable, "-m", "piper", "--model", str(PIPER_MODEL),
                    "--output_file", str(out)],
                   input=text.encode(), check=True, capture_output=True)


def dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def analyze_round(slug: str, tmp: Path):
    row = yaml.safe_load((REPO / "projects/trivia-quiz" / slug / "inputs/quiz_row.yaml").read_text())
    lines = {
        "q1_question": spoken_q(row["q1"]["question"]),
        "q1_answer": f"The answer is {spoken_answer(row['q1']['answer'])}.",
        "q2_question": spoken_q(row["q2"]["question"]),
        "q2_answer": f"It's {spoken_answer(row['q2']['answer'])}!",
        "q3_question": spoken_q(row["q3"]["question"]),
        "q3_answer": f"The correct answer is {spoken_answer(row['q3']['answer'])}.",
    }
    rows = []
    for lid, text in lines.items():
        start, end = WINDOWS[lid]
        win = end - start
        wav = tmp / f"{slug}_{lid}.wav"
        piper(text, wav)
        d = dur(wav)
        ratio = d / win
        tempo = min(1.2, ratio) if ratio > 1.0 else 1.0
        clipped = ratio > 1.2
        rows.append((lid, len(text), d, win, ratio, tempo, clipped, text))
        wav.unlink(missing_ok=True)
    return rows


def main():
    slugs = [f"riddles-round-{n}" for n in sys.argv[1:]] if len(sys.argv) > 1 else \
            [f"riddles-round-{n}" for n in range(7, 21)]
    worst = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for slug in slugs:
            print(f"\n=== {slug} ===")
            print(f"  {'line':<12} {'chars':>5} {'spoken':>7} {'window':>7} {'ratio':>6} {'tempo':>6}  status")
            for lid, n, d, win, ratio, tempo, clipped, text in analyze_round(slug, tmp):
                flag = "CLIP/TRUNCATE" if clipped else ("rushed" if tempo > 1.1 else ("tight" if tempo > 1.0 else "ok"))
                if tempo > 1.1 or clipped:
                    worst.append((slug, lid, ratio, text))
                print(f"  {lid:<12} {n:>5} {d:>6.2f}s {win:>6.2f}s {ratio:>6.2f} {tempo:>6.2f}  {flag}")
    print("\n\n========== FLAGGED (tempo>1.1 or truncated) ==========")
    if not worst:
        print("  none — all lines fit comfortably (tempo <=1.1x)")
    for slug, lid, ratio, text in sorted(worst, key=lambda x: -x[2]):
        print(f"  {slug:<20} {lid:<12} needs {ratio:.2f}x  | {text!r}")


if __name__ == "__main__":
    main()
