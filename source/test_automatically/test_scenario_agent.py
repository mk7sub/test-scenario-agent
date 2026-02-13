from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from config_loader import get_value

TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
CONFIG_SECTION = "test_scenario_agent"
BASE_DIR = Path(__file__).resolve().parent


def cfg(key: str, fallback: Any) -> Any:
    return get_value(CONFIG_SECTION, key, fallback)


def queue_reset_config() -> Dict[str, Any]:
    raw = get_value(CONFIG_SECTION, "queue_reset", {})
    return raw if isinstance(raw, dict) else {}


def resolve_relative_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = (BASE_DIR / raw).resolve()
    return path


def maybe_reset_queue() -> None:
    settings = queue_reset_config()
    queue_path_raw = settings.get("queue_path")
    template_path_raw = settings.get("template")
    if not queue_path_raw or not template_path_raw:
        print("[AGENT] queue_reset の queue_path または template が設定されていません", flush=True)
        return

    queue_path = resolve_relative_path(str(queue_path_raw))
    template_path = resolve_relative_path(str(template_path_raw))

    if not template_path.exists():
        print(f"[AGENT] queue_reset テンプレートが見つかりません: {template_path}", flush=True)
        return

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path, queue_path)
    print(f"[AGENT] queue.json を初期化しました: {queue_path}", flush=True)


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

        order = raw_event.get("order")
        status = raw_event.get("status")
        if not order or not status:
            raise ValueError(f"イベント{idx}にはorderとstatusが必須です")

        level = str(raw_event.get("level", "INFO")).upper()
        message = str(raw_event.get("message", "")).strip()

        events.append(
            {
                "index": idx,
                "when": when,
                "level": level,
                "order": str(order),
                "status": str(status),
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


def format_log_line(event: Dict[str, Any]) -> str:
    timestamp = event["when"].strftime(TIMESTAMP_FMT)
    return (
        f"{timestamp} {event['level']} order={event['order']} "
        f"status={event['status']} message={event['message']}"
    ).strip()


def write_log_file(path: Path, lines: List[str]) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as fp:
        for line in lines:
            fp.write(line)
            fp.write("\n")


def run(args: argparse.Namespace) -> None:
    scenario_path = Path(args.scenario)
    jsonl_path = Path(args.output_jsonl)
    log_dir = Path(args.log_dir)

    if getattr(args, "reset_queue", False):
        maybe_reset_queue()

    if jsonl_path.exists() and not args.append:
        jsonl_path.unlink()

    run_started_utc = datetime.utcnow()
    record: Dict[str, Any]

    try:
        scenario = load_scenario(scenario_path)
        base_start = determine_base_start(scenario, args.base_start)
        events = prepare_events(scenario, base_start)

        scenario_name = scenario.get("name") or scenario_path.stem
        log_file = log_dir / f"{slugify(str(scenario_name))}.log"
        executed_lines: List[str] = []

        for event in events:
            wait_until(event["when"], dry_run=args.dry_run)
            line = format_log_line(event)
            executed_lines.append(line)
            print(line, flush=True)

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
            "event_count": len(executed_lines),
            "base_start": base_start.isoformat(),
            "run_started": run_started_utc.isoformat() + "Z",
            "run_completed": datetime.utcnow().isoformat() + "Z",
            "status": "ok",
        }
    except Exception as exc:
        print(f"[AGENT] 実行エラー: {exc}", flush=True)
        record = {
            "scenario_file": str(args.scenario),
            "run_started": run_started_utc.isoformat() + "Z",
            "run_completed": datetime.utcnow().isoformat() + "Z",
            "status": "error",
            "error": str(exc),
        }

    append_jsonl(jsonl_path, record)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YAML駆動のテストシナリオ実行エージェント")
    parser.add_argument(
        "--scenario",
        default=cfg("scenario", os.path.join("./scenario", "normal_flow.yaml")),
        help="実行するシナリオYAMLのパス",
    )
    parser.add_argument(
        "--output-jsonl",
        default=cfg("output_jsonl", os.path.join("./log", "agent_runs.jsonl")),
        help="実行結果を記録するJSONLファイル",
    )
    parser.add_argument(
        "--log-dir",
        default=cfg("log_dir", os.path.join("./log", "agent_cases")),
        help="生成ログを保存するディレクトリ",
    )
    parser.add_argument(
        "--base-start",
        default=cfg("base_start", None),
        help="after_seconds計算の基準となるISO8601日時。未指定時はシナリオstart_atまたは現在時刻",
    )
    queue_cfg = queue_reset_config()

    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=bool(cfg("dry_run", False)),
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
        default=bool(cfg("append", False)),
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
        default=bool(queue_cfg.get("enabled", False)),
        help="シナリオ実行前に queue.json をテンプレートで初期化する",
    )
    parser.add_argument(
        "--no-reset-queue",
        dest="reset_queue",
        action="store_false",
        help="設定ファイルの queue_reset 指定を無効化する",
    )
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
