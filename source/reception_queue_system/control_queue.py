from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
QUEUE_PATH = BASE_DIR / "queue.json"
LOG_DIR = BASE_DIR / "log"
ISO_FMT = "%Y-%m-%dT%H:%M:%S"


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def load_queue() -> Dict[str, Any]:
    if QUEUE_PATH.exists():
        with QUEUE_PATH.open("r", encoding="utf-8") as fp:
            try:
                data = json.load(fp)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}

    orders = data.get("orders")
    if not isinstance(orders, list):
        orders = []

    count_raw = data.get("count", 0)
    try:
        count = int(count_raw)
    except (TypeError, ValueError):
        count = 0

    return {"orders": orders, "count": count}


def save_queue(payload: Dict[str, Any]) -> None:
    data = {
        "orders": payload.get("orders", []),
        "count": int(payload.get("count", 0)),
    }
    with QUEUE_PATH.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def append_log(message: str, level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_name = f"control_queue_{now.strftime('%Y%m%d')}.log"
    log_path = LOG_DIR / log_name
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write(f"[{timestamp}] [{level}] {message}\n")


def ensure_unique_id(orders: List[Dict[str, Any]], order_id: str) -> None:
    if any(str(order.get("id")) == order_id for order in orders):
        raise ValueError(f"既に存在する order_id です: {order_id}")


def pick_order(orders: List[Dict[str, Any]], order_id: Optional[str], *, status: Optional[str] = None) -> Dict[str, Any]:
    target: Optional[Dict[str, Any]] = None
    if order_id:
        for order in orders:
            if str(order.get("id")) == order_id:
                target = order
                break
        if not target:
            raise ValueError(f"order_id が見つかりません: {order_id}")
    else:
        for order in orders:
            if status is None or order.get("status") == status:
                target = order
                break
        if not target:
            raise ValueError("対象となる注文が存在しません")
    if status and target.get("status") != status:
        raise ValueError(f"order_id={target.get('id')} はステータス {status} ではありません")
    return target


def generate_auto_id(queue_payload: Dict[str, Any]) -> str:
    queue_payload["count"] = queue_payload.get("count", 0) + 1
    return f"{queue_payload['count']:03d}"


def maybe_update_count(queue_payload: Dict[str, Any], order_id: str) -> None:
    if order_id.isdigit():
        value = int(order_id)
        if value > queue_payload.get("count", 0):
            queue_payload["count"] = value


def register_order(order_id: Optional[str]) -> Dict[str, Any]:
    queue_payload = load_queue()
    orders = queue_payload["orders"]
    if order_id:
        ensure_unique_id(orders, order_id)
        maybe_update_count(queue_payload, order_id)
        new_id = order_id
    else:
        new_id = generate_auto_id(queue_payload)
        ensure_unique_id(orders, new_id)

    entry = {
        "id": new_id,
        "status": "受付済み",
        "queued_at": now_iso(),
    }
    orders.append(entry)
    save_queue(queue_payload)
    return entry


def update_status(order_id: str, new_status: str) -> Dict[str, Any]:
    queue_payload = load_queue()
    orders = queue_payload["orders"]
    target = pick_order(orders, order_id, status=None)
    target["status"] = new_status
    if new_status == "仕掛中":
        target["updated_at"] = now_iso()
    elif new_status == "完了":
        target["completed_at"] = now_iso()
    save_queue(queue_payload)
    return target


def remove_order(order_id: Optional[str], *, require_complete: bool) -> Dict[str, Any]:
    queue_payload = load_queue()
    orders = queue_payload["orders"]
    status = "完了" if require_complete else None
    target = pick_order(orders, order_id, status=status)
    queue_payload["orders"] = [order for order in orders if order is not target]
    save_queue(queue_payload)
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="呼出キュー制御ツール")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="受付を登録する (ステータス:受付済み)")
    register.add_argument("--order-id", help="任意の注文番号。省略時は自動採番")

    start = sub.add_parser("start", help="調理開始 (ステータスを仕掛中へ)")
    start.add_argument("order_id", help="対象注文ID")

    finish = sub.add_parser("finish", help="調理完了 (ステータスを完了へ)")
    finish.add_argument("order_id", help="対象注文ID")

    handoff = sub.add_parser("handoff", help="お渡し (完了ステータスの注文を削除)")
    handoff.add_argument("--order-id", help="対象注文ID。省略時は最古の完了注文")

    cancel = sub.add_parser("cancel", help="キャンセル (ステータスに関係なく削除)")
    cancel.add_argument("order_id", help="キャンセルする注文ID")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "register":
            result = register_order(args.order_id)
            message = f"[REGISTER] {result['id']} を受付済みで追加しました"
            print(message)
            append_log(message)
        elif args.command == "start":
            result = update_status(args.order_id, "仕掛中")
            message = f"[START] {result['id']} を仕掛中に変更しました"
            print(message)
            append_log(message)
        elif args.command == "finish":
            result = update_status(args.order_id, "完了")
            message = f"[FINISH] {result['id']} を完了に変更しました"
            print(message)
            append_log(message)
        elif args.command == "handoff":
            result = remove_order(args.order_id, require_complete=True)
            message = f"[HANDOFF] {result['id']} をキューから削除しました (お渡し)"
            print(message)
            append_log(message)
        elif args.command == "cancel":
            result = remove_order(args.order_id, require_complete=False)
            message = f"[CANCEL] {result['id']} をキャンセルしました"
            print(message)
            append_log(message)
    except ValueError as exc:
        message = f"[ERROR] {exc}"
        print(message)
        append_log(message, level="ERROR")


if __name__ == "__main__":
    main()
