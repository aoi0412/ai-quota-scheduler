# ai-quota-scheduler

Claude サブスクリプションの 5 時間枠・週間枠のうち、本人が使い切れない「余り」だけで harness2 を回す常駐スケジューラ。本人の利用を最優先する。

- 設計書: `docs/superpowers/specs/2026-10-21-ai-quota-scheduler-design.md`
- 実装計画: `docs/superpowers/plans/2026-10-21-ai-quota-scheduler-core.md`

## 仕組み

5 分ごとに次を繰り返す。

1. 残量を観測する（使用量 API → statusline スナップショットの順に試す）。
2. 本人の使用量を推定する（合計の増分 − harness 推定分）。曜日 × 時間帯の上側分位点（既定 0.95）で将来の使用量を予測する。
3. 安全ゲートを通す。観測なし・観測が古い・窓のリセット後で未観測・429 検知・キルスイッチ・コールドスタート（最初の 7 日）のどれかに当たれば並列 0 にする。
4. 上位 MPC で週間枠の余りを今の 5 時間ブロックに配分し、下位 MPC で並列数を決める。条件は「本人の待ち時間 ≤ 15 分」かつ「配分予算内」。
5. ジョブを起動または制御し、判断の根拠を SQLite に記録する。

harness2 側は `<state_dir>/jobs/<job_id>/aqs_control.json`（`paused` と `max_parallel`）を読んで従う。この読取口は harness2 fork 側の別計画で実装する。それまではジョブの起動と停止スイッチの記録だけが機能する。

## セットアップ

前提: Python 3.12、[uv](https://docs.astral.sh/uv/)。

```bash
uv tool install --from ~/ghq/github.com/aoi0412/ai-quota-scheduler ai-quota-scheduler
```

`aqs` が `~/.local/bin/aqs` に入る。

### 設定 `~/.config/ai-quota-scheduler/config.toml`

書かなかったキーには既定値が入る。

```toml
wait_limit_min = 15.0        # 本人の待ち時間の上限（分）
quantile = 0.95              # 本人予測の上側分位点（0.90〜0.95）
replan_interval_s = 300
max_parallel = 1             # harness の並列上限
cold_start_days = 7          # この日数は記録のみ（並列 0）
timezone = "Asia/Tokyo"      # 予測バケットの曜日・時刻
job_command = ["harness2"]   # ジョブ起動コマンド（キューの引数が後ろに付く）
harness_config_dir = "~/.config/ai-quota-scheduler/claude"  # harness 専用 CLAUDE_CONFIG_DIR
adapters = ["claude_usage_api", "claude_statusline"]

# 使用量 API（非公開。仕様が予告なく変わりうる。空なら無効）
usage_api_url = ""
usage_api_token_file = ""    # 例: "~/.claude/.credentials.json"（accessToken を Bearer で送る）
usage_api_headers = []       # 例: ["anthropic-beta: ..."]
```

### statusline フック

Claude Code の statusline に渡される JSON（`rate_limits.five_hour` / `seven_day`）を保存する。`~/.claude/settings.json` に次を書く。

```json
{
  "statusLine": {
    "type": "command",
    "command": "aqs statusline-hook --out ~/.local/state/ai-quota-scheduler/claude_statusline.json"
  }
}
```

表示は `5h 62% · 7d 22%` の 1 行になる。既存の statusline と併用する場合は、そのスクリプトの中で stdin を `aqs statusline-hook` にも渡す。statusline は Claude Code を使っている間しか更新されないため、観測が `stale_after_s`（既定 900 秒）より古くなると harness は止まる。

### harness 専用の Claude 設定ディレクトリ

本人の対話セッションと使用量の記録を混ぜないため、harness には別の `CLAUDE_CONFIG_DIR` でログインしておく。

```bash
CLAUDE_CONFIG_DIR=~/.config/ai-quota-scheduler/claude claude   # 起動して /login
```

### systemd（user unit）

```bash
mkdir -p ~/.config/systemd/user
cp deploy/ai-quota-scheduler.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ai-quota-scheduler
loginctl enable-linger "$USER"   # ログアウト中も動かす場合（sudo が必要なことがある）
```

## 使い方

```bash
aqs status                              # 残量・直近の判断・ジョブ
aqs status --json
aqs queue add -- --bench jb --k 1       # harness2 の 1 回の実行をキューに入れる
aqs queue list
aqs kill                                # 緊急停止（キルスイッチを入れる）
aqs unkill
```

Claude Code からは `.claude/agents/quota-scheduler.md` のサブエージェントを窓口に使う。

## 開発

```bash
uv sync
uv run pytest
```
