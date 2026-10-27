"""Build the example report shown on the homepage, so visitors see a real result instantly.

Usage (needs a real key; uses about 1 + questions x RUNS_PER_QUESTION Gemini calls):
    uv run --env-file .env python scripts/build_demo.py freecodecamp.org
    uv run --env-file .env python scripts/build_demo.py freecodecamp.org --questions questions.txt

Writes app/demo/demo_report.json. Commit that file and redeploy.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.citations import normalize_site  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.jobs import ReportRunner, default_deps  # noqa: E402
from app.main import DEMO_FILE  # noqa: E402
from app.storage import Storage  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("site")
    parser.add_argument("--questions", type=Path, help="text file, one question per line")
    parser.add_argument("--competitors", default="", help="comma separated")
    parser.add_argument("--out", type=Path, default=DEMO_FILE)
    args = parser.parse_args()

    settings = load_settings()
    if settings.demo_mode:
        print("GEMINI_API_KEY is not set. A demo report must use real answers.", file=sys.stderr)
        return 2
    settings = replace(settings, daily_call_budget=10_000, db_path=":memory:")
    base, domain = normalize_site(args.site)
    questions = args.questions.read_text().splitlines() if args.questions else []
    questions = [q.strip() for q in questions if q.strip()]
    competitors = [c.strip() for c in args.competitors.split(",") if c.strip()]

    runner = ReportRunner(default_deps(settings, Storage(":memory:")))
    report = runner.create(base, domain, questions, competitors)
    print(f"Building report for {domain}…")
    report = await runner.run(report.id)
    if report.status != "done":
        print(f"Failed: {report.error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report.model_dump_json(indent=1))
    print(f"Score {report.visibility_score}/100, {len(report.fixes)} fixes. Saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
