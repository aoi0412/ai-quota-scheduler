# ai-quota-scheduler 本体 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude サブスク残量 観測 → 本人使用量 予測 → 2階層 MPC → harness2 ジョブ 並列数 制御する常駐デーモン。

**Architecture:** 1パッケージ `ai_quota_scheduler`。観測アダプタ（Registry）→ SQLite 記録 → 帰属推定・予測 → 上位(週)/下位(5h) MPC → 安全ゲート → ジョブ起動 + 制御ファイル書込。harness2 側は制御ファイルを読むだけ（別計画）。

**Tech Stack:** Python 3.12, uv, 標準ライブラリ中心（sqlite3, tomllib, dataclasses, logging, argparse）, pytest。追加依存 最小（`httpx` のみ使用量 API 用）。

**Spec:** `docs/superpowers/specs/2026-10-21-ai-quota-scheduler-design.md`

**範囲外（別計画 `harness2-claude-code`）:** harness2 の Claude Code substrate、改善役サブスク認証モード、制御ファイル読取口。本計画はそのプロトコル定義と書込側のみ。

## Global Constraints

- Python 3.12 + uv。Linux 前提。systemd 常駐。
- W（本人 待ち上限）既定 **15分**。設定可。
- 本人 予測は上側分位点。既定 **0.95**（許容 0.90〜0.95）。
- 再計画周期 既定 **300秒**。
- コールドスタート: 最初 **7日** 記録のみ（並列 0 強制）→ 以降 初期上限 **並列1**。
- 安全側停止: 観測失敗 / 観測古い / 429・上限到達 / キルスイッチ → 並列 0。
- 実行中 rollout 最悪消費 → harness 予算側で確保。
- harness 専用 `CLAUDE_CONFIG_DIR` 分離。
- judge 消費 = 予算計算 対象外。
- Gemini 観測しない。Codex アダプタ 任意（本計画で実装、既定 無効）。
- コーディング規約: 1ファイル 200-400行、frozen dataclass 設定、全関数 型ヒント、`logging`（print 禁止）、各 `__init__.py` に `__all__`、アダプタは Factory & Registry。
- コミット: Conventional Commits。

## Review Focus

1. **resets_at 経過後の古い窓**（リセット直後 観測前）→ 使用率 0 扱いでなく「不明」扱い → 並列 0。Task 9 テスト。
2. **観測が % 単位で丸め・据え置き**（Δ=0 が続く）→ 帰属推定の係数 発散しない。Task 5 テスト。
3. **デーモン再起動**（実行中ジョブ残存）→ 二重起動せず 既存 PID 引継ぎ or 停止。Task 11 テスト。
4. **時計・タイムゾーン**（resets_at は epoch 秒 UTC、予測バケットはローカル時刻）→ 曜日・時間帯バケット ずれない。Task 6 テスト。
5. **週リセット直前**（週残量わずか・5h 窓 余裕）→ 上位予算が下位を正しく制限。Task 8 テスト。

---

## File Structure

```
pyproject.toml
src/ai_quota_scheduler/
  __init__.py            # __all__
  config.py              # SchedulerConfig (frozen) + load_config()
  models.py              # WindowState, QuotaSnapshot, Decision
  store.py               # SQLite: snapshots / harness_usage / decisions / jobs
  adapters/__init__.py   # Registry: register_adapter, create_adapter
  adapters/base.py       # QuotaAdapter Protocol
  adapters/claude_statusline.py  # statusline JSON → snapshot ファイル
  adapters/claude_usage_api.py   # 使用量 API ポーリング（エンドポイント設定値）
  adapters/codex_log.py          # ~/.codex/sessions 解析
  attribution.py         # 本人使用量 d 推定 + token→% 係数学習
  forecast.py            # 曜日×時間帯 分位点予測
  rate.py                # harness 消費速度 r 推定
  mpc/__init__.py
  mpc/simulate.py        # ホライズン前進シミュレーション
  mpc/upper.py           # 週間 → 5h 予算
  mpc/lower.py           # 5h → 並列数
  safety.py              # 安全ゲート
  control.py             # 制御ファイル プロトコル（harness2 との境界）
  runner.py              # ジョブ起動・停止・PID 管理
  daemon.py              # 1周期 tick() + ループ
  cli.py                 # `aqs` コマンド
deploy/ai-quota-scheduler.service
.claude/agents/quota-scheduler.md
tests/...（各モジュール対応）
```

---

### Task 1: プロジェクト雛形 + 設定 + データモデル

**Files:**
- Create: `pyproject.toml`, `src/ai_quota_scheduler/__init__.py`, `config.py`, `models.py`
- Test: `tests/test_config.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `SchedulerConfig`（frozen）: `wait_limit_min: float = 15.0`, `quantile: float = 0.95`, `replan_interval_s: int = 300`, `max_parallel: int = 1`, `cold_start_days: int = 7`, `stale_after_s: int = 900`, `r_init_pct_per_min: float = 0.05`, `db_path: Path`, `state_dir: Path`, `harness_config_dir: Path`, `job_command: tuple[str, ...]`, `adapters: tuple[str, ...] = ("claude_usage_api", "claude_statusline")`, `usage_api_url: str = ""`
  - `load_config(path: Path) -> SchedulerConfig`（TOML、未指定キー = 既定値）
  - `WindowState(name: str, used_pct: float, resets_at: datetime, window_minutes: int)`（frozen, UTC aware）
  - `QuotaSnapshot(platform: str, observed_at: datetime, windows: Mapping[str, WindowState], source: str)`（frozen）
  - `Decision(at: datetime, parallel: int, reason: str, detail: Mapping[str, float])`（frozen）
  - 窓名 定数: `FIVE_HOUR = "five_hour"`, `SEVEN_DAY = "seven_day"`

- [ ] **Step 1: 失敗テスト**

```python
def test_defaults_match_spec(tmp_path):
    cfg = load_config(write_toml(tmp_path, {}))
    assert cfg.wait_limit_min == 15.0
    assert cfg.quantile == 0.95
    assert cfg.replan_interval_s == 300
    assert cfg.cold_start_days == 7
    assert cfg.max_parallel == 1

def test_config_is_frozen(tmp_path):
    cfg = load_config(write_toml(tmp_path, {}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.wait_limit_min = 30

def test_quantile_out_of_range_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config(write_toml(tmp_path, {"quantile": 0.5}))

def test_window_requires_aware_utc():
    with pytest.raises(ValueError):
        WindowState("five_hour", 10.0, datetime(2026, 1, 1), 300)
```

- [ ] **Step 2:** `uv run pytest tests/test_config.py tests/test_models.py -v` → FAIL（未定義）
- [ ] **Step 3:** 実装。`quantile` 範囲 [0.90, 0.95] 外 → `ValueError`。`WindowState.__post_init__` で tz なし → `ValueError`。
- [ ] **Step 4:** 同コマンド → PASS
- [ ] **Step 5:** `git commit -m "feat(config): add scheduler config and quota models"`

---

### Task 2: SQLite ストア

**Files:**
- Create: `src/ai_quota_scheduler/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `QuotaSnapshot`, `Decision`
- Produces: `class Store(path: Path)`
  - `add_snapshot(s: QuotaSnapshot) -> None` / `latest_snapshot(platform: str) -> QuotaSnapshot | None` / `snapshots_between(platform: str, start: datetime, end: datetime) -> list[QuotaSnapshot]`
  - `add_harness_usage(job_id: str, at: datetime, tokens: int) -> None` / `harness_tokens_between(start: datetime, end: datetime) -> int`
  - `add_decision(d: Decision) -> None`
  - `first_snapshot_at() -> datetime | None`（コールドスタート判定用）

- [ ] **Step 1: 失敗テスト** — `test_roundtrip_snapshot`（windows 全項目 一致、tz UTC 保持）、`test_latest_snapshot_per_platform`、`test_harness_tokens_sum_range`（境界: start 含む・end 含まない）、`test_first_snapshot_at_empty_is_none`
- [ ] **Step 2:** `uv run pytest tests/test_store.py -v` → FAIL
- [ ] **Step 3:** 実装。windows は JSON 列。datetime は epoch 秒 REAL 保存。`PRAGMA journal_mode=WAL`。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(store): add sqlite persistence"`

---

### Task 3: アダプタ Registry + Claude statusline アダプタ

**Files:**
- Create: `adapters/__init__.py`, `adapters/base.py`, `adapters/claude_statusline.py`
- Test: `tests/adapters/test_claude_statusline.py`, `tests/adapters/test_registry.py`

**Interfaces:**
- Produces:
  - `class QuotaAdapter(Protocol): platform: str; def read(self) -> QuotaSnapshot | None`
  - `register_adapter(name: str) -> Callable[[type], type]` / `create_adapter(name: str, cfg: SchedulerConfig) -> QuotaAdapter`（未登録名 → `KeyError`）
  - `statusline_hook(stdin_json: str, out_path: Path) -> None`: statusline JSON の `rate_limits.five_hour` / `seven_day`（`used_percentage`, `resets_at` epoch 秒）→ snapshot ファイル（JSON）へ原子的書込（tmp + rename）。`rate_limits` 欠落 → 何も書かない
  - `ClaudeStatuslineAdapter.read()`: snapshot ファイル読取。`observed_at` = ファイル mtime。window_minutes: five_hour=300, seven_day=10080

- [ ] **Step 1: 失敗テスト**

```python
SAMPLE = {"rate_limits": {"five_hour": {"used_percentage": 62, "resets_at": 1792580676},
                          "seven_day": {"used_percentage": 22, "resets_at": 1793090602}}}

def test_hook_writes_snapshot(tmp_path):
    out = tmp_path / "snap.json"
    statusline_hook(json.dumps(SAMPLE), out)
    snap = ClaudeStatuslineAdapter(out).read()
    assert snap.windows["five_hour"].used_pct == 62
    assert snap.windows["seven_day"].resets_at == datetime.fromtimestamp(1793090602, UTC)

def test_hook_without_rate_limits_keeps_previous(tmp_path):
    out = tmp_path / "snap.json"
    statusline_hook(json.dumps(SAMPLE), out)
    statusline_hook(json.dumps({"model": {}}), out)
    assert ClaudeStatuslineAdapter(out).read().windows["five_hour"].used_pct == 62

def test_unknown_adapter_raises():
    with pytest.raises(KeyError):
        create_adapter("gemini", default_cfg())
```

- [ ] **Step 2:** `uv run pytest tests/adapters -v` → FAIL
- [ ] **Step 3:** 実装。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(adapters): add registry and claude statusline adapter"`

---

### Task 4: Claude 使用量 API アダプタ + Codex ログアダプタ

**Files:**
- Create: `adapters/claude_usage_api.py`, `adapters/codex_log.py`
- Test: `tests/adapters/test_claude_usage_api.py`, `tests/adapters/test_codex_log.py`, `tests/fixtures/codex_session.jsonl`

**Interfaces:**
- Produces:
  - `parse_usage_response(body: Mapping[str, Any], observed_at: datetime) -> QuotaSnapshot`: `five_hour`/`seven_day` に加え、構造化 `limits` 配列があれば **全窓**（モデル別週間枠含む）を `windows` に入れる（spec 4.6 モデル別上限）
  - `ClaudeUsageApiAdapter(cfg, fetch: Callable[[str], Mapping[str, Any]])`: `cfg.usage_api_url` 空 → `read()` は `None`。fetch 例外 → ログ警告 + `None`
  - `parse_codex_session(path: Path) -> QuotaSnapshot | None`: 最終 `rate_limits` 行。`primary`→`five_hour`, `secondary`→`seven_day`、`used_percent`, `window_minutes`, `resets_at`
  - `CodexLogAdapter(sessions_dir: Path)`: 最新 jsonl を解析

- [ ] **Step 1: 失敗テスト** — `test_parse_includes_model_scoped_limits`（limits 配列の各要素が windows に入る）、`test_fetch_error_returns_none`、`test_empty_url_returns_none`、`test_codex_last_rate_limits_wins`（fixture に2行、後者採用: primary used 62.0 / window 300 / resets_at 1792580676）
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。エンドポイント URL・認証ヘッダはコードに埋めず設定値（非公開 API の旨 docstring 明記）。`fetch` 既定実装 = `httpx.get(..., timeout=10)`。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(adapters): add claude usage api and codex log adapters"`

---

### Task 5: 帰属推定（本人使用量 d、token→% 係数）

**Files:**
- Create: `src/ai_quota_scheduler/attribution.py`
- Test: `tests/test_attribution.py`

**Interfaces:**
- Consumes: `Store.snapshots_between`, `Store.harness_tokens_between`
- Produces:
  - `class TokenToPct`: `update(delta_total_pct: float, harness_tokens: int, user_idle: bool) -> None`, `pct(tokens: int) -> float`, `coef: float`
  - `estimate_user_usage(delta_total_pct: float, harness_tokens: int, conv: TokenToPct) -> float`（= max(0, Δ合計 − conv.pct(tokens))）

- [ ] **Step 1: 失敗テスト**

```python
def test_user_usage_is_total_minus_harness():
    conv = TokenToPct(coef=1e-5)
    assert estimate_user_usage(3.0, 100_000, conv) == pytest.approx(2.0)

def test_never_negative():
    assert estimate_user_usage(0.0, 100_000, TokenToPct(coef=1e-5)) == 0.0

def test_coef_learns_only_when_user_idle():
    conv = TokenToPct(coef=1e-5)
    conv.update(5.0, 100_000, user_idle=False)
    assert conv.coef == 1e-5

def test_zero_delta_rounding_does_not_diverge():   # Review Focus 2
    conv = TokenToPct(coef=1e-5)
    for _ in range(100):
        conv.update(0.0, 50_000, user_idle=True)
    assert 0 < conv.coef < 1e-3
```

- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。係数 = EMA(α=0.1) of Δ%/tokens、Δ%=0 の周期は % 丸め由来 → 更新スキップ（累積 tokens を次回へ繰越）。`user_idle` 判定は呼び出し側（本人 予測使用量 ≈ 0 かつ 夜間等）で決める。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(attribution): estimate user usage and token-to-pct coefficient"`

---

### Task 6: 本人使用量 予測（曜日×時間帯 分位点）

**Files:**
- Create: `src/ai_quota_scheduler/forecast.py`
- Test: `tests/test_forecast.py`

**Interfaces:**
- Produces:
  - `class UsageForecaster(tz: ZoneInfo)`: `fit(samples: Sequence[tuple[datetime, float]]) -> None`（sample = (区間開始 UTC, その区間の本人使用 %)、区間長 = replan_interval）
  - `upper(start: datetime, end: datetime, q: float) -> float`: 区間 [start, end) の本人使用 % 上側分位点の合計
  - バケット = (ローカル曜日, ローカル時)。サンプル不足（< 3）バケット → 全バケット同 q の値（保守側フォールバック）

- [ ] **Step 1: 失敗テスト** — `test_upper_uses_local_hour_bucket`（Asia/Tokyo 12時に使用集中の履歴 → 12-13時 JST の upper > 03-04時 JST、UTC 入力で検証 = Review Focus 4）、`test_sparse_bucket_falls_back_to_global`、`test_upper_monotone_in_q`（q=0.95 ≥ q=0.90）、`test_upper_zero_length_interval_is_zero`
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。経験分位点（`statistics.quantiles` or ソート補間）。部分区間は按分。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(forecast): add weekday-hour quantile usage forecaster"`

---

### Task 7: harness 消費速度 r + ホライズン シミュレーション

**Files:**
- Create: `src/ai_quota_scheduler/rate.py`, `mpc/__init__.py`, `mpc/simulate.py`
- Test: `tests/test_rate.py`, `tests/mpc/test_simulate.py`

**Interfaces:**
- Produces:
  - `class RateEstimator(r_init: float)`: `observe(pct: float, worker_minutes: float) -> None`, `r: float`（%/分/並列1、EMA α=0.2、初期 r_init、下限 r_init/4 上限なし）
  - `@dataclass(frozen=True) class SimResult: hit_at: datetime | None; harness_pct: float; final_used_pct: float`
  - `simulate(now: datetime, used_pct: float, resets_at: datetime, parallel: int, r: float, user_upper: Callable[[datetime, datetime], float], step: timedelta, reserve_pct: float) -> SimResult`

`simulate` アルゴリズム（シグネチャで決まらない部分）:

```python
used = used_pct + reserve_pct          # 実行中 rollout 最悪消費（harness 予算側）
t = now
while t < resets_at:
    dt = min(step, resets_at - t)
    used += user_upper(t, t + dt) + parallel * r * dt.total_seconds() / 60
    t += dt
    if used >= 100.0:
        return SimResult(hit_at=t, ...)
return SimResult(hit_at=None, ...)
```

- [ ] **Step 1: 失敗テスト** — `test_no_hit_when_idle`（parallel=0, user=0 → hit_at None）、`test_hit_time_matches_rate`（used=50, r=1, parallel=1, user=0, reset 120分後 → hit_at ≈ now+50分）、`test_reserve_moves_hit_earlier`、`test_rate_ema_and_floor`
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(mpc): add rate estimator and horizon simulation"`

---

### Task 8: 上位 MPC（週間 → 5h 予算）+ 下位 MPC（5h → 並列数）

**Files:**
- Create: `mpc/upper.py`, `mpc/lower.py`
- Test: `tests/mpc/test_upper.py`, `tests/mpc/test_lower.py`

**Interfaces:**
- Consumes: `simulate`, `SimResult`, `UsageForecaster.upper`
- Produces:
  - `weekly_budget(now: datetime, week: WindowState, user_upper: Callable[[datetime, datetime], float]) -> float`: 週リセットまで harness に回せる %（= max(0, 100 − used − 本人上側予測)）。週リセット時 残量≈0 目標 → 残りを全額 harness 予算候補
  - `block_budget(now: datetime, week_budget_pct: float, week: WindowState, five: WindowState) -> float`: 今の 5h ブロックへ配分。残り 5h ブロック数で按分、ただし週予算超えない
  - `choose_parallel(now: datetime, five: WindowState, budget_pct: float, r: float, user_upper: Callable[[datetime, datetime], float], cfg: SchedulerConfig, reserve_pct: float) -> tuple[int, str]`: u ∈ 0..cfg.max_parallel を総当たり。採用条件 = (hit_at is None or resets_at − hit_at ≤ W) かつ harness_pct ≤ budget_pct。条件満たす最大 u。理由文字列 返す

- [ ] **Step 1: 失敗テスト**

```python
def test_lower_full_when_reset_near():
    # used 90%, reset 20分後, r=1/並列, 本人0 → 並列1で10分後到達, 待ち10分 ≤ 15 → 採用
    u, _ = choose_parallel(NOW, win(90, NOW + 20*MIN), 100.0, 1.0, zero_user, cfg(max_parallel=2), 0.0)
    assert u == 2   # 並列2: 5分後到達 → 待ち15分 ≤ 15 → 採用

def test_lower_zero_when_wait_exceeds_w():
    u, _ = choose_parallel(NOW, win(50, NOW + 300*MIN), 100.0, 1.0, zero_user, cfg(max_parallel=1), 0.0)
    assert u == 0   # 50分後到達 → 待ち250分 > 15

def test_lower_respects_budget():
    u, _ = choose_parallel(NOW, win(10, NOW + 60*MIN), 5.0, 1.0, zero_user, cfg(max_parallel=4), 0.0)
    assert u == 0   # 並列1でも 60% 消費 > 予算5%

def test_weekly_budget_reserves_user_upper():
    assert weekly_budget(NOW, win(60, NOW + DAY), const_user(30.0)) == pytest.approx(10.0)

def test_week_nearly_exhausted_caps_block():          # Review Focus 5
    b = block_budget(NOW, weekly_budget(NOW, win(97, NOW + 2*HOUR), const_user(2.0)),
                     win(97, NOW + 2*HOUR), win(10, NOW + 4*HOUR))
    assert b <= 1.0 + 1e-9
```

- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(mpc): add weekly budget and 5h parallel selection"`

---

### Task 9: 安全ゲート

**Files:**
- Create: `src/ai_quota_scheduler/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Produces: `gate(now: datetime, snap: QuotaSnapshot | None, cfg: SchedulerConfig, first_seen: datetime | None, kill_file: Path, rate_limited_until: datetime | None) -> str | None`（停止理由 or None=通過）。理由定数: `NO_OBSERVATION`, `STALE`, `WINDOW_EXPIRED`, `COLD_START`, `KILL_SWITCH`, `RATE_LIMITED`

- [ ] **Step 1: 失敗テスト** — 各理由 1 テスト:
  - `snap=None` → `NO_OBSERVATION`
  - `observed_at` が `stale_after_s`(900) 超過 → `STALE`
  - `now > five_hour.resets_at`（観測前リセット）→ `WINDOW_EXPIRED`（Review Focus 1）
  - `now − first_seen < 7日` → `COLD_START`
  - kill_file 存在 → `KILL_SWITCH`
  - `rate_limited_until > now` → `RATE_LIMITED`
  - 全条件クリア → `None`
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。評価順 = KILL_SWITCH → NO_OBSERVATION → STALE → WINDOW_EXPIRED → RATE_LIMITED → COLD_START。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(safety): add stop gate"`

---

### Task 10: 制御ファイル プロトコル（harness2 との境界）

**Files:**
- Create: `src/ai_quota_scheduler/control.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Produces（**別計画 harness2 側がこの形式を読む**）:
  - ファイル: `<job_dir>/aqs_control.json`
  - 形式: `{"version": 1, "paused": bool, "max_parallel": int, "updated_at": float(epoch秒)}`
  - 意味: `paused=true` → 新規 rollout / 改善セッション 開始禁止（実行中は完了まで継続）。`max_parallel` → 同時実行上限。
  - `write_control(job_dir: Path, paused: bool, max_parallel: int, now: datetime) -> None`（原子的書込）
  - `read_control(job_dir: Path) -> dict[str, Any] | None`

- [ ] **Step 1: 失敗テスト** — `test_roundtrip`、`test_atomic_write_no_partial`（書込中に読んでも JSON 破損なし: tmp+rename 確認）、`test_version_field_is_1`
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(control): define harness2 control file protocol"`

---

### Task 11: ジョブキュー + ランナー

**Files:**
- Create: `src/ai_quota_scheduler/runner.py`
- Modify: `src/ai_quota_scheduler/store.py`（jobs テーブル追加）
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `write_control`, `Store`
- Produces:
  - `Store.enqueue(job_id: str, args: Sequence[str]) -> None` / `Store.next_queued() -> tuple[str, list[str]] | None` / `Store.set_job_state(job_id: str, state: str, pid: int | None) -> None` / `Store.running_jobs() -> list[tuple[str, int]]`
  - `class Runner(cfg: SchedulerConfig, store: Store)`: `apply(parallel: int, now: datetime) -> None`
    - parallel=0 → 全 running ジョブへ `paused=true`
    - parallel>0 かつ running なし → キュー先頭を起動（`cfg.job_command + args`、env `CLAUDE_CONFIG_DIR=cfg.harness_config_dir`）、制御ファイル `paused=false, max_parallel=parallel`
    - running あり → 制御ファイル更新のみ
    - 同時起動ジョブ 最大 1（並列度は harness2 内部で制御）【計画判断】
  - `Runner.reconcile() -> None`: 起動時、DB の running PID 生存確認。死亡 → `failed`、生存 → 引継ぎ（Review Focus 3）
  - `Runner.reserve_pct(r: float) -> float`: 実行中 rollout 最悪消費見込み = running ジョブの `max_parallel × r × 想定 rollout 長(分)`。想定長 既定 60分（設定 `rollout_max_min`）

- [ ] **Step 1: 失敗テスト** — `test_starts_queued_job_with_isolated_config_dir`（subprocess を fake で差し替え、env 検証）、`test_pause_writes_control`、`test_reconcile_marks_dead_pid_failed`、`test_reconcile_adopts_live_pid_no_double_start`、`test_reserve_zero_when_idle`
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。`SchedulerConfig` に `rollout_max_min: int = 60` 追加。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(runner): add job queue and runner"`

---

### Task 12: デーモン tick + 判断記録

**Files:**
- Create: `src/ai_quota_scheduler/daemon.py`
- Test: `tests/test_daemon.py`

**Interfaces:**
- Consumes: 全前タスク
- Produces:
  - `tick(now: datetime, ctx: DaemonContext) -> Decision`: 観測（設定 adapters 順、最初の非 None 採用）→ `Store.add_snapshot` → `gate` → 帰属・r・予測更新 → `weekly_budget` → `block_budget` → `choose_parallel`（上限 `cfg.max_parallel`）→ `Runner.apply` → `Store.add_decision`。gate 理由あり → parallel=0、reason=理由
  - `DaemonContext`: cfg, store, adapters, forecaster, conv, rate, runner, kill_file, rate_limited_until
  - `run_forever(ctx: DaemonContext) -> None`: `tick` → `sleep(cfg.replan_interval_s)`。例外 → ログ + parallel=0 適用 + 継続
  - 429 検知: ジョブ stderr/ログに `429` or `rate_limit` → `rate_limited_until = now + 15分`

- [ ] **Step 1: 失敗テスト** — fake adapter / fake runner で:
  - `test_cold_start_records_but_parallel_zero`
  - `test_after_cold_start_runs_one_when_reset_near`
  - `test_adapter_failure_falls_back_to_next_adapter`
  - `test_all_adapters_fail_parallel_zero`
  - `test_exception_in_tick_applies_zero`
  - `test_decision_logged_every_tick`
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装。
- [ ] **Step 4:** → PASS
- [ ] **Step 5:** `git commit -m "feat(daemon): add control loop tick"`

---

### Task 13: CLI `aqs` + systemd unit + 窓口サブエージェント

**Files:**
- Create: `src/ai_quota_scheduler/cli.py`, `deploy/ai-quota-scheduler.service`, `.claude/agents/quota-scheduler.md`, `README.md`
- Modify: `pyproject.toml`（`[project.scripts] aqs = "ai_quota_scheduler.cli:main"`）
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces（サブコマンド）:
  - `aqs daemon --config PATH`
  - `aqs status [--json]`: 最新観測（各窓 used_pct / resets_at）、最新 Decision、running/queued ジョブ、今週 harness 消費 %
  - `aqs queue add -- ARGS...` / `aqs queue list`
  - `aqs kill` / `aqs unkill`（キルスイッチファイル作成/削除）
  - `aqs statusline-hook --out PATH`（stdin → `statusline_hook`。stdout へ1行表示も返す）
- systemd: `Type=simple`, `Restart=on-failure`, `ExecStart=%h/.local/bin/aqs daemon --config %h/.config/ai-quota-scheduler/config.toml`（user unit）
- サブエージェント: `aqs status --json` / `aqs queue add` / `aqs kill` のみ使用する窓口。制御ループは持たない旨 明記

- [ ] **Step 1: 失敗テスト** — `test_status_json_keys`（`windows`, `decision`, `jobs`, `harness_week_pct`）、`test_queue_add_then_list`、`test_kill_creates_file`、`test_statusline_hook_cli_writes_snapshot`
- [ ] **Step 2:** → FAIL
- [ ] **Step 3:** 実装 + unit / agent / README（セットアップ手順: uv tool install、config.toml 例、statusline 設定、harness 専用 `CLAUDE_CONFIG_DIR` ログイン、`systemctl --user enable --now`）
- [ ] **Step 4:** `uv run pytest -v` 全体 → PASS
- [ ] **Step 5:** `git commit -m "feat(cli): add aqs cli, systemd unit and subagent"`

---

## 別計画（次）

`harness2-claude-code`（harness2 fork 側）:
1. Claude Code substrate（surface / materialise / read_trajectory、`claude -p --output-format stream-json`）
2. 改善役 `claude_cli` サブスク認証モード（`CLAUDE_CODE_USE_VERTEX` 外す）
3. `core/schedule.py` に `aqs_control.json` 読取口（paused / max_parallel、Task 10 プロトコル）
4. トークン使用量の書出し（ai-quota-scheduler `add_harness_usage` 用）
