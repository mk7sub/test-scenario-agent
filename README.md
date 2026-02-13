# test-scenario-agent

フードコート向け呼び出し・順番待ちシステムのログを対象に、擬似的な表示システムとテスト自動化スクリプト群を提供します。YAML シナリオを再生してログを生成し、ローカル LLM (Ollama) による解析までを一貫して行えます。また、`source/reception_queue_system/queue.json` を編集するだけで画面表示が更新される簡易 UI も含めています。

## システム仕様
対象システムはフードコートの呼び出し・順番待ちで使用される画面表示系アプリケーションで、ログと画面表示の動きは常に一致している前提です。

### ステータス
- 受付済み
- 仕掛中
- 完了

### 処理の流れ
1. 受付・採番
2. 表示（お待ち番号）: ステータスが「受付済み」「仕掛中」の一覧を表示
3. 調理完了
4. 表示（呼び出し番号）: ステータスが「完了」の一覧を表示
5. お渡し完了

### 重要な制約
- ステータスは必ず「受付済み」→「仕掛中」→「完了」の順番で遷移する。
- 順番が逆行・飛び越え・欠落すると不正な遷移とみなす。
- ステータス遷移が途中で終わっている（例: 「受付済み」のまま）は許容される。
- ログにエラーや例外が出力されている場合は PASS と判定してはいけない。

## ディレクトリ概要
- `source/reception_queue_system/`
	- `queue.json`: 表示システムが参照する受付情報。
	- `display_board.py`: Tkinter で構築した簡易呼出ディスプレイ。
	- `control_queue.py`: queue.json を更新するステータス制御CLI。
- `source/test_automatically/`
	- これまで作成した Python スクリプト（データセット生成、シナリオ実行、LLM 解析、Ollama ラッパーなど）を集約。
	- `config/settings.yaml`: 自動テスト系スクリプトの初期値をまとめる設定ファイル。

## 前提条件
- Python 3.10 以上
- パッケージ: `pandas`, `requests`, `PyYAML`
- WSL2/AlmaLinux 側で `ollama serve` が起動済み（Windows からは `http://localhost:11434` で到達できる想定）
- 必要に応じて環境変数 `OLLAMA_BASE_URL` で API ベースURLを上書き可能

```
pip install pandas requests PyYAML
```

## 擬似画面表示システム
`source/reception_queue_system/display_board.py` は Tkinter で構築した簡易 UI です。ウィンドウ左側に「お待ち番号」（受付済み・仕掛中）、右側に「呼び出し番号」（完了）を表示し、中央に縦線で区切ります。`queue.json` の変更を 1 秒間隔で監視し、ファイル内容が変わるたびに画面へ即座に反映します。

> 以下の例では `cd source` してから仮想環境をアクティブ化し、`python ...` を実行する想定です。

```
cd source
python reception_queue_system/display_board.py
```

`queue.json` には以下のように ID とステータスを記載します（順序は受付順・完了順を意味します）。`count` は直近で採番された数値を保持し、`register` 実行時に自動的にカウントアップされます。

```
{
	"count": 3,
	"orders": [
		{"id": "001", "status": "受付済み", "queued_at": "2026-02-13T11:59:00"},
		{"id": "002", "status": "仕掛中", "queued_at": "2026-02-13T12:02:00", "updated_at": "2026-02-13T12:05:10"},
		{"id": "003", "status": "完了", "completed_at": "2026-02-13T12:01:30"}
	]
}
```

- `status` が「受付済み」「仕掛中」のものは左列（受付順）、「完了」は右列（完了順）に表示。
- `queued_at` / `completed_at` を ISO8601 形式で記述すると並び順の基準になります。省略時はファイル内の並び順を使用します。
- JSON を更新して保存するだけでウィンドウに反映されます。

### ステータス制御 CLI
`reception_queue_system/control_queue.py` を使うと、キュー操作をコマンドラインから行えます。`queue.json` を直接編集する代わりに以下のサブコマンドを利用してください。

```
cd source
python reception_queue_system/control_queue.py register --order-id A105
python reception_queue_system/control_queue.py start A105
python reception_queue_system/control_queue.py finish A105
python reception_queue_system/control_queue.py handoff --order-id A105
python reception_queue_system/control_queue.py cancel A102
```

- `register`: 採番＋ステータスを「受付済み」に設定。`--order-id` 省略時は `001` から始まる連番で自動採番（`queue.json` の `count` を更新）。
- `start`: ステータスを「仕掛中」に更新。
- `finish`: ステータスを「完了」に更新。
- `handoff`: 完了済みの注文を削除（`--order-id` 省略時は最も古い完了注文を削除）。
- `cancel`: 指定IDをステータス問わず削除。

## データセットの生成
合成ログとテストケース定義は `source/test_automatically/generate_dataset.py` で生成します。

```
cd source
python test_automatically/generate_dataset.py
```

出力物:
- `log/app.log`
- `testcase/testcases.csv`
- `testcase/testcases_table.csv`

## YAMLベースのテストシナリオ自動実行エージェント
`source/test_automatically/test_scenario_agent.py` は `scenario/` 配下の YAML シナリオを読み込み、指定されたタイムラインどおりにログイベントを発生させます。LLM には依存せず、シナリオに記述された「何秒後」「指定日時」の定義に従って実行します。出力は JSONL とケースごとの `.log` ファイルです。

```
cd source
python test_automatically/test_scenario_agent.py \
	--scenario ./scenario/normal_flow.yaml \
	--output-jsonl ./log/agent_runs.jsonl \
	--log-dir ./log/agent_cases \
	--dry-run  # 実時間待ちをスキップしたい場合
```

主なオプション:
- `--scenario`: 実行する YAML ファイル
- `--base-start`: `after_seconds` の基準となる ISO8601 日時。未指定時はシナリオ `start_at` または現在時刻
- `--dry-run`: 時間待ちをスキップし、すべて即時に実行
- `--append`: 既存 JSONL に追記（指定しない場合は再作成）

### スクリプト初期状態の設定ファイル
`source/test_automatically/config/settings.yaml` に以下のようなセクションを作っておくと、CLI 引数を省略した際の初期値として読み込まれます。CLI で明示した値が常に優先されます。

```
test_scenario_agent:
	scenario: ./scenario/tc001_normal_sequential.yaml
	output_jsonl: ./log/agent_runs.jsonl
	log_dir: ./log/agent_cases
	base_start:
	dry_run: false
	append: false
	queue_reset:
		enabled: true
		queue_path: ../reception_queue_system/queue.json
		template: ./config/queue_template.json

analyze_agent_logs:
	agent_jsonl: ./log/agent_runs.jsonl
	output_csv: ./log/agent_analysis_results.csv
	model: phi4-mini-latest-16384
	base_url: http://localhost:11434
	temperature: 0.0
	num_ctx: 16384
	limit:
```

`dry_run` や `append` を `true` にした場合でも、`--no-dry-run`・`--no-append` フラグでその場で上書きできます。`queue_reset.enabled` を `true` にしておくと、`queue_template.json` の内容で `reception_queue_system/queue.json` を初期化してからシナリオ実行を開始します（`--no-reset-queue` で無効化可能）。

### シナリオファイルの書式
`scenario/normal_flow.yaml` を参考に、以下のように記述します。

```
id: normal-small-1
name: normal_small_flow
expected_label: PASS
start_at: 2026-02-12T10:00:00

events:
	- after_seconds: 0
		cmd: python reception_queue_system/control_queue.py register
		message: 受付完了 (001を採番)
	- after_seconds: 5
		cmd: python reception_queue_system/control_queue.py start 001
		message: 001番の調理開始
	- at: 2026-02-12T10:00:15
		cmd: python reception_queue_system/control_queue.py finish 001
		message: 001番の調理完了・呼出
```

- `after_seconds`: 基準時刻（`start_at` または `--base-start` 指定時刻）からの相対秒。
- `at`: 絶対日時（ISO8601 形式）。`after_seconds` と同時に指定しないでください。
- `cmd`: 実行するコマンド（相対パス可）。`python reception_queue_system/control_queue.py` のようにテスト対象 CLI を直接記載します。`queue_reset` で `count=0` に初期化されるため、`001` からの連番は `register` 実行時に自動採番でき、`--order-id` を省略できます。
- `message`: 手順のメモ。
- 実行結果は `log/agent_cases/<シナリオ名>.log` と `log/agent_runs.jsonl` に保存されます。

## エージェント出力ログ解析
`source/test_automatically/analyze_agent_logs.py` はエージェントが生成したログを LLM に渡し、PASS/FAIL 判定を実施します。`prompts.build_analysis_prompt` に含まれる仕様・評価観点を使用します。

```
cd source
python test_automatically/analyze_agent_logs.py \
	--agent-jsonl ./log/agent_runs.jsonl \
	--output-csv ./log/agent_analysis_results.csv \
	--model phi4-mini-latest-16384
```

結果 CSV には期待ラベルやエージェント判定との一致状況、LLM 応答全文などが含まれます。
デフォルトでも `phi4-mini-latest-16384` が利用されるため、別モデルを使う場合のみ `--model` を上書きしてください。
