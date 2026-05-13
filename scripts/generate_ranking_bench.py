"""Orchestrator: iterate worklist, fetch seed passages, dispatch Codex,
write silver entries to tests/fixtures/ranking_bench.jsonl.

Codex is invoked via subprocess against the locally-installed codex-cli
(0.130.0+) using `codex exec --output-last-message`. The --output-last-message
flag writes only the final agent message to a temp file, suitable for JSON
parsing (stdout from codex exec contains progress chrome).

For each chapter, up to N=5 substantive passages (passage_role='evidence',
char_count between 300 and 2000) are selected from the most content-rich
sections, then Codex is called once per passage with the template at
tests/fixtures/ranking_bench_generation_prompt.md.

Prompt handling note: the template includes a prose preamble before the <task>
tag (generation instructions). We keep it as-is and pass the full rendered
template to Codex; Codex reliably ignores the preamble and responds to the
<task> block.

Pool design: a single pool is opened in main() and passed into
fetch_seed_passages() to avoid repeated open/close overhead across 32 chapters.

Resumability: already_processed() reads existing JSONL entries and skips any
passage whose passage_id has already been used as a seed. Re-running after
interruption picks up exactly where it left off.

Parallelism: --concurrency N (default 4) runs up to N codex exec calls
concurrently via asyncio.Semaphore + asyncio.gather. An asyncio.Lock guards
the JSONL file writes so lines are never interleaved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

import asyncpg

from genereview_link.db.pool import create_pool

PROMPT_PATH = Path("tests/fixtures/ranking_bench_generation_prompt.md")
SEEDS_PER_CHAPTER = 5
CODEX_BIN = "codex"


async def fetch_seed_passages(
    pool: asyncpg.Pool, nbk_id: str
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select passage_id, chapter_section, heading_path, text, char_count
            from genereview_passages
            where nbk_id = $1
              and passage_role = 'evidence'
              and char_count between 300 and 2000
            order by char_count desc
            limit $2
            """,
            nbk_id,
            SEEDS_PER_CHAPTER,
        )
    return list(rows)


def render_prompt(template: str, chapter_title: str, row: dict) -> str:
    nbk_id = row["passage_id"].split(":")[0]
    return (
        template.replace("{NBK_ID}", nbk_id)
        .replace("{CHAPTER_TITLE}", chapter_title)
        .replace("{SECTION}", row["chapter_section"])
        .replace("{HEADING_PATH}", row["heading_path"] or "")
        .replace("{PASSAGE_ID}", row["passage_id"])
        .replace("{PASSAGE_TEXT}", row["text"])
    )


def _parse_codex_json(raw: str) -> dict:
    """Tolerantly extract a JSON object from a model response.

    Strategy:
      1. Try direct parse.
      2. Try stripping a leading/trailing markdown fence.
      3. Find the first '{' and balance braces.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Strip a fenced block.
    if raw.startswith("```"):
        body = raw.split("\n", 1)[1] if "\n" in raw else ""
        if body.endswith("```"):
            body = body[:-3]
        try:
            return json.loads(body.strip())
        except json.JSONDecodeError:
            pass
    # Find first { and balance braces.
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object found in codex output")
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start : i + 1])
    raise ValueError("unbalanced braces in codex output")


async def run_codex_async(prompt: str, timeout: float = 180.0) -> dict:
    """Async equivalent of run_codex via asyncio.create_subprocess_exec.

    Uses --output-last-message to capture only the final agent response,
    which is what we parse as JSON. Timeout is 180s to accommodate reasoning.
    """
    fd, out_path_str = tempfile.mkstemp(suffix=".txt")
    import os

    os.close(fd)
    out_path = Path(out_path_str)
    try:
        proc = await asyncio.create_subprocess_exec(
            CODEX_BIN,
            "exec",
            "-c",
            "model_reasoning_effort=medium",
            "--skip-git-repo-check",
            "--output-last-message",
            str(out_path),
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")), timeout=timeout
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"codex exec failed (rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')[:500]}"
            )
        raw = out_path.read_text()
    finally:
        out_path.unlink(missing_ok=True)
    return _parse_codex_json(raw)


def already_processed(jsonl_path: Path) -> set[str]:
    """Return the set of passage_ids already written to the output file."""
    if not jsonl_path.exists():
        return set()
    seen: set[str] = set()
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            seen.add(json.loads(line)["expected_top1_passage_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return seen


async def process_passage(
    chapter: dict,
    seed: dict,
    template: str,
    sem: asyncio.Semaphore,
    out_lock: asyncio.Lock,
    out_handle,  # type: ignore[type-arg]
) -> None:
    pid: str = seed["passage_id"]
    nbk_id: str = chapter["nbk_id"]
    prompt = render_prompt(template, chapter["title"], seed)
    async with sem:
        try:
            response = await run_codex_async(prompt)
        except Exception as e:
            print(f"  codex error on {pid}: {e}", flush=True)  # noqa: T201
            return

    if response.get("skip"):
        print(f"  skip {pid}: {response.get('reason')}", flush=True)  # noqa: T201
        return

    queries: list[dict] = response.get("queries", [])
    n_q = len(queries)
    async with out_lock:
        for q in queries:
            entry = {
                "query": q["query"],
                "expected_top1_passage_id": pid,
                "expected_top5_passage_ids": [pid],
                "status": "silver",
                "source": "codex_generated_2026-05-13",
                "intent": q.get("intent", "other"),
                "section": seed["chapter_section"],
                "chapter_nbk_id": nbk_id,
                "notes": (
                    f"Codex-generated from {pid}, "
                    f"style={q.get('style')}."
                ),
            }
            out_handle.write(json.dumps(entry) + "\n")
        out_handle.flush()
    print(f"  ok {pid}: {n_q} queries", flush=True)  # noqa: T201


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate silver ranking benchmark entries via Codex"
    )
    ap.add_argument(
        "--worklist",
        type=Path,
        default=Path("tests/fixtures/ranking_bench_worklist.json"),
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/ranking_bench.jsonl"),
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max parallel codex exec calls (default 4)",
    )
    args = ap.parse_args()

    worklist: list[dict] = json.loads(args.worklist.read_text())
    template: str = PROMPT_PATH.read_text()
    seen = already_processed(args.output)
    print(f"resuming: {len(seen)} passages already processed", flush=True)  # noqa: T201
    print(f"concurrency: {args.concurrency}", flush=True)  # noqa: T201

    pool = await create_pool()
    sem = asyncio.Semaphore(args.concurrency)
    out_lock = asyncio.Lock()
    try:
        # Build the full work list first (one Codex call per passage).
        work: list[tuple[dict, dict]] = []
        for chapter in worklist:
            nbk_id: str = chapter["nbk_id"]
            title: str = chapter["title"]
            print(f"chapter {nbk_id}: {title}", flush=True)  # noqa: T201
            seeds = await fetch_seed_passages(pool, nbk_id)
            for seed in seeds:
                pid: str = seed["passage_id"]
                if pid in seen:
                    print(f"  skip (already processed) {pid}", flush=True)  # noqa: T201
                    continue
                work.append((chapter, dict(seed)))
        print(f"work queue: {len(work)} passages", flush=True)  # noqa: T201

        with args.output.open("a") as out:
            tasks = [
                process_passage(chapter, seed, template, sem, out_lock, out)
                for chapter, seed in work
            ]
            await asyncio.gather(*tasks)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
