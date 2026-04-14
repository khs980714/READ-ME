"""
질문 분류기 정확도 테스트

사용법:
    docker exec read-me-fastapi_server-1 python tests/run_classifier_test.py

결과 파일:
    tests/classifier_results.md
    tests/classifier_results.csv
"""

import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 모듈 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from chains.classifier import classify_question

TEST_CASES_PATH = Path(__file__).parent / "classifier_test_cases.json"
RESULTS_MD_PATH = Path(__file__).parent / "classifier_results.md"
RESULTS_CSV_PATH = Path(__file__).parent / "classifier_results.csv"

VALID_TYPES = [
    "keyword_search",
    "specific_search",
    "goal_oriented",
    "career_certification",
    "level_based",
    "out_of_scope",
]

TYPE_LABEL = {
    "keyword_search":       "도서 조회",
    "specific_search":      "특정 기술 추천",
    "goal_oriented":        "진로·목적 기반",
    "career_certification": "자격증·취업",
    "level_based":          "수준 기반",
    "out_of_scope":         "범위 외",
}


async def run_tests(test_cases: list[dict]) -> list[dict]:
    """모든 테스트 케이스에 대해 분류기를 실행하고 결과 반환."""
    results = []
    total = len(test_cases)
    for i, tc in enumerate(test_cases, 1):
        question = tc["question"]
        expected = tc["expected"]
        try:
            actual = await classify_question(question)
        except Exception as e:
            actual = f"ERROR: {e}"
        passed = actual == expected
        results.append({
            "no":       i,
            "question": question,
            "expected": expected,
            "actual":   actual,
            "passed":   passed,
        })
        status = "✅" if passed else "❌"
        print(f"[{i:>3}/{total}] {status} {question[:40]:<40}  기대:{expected:<22} 실제:{actual}")
    return results


def build_summary(results: list[dict]) -> dict:
    """유형별 정확도 요약 계산."""
    summary = {t: {"total": 0, "correct": 0} for t in VALID_TYPES}
    for r in results:
        t = r["expected"]
        if t in summary:
            summary[t]["total"] += 1
            if r["passed"]:
                summary[t]["correct"] += 1
    return summary


def save_markdown(results: list[dict], summary: dict, run_at: str):
    """마크다운 결과 파일 저장."""
    total = len(results)
    total_correct = sum(1 for r in results if r["passed"])
    overall_acc = total_correct / total * 100 if total else 0

    lines = [
        "# 질문 분류기 테스트 결과\n",
        f"실행 일시: {run_at}  \n",
        f"전체 정확도: **{total_correct}/{total} ({overall_acc:.1f}%)**\n",
        "\n## 유형별 정확도\n",
        "| 유형 | 분류 키 | 정답 | 전체 | 정확도 |",
        "|---|---|---|---|---|",
    ]
    for t in VALID_TYPES:
        s = summary[t]
        acc = s["correct"] / s["total"] * 100 if s["total"] else 0
        lines.append(
            f"| {TYPE_LABEL[t]} | `{t}` | {s['correct']} | {s['total']} | {acc:.1f}% |"
        )

    lines += ["\n## 전체 테스트 케이스\n"]

    for t in VALID_TYPES:
        group = [r for r in results if r["expected"] == t]
        if not group:
            continue
        lines.append(f"\n### {TYPE_LABEL[t]} (`{t}`)\n")
        lines += [
            "| # | 질문 | 원하는 결과 | 실제 결과 | 통과 |",
            "|---|---|---|---|---|",
        ]
        for r in group:
            mark = "✅" if r["passed"] else "❌"
            actual_disp = f"`{r['actual']}`" if not r["actual"].startswith("ERROR") else r["actual"]
            lines.append(
                f"| {r['no']} | {r['question']} | `{r['expected']}` | {actual_disp} | {mark} |"
            )

    RESULTS_MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ MD 저장 완료: {RESULTS_MD_PATH}")


def save_csv(results: list[dict], run_at: str):
    """CSV 결과 파일 저장."""
    with RESULTS_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["no", "question", "expected", "actual", "passed", "run_at"],
        )
        writer.writeheader()
        for r in results:
            writer.writerow({**r, "passed": "O" if r["passed"] else "X", "run_at": run_at})
    print(f"✅ CSV 저장 완료: {RESULTS_CSV_PATH}")


async def main():
    print("=" * 60)
    print("  READ:ME 질문 분류기 정확도 테스트")
    print("=" * 60)

    test_cases = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))
    print(f"총 {len(test_cases)}개 테스트 케이스 로드\n")

    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = await run_tests(test_cases)
    summary = build_summary(results)

    total = len(results)
    total_correct = sum(1 for r in results if r["passed"])
    print(f"\n{'=' * 60}")
    print(f"  전체 정확도: {total_correct}/{total} ({total_correct/total*100:.1f}%)")
    print("  유형별 정확도:")
    for t in VALID_TYPES:
        s = summary[t]
        acc = s["correct"] / s["total"] * 100 if s["total"] else 0
        bar = "█" * int(acc / 5) + "░" * (20 - int(acc / 5))
        print(f"    {TYPE_LABEL[t]:<16} {bar} {acc:5.1f}%  ({s['correct']}/{s['total']})")
    print("=" * 60)

    save_markdown(results, summary, run_at)
    save_csv(results, run_at)


if __name__ == "__main__":
    asyncio.run(main())
