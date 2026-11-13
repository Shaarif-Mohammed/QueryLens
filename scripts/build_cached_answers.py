"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Build the saved answers for the quick insights

    python scripts/build_cached_answers.py                      # every quick insight
    python scripts/build_cached_answers.py "Which states have the most revenue?"   # only these

Runs each quick-insight question through the full live pipeline (SQL, safety checks,
warehouse, explanation) exactly as the interface would, and saves the result to
metadata/cached_answers.json. A prepared follow-up is run after its own question, with
that question as context.

An answer is saved only if it is trustworthy as shown: answered with every figure in
its summary matched to the result and no overstated wording, or refused with a
suggested alternative. Anything else is reported and left out, so it runs live
instead. Answers already in the file for other questions are kept.

Each question is one paid model call plus one for the explanation (about USD 0.01).
"""

import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "metadata").is_dir())
sys.path.insert(0, str(ROOT))

from app.answers import CACHE_PATH, EXAMPLES, Answer, AnswerCache, from_pipeline, normalise  # noqa: E402
from app.pipeline import answer_question, explain_answer  # noqa: E402


def problems(answer: Answer) -> list[str]:
    """Why an answer should not be saved; empty when it can be."""
    if answer.status == "cannot_answer":
        return [] if answer.closest_alternative else ["refused without a suggested alternative"]
    if not answer.ok:
        return [f"{answer.status}: {answer.message}"]
    e = answer.explanation
    if e is None or not e.ok:
        return ["explanation failed: " + (e.error if e else "missing")]
    found = []
    if e.ungrounded_numbers:
        found.append(f"figures not matched to the result: {e.ungrounded_numbers}")
    if e.banned_terms:
        found.append(f"overstated wording: {e.banned_terms}")
    return found


def run(question: str, previous: dict | None) -> Answer:
    result = answer_question(question, previous)
    explain_answer(result)
    return from_pipeline(result, previous)


def report(answer: Answer, issues: list[str]) -> None:
    label = f"{answer.question}" + (f"  (after: {answer.follow_up_of})" if answer.follow_up_of else "")
    print("=" * 100)
    print(("SAVED    " if not issues else "SKIPPED  ") + label)
    print(f"  status: {answer.status} | rows: {len(answer.dataframe) if answer.dataframe is not None else '-'}"
          f" | {sum(answer.timings.values()):.1f}s")
    if answer.explanation and answer.explanation.ok:
        print(f"  headline: {answer.explanation.headline}")
    for follow_up in answer.follow_ups:
        print(f"  follow-up: {follow_up}")
    if answer.closest_alternative:
        print(f"  alternative: {answer.closest_alternative}")
    for issue in issues:
        print(f"  !! {issue}")


def main() -> int:
    wanted = {normalise(q) for q in sys.argv[1:]}
    unknown = wanted - {normalise(e.question) for e in EXAMPLES}
    if unknown:
        print(f"Not a quick-insight question: {sorted(unknown)}. "
              "Quick insights are listed in EXAMPLES in app/answers.py.")
        return 2
    cache = AnswerCache.load(CACHE_PATH)
    saved = skipped = 0
    for example in EXAMPLES:
        if wanted and normalise(example.question) not in wanted:
            continue
        answer = run(example.question, None)
        issues = problems(answer)
        report(answer, issues)
        if issues:
            skipped += 1
            continue
        cache.put(answer)
        saved += 1
        if example.follow_up and answer.ok:
            follow = run(example.follow_up, answer.follow_up_context())
            follow_issues = problems(follow)
            report(follow, follow_issues)
            if follow_issues:
                skipped += 1
            else:
                cache.put(follow)
                saved += 1
    cache.save(CACHE_PATH)
    print("=" * 100)
    print(f"Saved {saved}, skipped {skipped}. {len(cache)} answers in {CACHE_PATH.relative_to(ROOT)}.")
    if skipped:
        print("Re-run the skipped questions by name once the problem is understood.")
    return 0 if not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
