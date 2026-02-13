from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import tkinter as tk
from tkinter import messagebox

BASE_DIR = Path(__file__).resolve().parent
QUEUE_PATH = BASE_DIR / "queue.json"
LOG_DIR = BASE_DIR / "log"
POLL_INTERVAL_MS = 1000
WAITING_STATUSES = {"受付済み", "仕掛中"}
CALLING_STATUSES = {"完了"}


def append_log(message: str, level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_name = f"display_board_{now.strftime('%Y%m%d')}.log"
    log_path = LOG_DIR / log_name
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write(f"[{timestamp}] [{level}] {message}\n")


def parse_iso(value: str | None) -> float:
    if not value:
        return 0.0
    text = value.strip()
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def load_orders(path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    orders: Iterable[Dict[str, Any]] = payload.get("orders") or []
    indexed = [(idx, order) for idx, order in enumerate(orders)]

    waiting: List[Dict[str, Any]] = []
    calling: List[Dict[str, Any]] = []

    for idx, order in indexed:
        status = str(order.get("status", "")).strip()
        base = order.copy()
        base["__index"] = idx
        if status in WAITING_STATUSES:
            waiting.append(base)
        elif status in CALLING_STATUSES:
            calling.append(base)

    waiting.sort(key=lambda item: (parse_iso(item.get("queued_at")), item["__index"]))
    calling.sort(key=lambda item: (parse_iso(item.get("completed_at") or item.get("updated_at")), item["__index"]))

    return waiting, calling


class QueueDisplayApp:
    def __init__(self, root: tk.Tk, queue_path: Path) -> None:
        self.root = root
        self.queue_path = queue_path
        self.last_mtime: float | None = None
        self.waiting_snapshot: Dict[str, str] = {}
        self.calling_snapshot: Dict[str, str] = {}

        self.root.title("フードコート呼出ディスプレイ")
        self.root.geometry("900x500")
        self.root.configure(bg="white")

        self.waiting_var = tk.StringVar(value="読み込み中...")
        self.calling_var = tk.StringVar(value="読み込み中...")

        self._build_layout()
        self._schedule_poll()

    def _build_layout(self) -> None:
        left_frame = tk.Frame(self.root, bg="white")
        divider = tk.Frame(self.root, bg="#cccccc", width=2)
        right_frame = tk.Frame(self.root, bg="white")

        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(20, 10), pady=20)
        divider.pack(side=tk.LEFT, fill=tk.Y, pady=20)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 20), pady=20)

        title_font = ("Meiryo", 24, "bold")
        list_font = ("Menlo", 22)

        tk.Label(left_frame, text="お待ち番号", font=title_font, bg="white", fg="#0d47a1").pack(anchor="n", pady=(0, 10))
        tk.Label(left_frame, textvariable=self.waiting_var, font=list_font, bg="white", justify="left", anchor="n")\
            .pack(fill=tk.BOTH, expand=True)

        tk.Label(right_frame, text="呼び出し番号", font=title_font, bg="white", fg="#b71c1c").pack(anchor="n", pady=(0, 10))
        tk.Label(right_frame, textvariable=self.calling_var, font=list_font, bg="white", justify="left", anchor="n")\
            .pack(fill=tk.BOTH, expand=True)

    def _schedule_poll(self) -> None:
        self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    def _poll_queue(self) -> None:
        try:
            mtime = self.queue_path.stat().st_mtime
        except FileNotFoundError:
            self._show_error("queue.json が見つかりません")
            self._schedule_poll()
            return

        if self.last_mtime != mtime:
            try:
                waiting, calling = load_orders(self.queue_path)
                self._update_lists(waiting, calling)
                self.last_mtime = mtime
                append_log(
                    f"queue.json を更新 waiting={len(waiting)} calling={len(calling)}"
                )
            except Exception as exc:  # noqa: BLE001
                self._show_error(f"queue.json の読み込みに失敗しました: {exc}")

        self._schedule_poll()

    def _update_lists(self, waiting: List[Dict[str, Any]], calling: List[Dict[str, Any]]) -> None:
        self.waiting_snapshot = self._record_panel_events("お待ちエリア", self.waiting_snapshot, waiting)
        self.calling_snapshot = self._record_panel_events("呼出エリア", self.calling_snapshot, calling)

        waiting_text = "\n".join(f"・{order.get('id')} ({order.get('status')})" for order in waiting) or "現在お待ち番号はありません"
        calling_text = "\n".join(f"・{order.get('id')}" for order in calling) or "現在呼び出し番号はありません"

        self.waiting_var.set(waiting_text)
        self.calling_var.set(calling_text)

    def _show_error(self, message: str) -> None:
        self.waiting_var.set(message)
        self.calling_var.set(message)
        print(f"[WARN] {message}", file=sys.stderr)
        append_log(message, level="WARN")

    def _record_panel_events(
        self,
        panel_name: str,
        previous: Dict[str, str],
        orders: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        current: Dict[str, str] = {}
        for order in orders:
            order_id = order.get("id")
            if not order_id:
                continue
            current[str(order_id)] = str(order.get("status", ""))

        prev_ids = set(previous.keys())
        curr_ids = set(current.keys())

        for added in sorted(curr_ids - prev_ids):
            append_log(
                f"{panel_name}: {added} を表示 status={current.get(added) or '未設定'}"
            )
        for removed in sorted(prev_ids - curr_ids):
            append_log(
                f"{panel_name}: {removed} を非表示 status={previous.get(removed) or '未設定'}"
            )

        return current


def main() -> None:
    if not QUEUE_PATH.exists():
        warning = f"ファイルが見つかりません: {QUEUE_PATH}"
        messagebox.showwarning("queue.json", warning)
        append_log(warning, level="WARN")
    root = tk.Tk()
    app = QueueDisplayApp(root, QUEUE_PATH)
    append_log("ディスプレイボードを起動しました")
    root.mainloop()


if __name__ == "__main__":
    main()
