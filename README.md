# test-scenario-agent

フードコート向け呼び出し・順番待ちシステムのログを対象に、YAML で記述したテストシナリオを自動実行し、その結果をローカル LLM (Ollama) で解析・ベンチマークするためのツール群です。WSL2 上で起動している Ollama サーバーへは HTTP API 経由で接続します（ログ解析・ベンチマークで使用）。

## 前提条件
- Python 3.10 以上
- パッケージ: `pandas`, `requests`, `PyYAML`
- WSL2/AlmaLinux 側で `ollama serve` が起動済み（Windows からは `http://localhost:11434` で到達できる想定）
- 必要に応じて環境変数 `OLLAMA_BASE_URL` で API ベースURLを上書き可能

```
pip install pandas requests PyYAML
```

## データセットの生成
合成ログとテストケース定義は `source/generate_dataset.py` で生成します。

```
python source/generate_dataset.py
```

出力物:
- `log/app.log`
- `testcase/testcases.csv`
- `testcase/testcases_table.csv`

## YAMLベースのテストシナリオ自動実行エージェント
`source/test_scenario_agent.py` は `scenario/` 配下の YAML シナリオを読み込み、指定されたタイムラインどおりにログイベントを発生させます。LLM には依存せず、シナリオに記述された「何秒後」「指定日時」の定義に従って実行します。出力は JSONL とケースごとの `.log` ファイルです。

```
python source/test_scenario_agent.py \
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

### シナリオファイルの書式
`scenario/normal_flow.yaml` を参考に、以下のように記述します。

```
id: normal-small-1
name: normal_small_flow
expected_label: PASS
start_at: 2026-02-12T10:00:00

events:
	- after_seconds: 0
		level: INFO
		order: normal-1
		status: 受付済み
		message: 受付完了
	- after_seconds: 5
		level: INFO
		order: normal-1
		status: 仕掛中
		message: 調理開始
	- at: 2026-02-12T10:00:15
		level: INFO
		order: normal-1
		status: 完了
		message: 調理完了・呼び出し
```

- `after_seconds`: 基準時刻（`start_at` または `--base-start` 指定時刻）からの相対秒。
- `at`: 絶対日時（ISO8601 形式）。`after_seconds` と同時に指定しないでください。
- `order`, `status` は必須。`level` が未指定の場合は `INFO` になります。
- 実行結果は `log/agent_cases/<シナリオ名>.log` と `log/agent_runs.jsonl` に保存されます。

## エージェント出力ログ解析
`source/analyze_agent_logs.py` はエージェントが生成したログを LLM に渡し、PASS/FAIL 判定を実施します。`prompts.build_analysis_prompt` に含まれる仕様・評価観点を使用します。

```
python source/analyze_agent_logs.py \
	--agent-jsonl ./log/agent_runs.jsonl \
	--output-csv ./log/agent_analysis_results.csv \
	--model phi4-mini-latest-16384
```

結果 CSV には期待ラベルやエージェント判定との一致状況、LLM 応答全文などが含まれます。
デフォルトでも `phi4-mini-latest-16384` が利用されるため、別モデルを使う場合のみ `--model` を上書きしてください。
