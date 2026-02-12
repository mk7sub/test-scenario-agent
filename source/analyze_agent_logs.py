from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ollama_http import DEFAULT_BASE_URL, generate as ollama_generate_http
from prompts import build_analysis_prompt


def parse_label(response_text: str) -> str:
    first_line = response_text.strip().splitlines()[0] if response_text.strip() else ""
    upper = first_line.upper()
    if "PASS" in upper:
        return "PASS"
    if "FAIL" in upper:
        return "FAIL"
    return "UNKNOWN"


def load_agent_records(path: Path, limit: Optional[int]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
            if limit is not None and len(records) >= limit:
                break
    return records


def analyze_log(
    log_text: str,
    *,
    model: str,
    base_url: str,
    temperature: float,
    num_ctx: int,
) -> Dict[str, Any]:
    prompt = build_analysis_prompt(log_text)
    response = ollama_generate_http(
        model=model,
        prompt=prompt,
        options={"temperature": temperature, "num_ctx": num_ctx},
        base_url=base_url,
        stream=False,
    )
    response_text = response.get("response", "")
    return {
        "label": parse_label(response_text),
        "response_text": response_text,
        "eval_count": response.get("eval_count"),
        "eval_duration": response.get("eval_duration"),
    }


def run(args: argparse.Namespace) -> None:
    jsonl_path = Path(args.agent_jsonl)
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Agent JSONL not found: {jsonl_path}")

    records = load_agent_records(jsonl_path, args.limit)
    results: List[Dict[str, Any]] = []

    for record in records:
        status = record.get("status", "ok")
        case_id = record.get("case_id")
        name = record.get("name")
        log_text = record.get("generated_log")
        expected = (record.get("expected_label") or "").upper()
        agent_verdict = (record.get("agent_verdict") or "").upper()

        if status != "ok" or not log_text:
            results.append(
                {
                    "CaseID": case_id,
                    "Name": name,
                    "ExpectedLabel": expected,
                    "AgentVerdict": agent_verdict,
                    "AnalyzerLabel": "SKIPPED",
                    "MatchesAgent": None,
                    "MatchesExpected": None,
                    "AnalyzerReason": record.get("error"),
                    "EvalCount": None,
                    "EvalDurationNs": None,
                    "Status": status,
                }
            )
            continue

        print(f"[ANALYZE] Case {case_id} ...", flush=True)

        try:
            analysis = analyze_log(
                log_text,
                model=args.model,
                base_url=args.base_url,
                temperature=args.temperature,
                num_ctx=args.num_ctx,
            )
            analyzer_label = analysis["label"]
            matches_expected = (
                analyzer_label == expected if expected in {"PASS", "FAIL"} else None
            )
            matches_agent = (
                analyzer_label == agent_verdict if agent_verdict in {"PASS", "FAIL"} else None
            )
            results.append(
                {
                    "CaseID": case_id,
                    "Name": name,
                    "ExpectedLabel": expected,
                    "AgentVerdict": agent_verdict,
                    "AnalyzerLabel": analyzer_label,
                    "MatchesAgent": matches_agent,
                    "MatchesExpected": matches_expected,
                    "AnalyzerReason": analysis["response_text"],
                    "EvalCount": analysis["eval_count"],
                    "EvalDurationNs": analysis["eval_duration"],
                    "Status": "ok",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "CaseID": case_id,
                    "Name": name,
                    "ExpectedLabel": expected,
                    "AgentVerdict": agent_verdict,
                    "AnalyzerLabel": "ERROR",
                    "MatchesAgent": None,
                    "MatchesExpected": None,
                    "AnalyzerReason": str(exc),
                    "EvalCount": None,
                    "EvalDurationNs": None,
                    "Status": "error",
                }
            )
            print(f"[ANALYZE] Case {case_id} failed: {exc}", flush=True)

    df = pd.DataFrame(results)
    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"[ANALYZE] Results saved to {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze agent-generated logs with Ollama")
    parser.add_argument("--agent-jsonl", default=os.path.join("./log", "agent_runs.jsonl"))
    parser.add_argument("--output-csv", default=os.path.join("./log", "agent_analysis_results.csv"))
    parser.add_argument("--model", default="phi4-mini-latest-16384")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--limit", type=int)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
