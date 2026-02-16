from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from analyze_agent_logs import build_parser as build_analyzer_parser
from analyze_agent_logs import run as run_analyzer
from config_loader import get_value

TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
CONFIG_SECTION = "test_scenario_agent"
BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = BASE_DIR.parent
RESULTS_DIR = BASE_DIR / "results"

DEFAULT_SCENARIO = os.path.join("./scenario", "normal_flow.yaml")
DEFAULT_JSONL = os.path.join("./log", "agent_runs.jsonl")
DEFAULT_LOG_DIR = os.path.join("./log", "agent_cases")


def cfg(key: str, fallback: Any) -> Any:
    return get_value(CONFIG_SECTION, key, fallback)


def queue_reset_config() -> Dict[str, Any]:
    raw = get_value(CONFIG_SECTION, "queue_reset", {})
    return raw if isinstance(raw, dict) else {}


def pick_option(arg_value: Optional[str], key: str, fallback: str) -> Tuple[str, bool]:
    if arg_value is not None:
        return arg_value, False
    return str(cfg(key, fallback)), True


def should_use_base(path_value: str, from_config: bool) -> bool:
    if not from_config:
        return False
    normalized = path_value.strip().replace("\\", "/")
    return normalized.startswith("./")


def resolve_path(raw: str, *, prefer_base: bool) -> Path:
    path = Path(str(raw))
    if path.is_absolute():
        return path
    base = BASE_DIR if prefer_base else Path.cwd()
    return (base / path).resolve()


def copy_queue_template(queue_path_raw: str, template_path_raw: str) -> Tuple[Path, Path]:
    queue_path = resolve_path(str(queue_path_raw), prefer_base=True)
    template_path = resolve_path(str(template_path_raw), prefer_base=True)
    if not template_path.exists():
        raise FileNotFoundError(f"queue_reset テンプレートが見つかりません: {template_path}")
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path, queue_path)
    return queue_path, template_path


def maybe_reset_queue() -> None:
    settings = queue_reset_config()
    queue_path_raw = settings.get("queue_path")
    template_path_raw = settings.get("template")
    if not queue_path_raw or not template_path_raw:
        print("[AGENT] queue_reset の queue_path または template が設定されていません", flush=True)
        return
    try:
        queue_path, template_path = copy_queue_template(queue_path_raw, template_path_raw)
    except FileNotFoundError as exc:
        print(f"[AGENT] {exc}", flush=True)
        return
    print(f"[AGENT] queue.json を初期化しました: {queue_path} (template={template_path})", flush=True)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()
    return slug or "case"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)
        fp.write("\n")


def parse_iso_datetime(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"日時の解析に失敗しました: {value}") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def determine_base_start(scenario: Dict[str, Any], override: Optional[str]) -> datetime:
    if override:
        return parse_iso_datetime(override)
    if scenario.get("start_at"):
        return parse_iso_datetime(str(scenario["start_at"]))
    return datetime.now()


def load_scenario(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp)
    if not isinstance(data, dict):
        raise ValueError("シナリオファイルの形式が正しくありません")
    if not isinstance(data.get("events"), list) or not data["events"]:
        raise ValueError("events セクションに1件以上のイベントを記述してください")
    return data


def prepare_events(scenario: Dict[str, Any], base_start: datetime) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for idx, raw_event in enumerate(scenario.get("events", []), start=1):
        if not isinstance(raw_event, dict):
            raise ValueError(f"イベント{idx}の形式が正しくありません")

        when: Optional[datetime] = None
        if "after_seconds" in raw_event:
            try:
                offset = float(raw_event["after_seconds"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"イベント{idx}のafter_secondsが不正です") from exc
            when = base_start + timedelta(seconds=offset)
        elif "at" in raw_event:
            when = parse_iso_datetime(str(raw_event["at"]))
        else:
            raise ValueError(f"イベント{idx}にafter_secondsまたはatを指定してください")

        message = str(raw_event.get("message", "")).strip()

        reset_spec = raw_event.get("reset_queue", None)
        if reset_spec is not None and reset_spec is not False:
            if reset_spec is True:
                reset_options: Dict[str, Any] = {}
            elif isinstance(reset_spec, dict):
                reset_options = {
                    "queue_path": reset_spec.get("queue_path"),
                    "template": reset_spec.get("template"),
                }
            else:
                raise ValueError(f"イベント{idx}のreset_queue指定が不正です (bool か dict を指定してください)")
            events.append(
                {
                    "index": idx,
                    "when": when,
                    "action": "reset_queue",
                    "reset_options": reset_options,
                    "message": message,
                }
            )
            continue

        cmd = str(raw_event.get("cmd", "")).strip()
        if not cmd:
            raise ValueError(f"イベント{idx}にはcmdが必須です")

        events.append(
            {
                "index": idx,
                "when": when,
                "action": "cmd",
                "cmd": cmd,
                "message": message,
            }
        )

    events.sort(key=lambda item: item["when"])
    return events


def wait_until(target: datetime, *, dry_run: bool) -> None:
    if dry_run:
        return
    now = datetime.now()
    delay = (target - now).total_seconds()
    if delay > 0:
        time.sleep(delay)


def execute_command(cmd: str) -> subprocess.CompletedProcess[str]:
    print(f"[AGENT] 実行: {cmd}", flush=True)
    return subprocess.run(
        cmd,
        shell=True,
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
    )


def format_log_line(event: Dict[str, Any], result: subprocess.CompletedProcess[str]) -> List[str]:
    timestamp = event["when"].strftime(TIMESTAMP_FMT)
    header = f"{timestamp} CMD rc={result.returncode} cmd={event['cmd']}"
    if event.get("message"):
        header += f" message={event['message']}"

    lines = [header.strip()]

    if result.stdout:
        for line in result.stdout.rstrip().splitlines():
            lines.append(f"{timestamp} STDOUT {line}")
    if result.stderr:
        for line in result.stderr.rstrip().splitlines():
            lines.append(f"{timestamp} STDERR {line}")

    return lines


def run_analysis(agent_jsonl: Path, scenario_name: str) -> None:
    if not agent_jsonl.exists():
        print(f"[ANALYZE] JSONL が存在しないため解析をスキップします: {agent_jsonl}", flush=True)
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RESULTS_DIR / f"{slugify(scenario_name)}_analysis.csv"
    parser = build_analyzer_parser()
    analyzer_args = parser.parse_args(
        [
            "--agent-jsonl",
            str(agent_jsonl),
            "--output-csv",
            str(csv_path),
        ]
    )
    print(f"[ANALYZE] 解析結果を {csv_path} に出力します", flush=True)
    run_analyzer(analyzer_args)


def process_reset_event(event: Dict[str, Any], queue_defaults: Dict[str, Any]) -> List[str]:
    options = event.get("reset_options") or {}
    queue_path_raw = options.get("queue_path") or queue_defaults.get("queue_path")
    template_raw = options.get("template") or queue_defaults.get("template")
    if not queue_path_raw or not template_raw:
        raise ValueError("queue_reset イベントに queue_path または template が指定されていません")

    queue_path, template_path = copy_queue_template(queue_path_raw, template_raw)
    timestamp = event["when"].strftime(TIMESTAMP_FMT)
    header = f"{timestamp} RESET queue_path={queue_path} template={template_path}"
    if event.get("message"):
        header += f" message={event['message']}"
    return [header]


def write_log_file(path: Path, lines: List[str]) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as fp:
        for line in lines:
            fp.write(line)
            fp.write("\n")


def run(args: argparse.Namespace) -> None:
    scenario_value, scenario_from_cfg = pick_option(args.scenario, "scenario", DEFAULT_SCENARIO)
    jsonl_value, jsonl_from_cfg = pick_option(args.output_jsonl, "output_jsonl", DEFAULT_JSONL)
    log_dir_value, log_dir_from_cfg = pick_option(args.log_dir, "log_dir", DEFAULT_LOG_DIR)

    scenario_path = resolve_path(scenario_value, prefer_base=True if scenario_from_cfg else False)
    jsonl_path = resolve_path(
        jsonl_value,
        prefer_base=should_use_base(jsonl_value, jsonl_from_cfg),
    )
    log_dir = resolve_path(
        log_dir_value,
        prefer_base=should_use_base(log_dir_value, log_dir_from_cfg),
    )

    dry_run = args.dry_run if args.dry_run is not None else bool(cfg("dry_run", False))
    append_mode = args.append if args.append is not None else bool(cfg("append", False))
    base_start_value = args.base_start if args.base_start is not None else cfg("base_start", None)
    queue_cfg = queue_reset_config()
    reset_queue = args.reset_queue if args.reset_queue is not None else bool(queue_cfg.get("enabled", False))

    if reset_queue:
        maybe_reset_queue()

    if jsonl_path.exists() and not append_mode:
        jsonl_path.unlink()

    run_started_utc = datetime.now(timezone.utc)
    record: Dict[str, Any]
    scenario_name: Optional[str] = None
    scenario: Optional[Dict[str, Any]] = None
    executed_lines: List[str] = []
    log_file: Optional[Path] = None

    try:
        scenario = load_scenario(scenario_path)
        base_start = determine_base_start(scenario, base_start_value)
        events = prepare_events(scenario, base_start)

        scenario_name = scenario.get("name") or scenario_path.stem
        log_file = (log_dir / f"{slugify(str(scenario_name))}.log").resolve()

        for event in events:
            wait_until(event["when"], dry_run=dry_run)
            if event.get("action") == "reset_queue":
                lines = process_reset_event(event, queue_cfg)
                executed_lines.extend(lines)
                for line in lines:
                    print(line, flush=True)
                continue

            result = execute_command(event["cmd"])
            lines = format_log_line(event, result)
            executed_lines.extend(lines)
            for line in lines:
                print(line, flush=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"コマンドが失敗しました (rc={result.returncode}): {event['cmd']}"
                )

        write_log_file(log_file, executed_lines)
        print(f"[AGENT] ログを {log_file} に保存しました", flush=True)

        record = {
            "case_id": scenario.get("id") or scenario_path.stem,
            "scenario_name": scenario_name,
            "scenario_file": str(scenario_path),
            "expected_label": scenario.get("expected_label"),
            "kind": scenario.get("kind"),
            "size": scenario.get("size"),
            "description": scenario.get("description"),
            "generated_log": "\n".join(executed_lines),
            "log_file": str(log_file),
            "event_count": len(events),
            "base_start": base_start.isoformat(),
            "run_started": run_started_utc.isoformat(),
            "run_completed": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
        }
    except Exception as exc:
        print(f"[AGENT] 実行エラー: {exc}", flush=True)
        if log_file and executed_lines:
            write_log_file(log_file, executed_lines)
            print(f"[AGENT] 途中までのログを {log_file} に保存しました", flush=True)
        record = {
            "scenario_file": str(scenario_path),
            "scenario_name": scenario_name,
            "log_file": str(log_file) if log_file else None,
            "generated_log": "\n".join(executed_lines),
            "event_count": len(executed_lines),
            "run_started": run_started_utc.isoformat(),
            "run_completed": datetime.now(timezone.utc).isoformat(),
            "status": "error",
            "error": str(exc),
        }
        if scenario is not None:
            record.setdefault("case_id", scenario.get("id") or scenario_path.stem)
            record.setdefault("expected_label", scenario.get("expected_label"))
            record.setdefault("kind", scenario.get("kind"))
            record.setdefault("size", scenario.get("size"))
            record.setdefault("description", scenario.get("description"))

    append_jsonl(jsonl_path, record)
    analysis_target = scenario_name or scenario_path.stem
    if executed_lines and analysis_target:
        run_analysis(jsonl_path, analysis_target)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YAML駆動のテストシナリオ実行エージェント")
    parser.add_argument(
        "--scenario",
        help="実行するシナリオYAMLのパス",
        default=None,
    )
    parser.add_argument(
        "--output-jsonl",
        help="実行結果を記録するJSONLファイル",
        default=None,
    )
    parser.add_argument(
        "--log-dir",
        help="生成ログを保存するディレクトリ",
        default=None,
    )
    parser.add_argument(
        "--base-start",
        help="after_seconds計算の基準となるISO8601日時。未指定時はシナリオstart_atまたは現在時刻",
        default=None,
    )

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="時間待ちをスキップして即時実行する",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="設定ファイルでdry_runが有効な場合に無効化する",
    )
    parser.add_argument(
        "--append",
        dest="append",
        action="store_true",
        help="既存のJSONLを残したまま追記する",
    )
    parser.add_argument(
        "--no-append",
        dest="append",
        action="store_false",
        help="設定ファイルでappendが有効な場合に再作成に切り替える",
    )
    parser.add_argument(
        "--reset-queue",
        dest="reset_queue",
        action="store_true",
        help="シナリオ実行前に queue.json をテンプレートで初期化する",
    )
    parser.add_argument(
        "--no-reset-queue",
        dest="reset_queue",
        action="store_false",
        help="設定ファイルの queue_reset 指定を無効化する",
    )
    parser.set_defaults(dry_run=None, append=None, reset_queue=None)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
