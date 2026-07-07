-- ==========================================================
-- Market Sentiment Radar — SQLite 스키마
-- Supabase(Postgres) 이전을 고려해 표준 타입 위주로 설계.
--  - AUTOINCREMENT 대신 INTEGER PRIMARY KEY (Postgres 이전 시 SERIAL 로 치환)
--  - 시간은 ISO8601 TEXT (UTC) 로 저장
--  - JSON 은 TEXT 로 저장 (Postgres jsonb 이전 가능)
-- ==========================================================

-- 테마별 키워드/설정 (config 파일과 동기화되지만 런타임 편집 지원)
CREATE TABLE IF NOT EXISTS keywords_config (
    id            INTEGER PRIMARY KEY,
    theme         TEXT NOT NULL,
    display_name  TEXT,
    config_json   TEXT NOT NULL,          -- 테마 전체 설정 스냅샷
    my_view       TEXT DEFAULT 'none',    -- positive|negative|neutral|none
    updated_at    TEXT NOT NULL,
    UNIQUE(theme)
);

-- 수집된 원본 아이템
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY,
    theme           TEXT NOT NULL,
    source_type     TEXT NOT NULL,        -- news_kr|news_global|report|blog|youtube|reddit|telegram
    source_name     TEXT,                 -- 매체/채널명
    title           TEXT NOT NULL,
    content_snippet TEXT,                 -- 최대 3000자
    url             TEXT,
    url_normalized  TEXT,                 -- dedup 용 정규화 URL
    author          TEXT,
    published_at    TEXT,                 -- ISO8601 UTC
    collected_at    TEXT NOT NULL,        -- ISO8601 UTC
    reach_score     REAL DEFAULT 0.0,     -- 조회수/구독자 등 정규화 값
    lang            TEXT,                 -- ko|en
    keyword_matched TEXT,                 -- 매칭된 키워드
    content_hash    TEXT,                 -- 제목+URL 기반 중복 판별 해시
    UNIQUE(theme, url_normalized)         -- 같은 테마 내 동일 URL 재수집 방지
);

CREATE INDEX IF NOT EXISTS idx_items_theme_pub    ON items(theme, published_at);
CREATE INDEX IF NOT EXISTS idx_items_theme_source ON items(theme, source_type);
CREATE INDEX IF NOT EXISTS idx_items_hash         ON items(content_hash);

-- gpt-5-nano 개별 분류 결과 (아이템 1:1)
CREATE TABLE IF NOT EXISTS classifications (
    id              INTEGER PRIMARY KEY,
    item_id         INTEGER NOT NULL,
    sentiment       TEXT NOT NULL,        -- positive|neutral|negative
    confidence      REAL NOT NULL,        -- 0.0~1.0
    one_line_summary TEXT,
    key_argument    TEXT,
    time_horizon    TEXT,                 -- short|mid|long|unclear
    is_opinion      INTEGER DEFAULT 1,    -- 0/1 (사실보도=0)
    model           TEXT,                 -- 사용 모델명
    classified_at   TEXT NOT NULL,
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE,
    UNIQUE(item_id)
);

CREATE INDEX IF NOT EXISTS idx_class_item ON classifications(item_id);

-- 일별 집계 (테마 x 날짜 x 스코프)
-- scope: all | institutional | retail | <source_type>
CREATE TABLE IF NOT EXISTS daily_aggregates (
    id              INTEGER PRIMARY KEY,
    theme           TEXT NOT NULL,
    bucket_date     TEXT NOT NULL,        -- YYYY-MM-DD (KST 기준)
    scope           TEXT NOT NULL DEFAULT 'all',
    n_items         INTEGER DEFAULT 0,

    -- Raw (건수 기준)
    pct_pos_raw     REAL DEFAULT 0.0,
    pct_neu_raw     REAL DEFAULT 0.0,
    pct_neg_raw     REAL DEFAULT 0.0,
    nsi_raw         REAL DEFAULT 0.0,     -- pos% - neg%  (-100~+100)

    -- Weighted (source_weight x log(1+reach) x confidence)
    pct_pos_wt      REAL DEFAULT 0.0,
    pct_neu_wt      REAL DEFAULT 0.0,
    pct_neg_wt      REAL DEFAULT 0.0,
    nsi_wt          REAL DEFAULT 0.0,

    extreme_flag    INTEGER DEFAULT 0,    -- 쏠림 경보
    divergence      REAL,                 -- 기관 NSI - 리테일 NSI (scope=all 행에만 채움)
    computed_at     TEXT NOT NULL,
    UNIQUE(theme, bucket_date, scope)
);

CREATE INDEX IF NOT EXISTS idx_agg_theme_date ON daily_aggregates(theme, bucket_date);

-- deepseek-v4-pro 일별 총평 (테마 x 날짜)
CREATE TABLE IF NOT EXISTS daily_commentary (
    id              INTEGER PRIMARY KEY,
    theme           TEXT NOT NULL,
    bucket_date     TEXT NOT NULL,
    commentary      TEXT NOT NULL,        -- 총평 본문
    counter_args    TEXT,                 -- "반론 Top 3" (JSON 배열)
    model           TEXT,
    generated_at    TEXT NOT NULL,
    UNIQUE(theme, bucket_date)
);

-- Google Trends 등 관심도(attention) 별도 트랙
CREATE TABLE IF NOT EXISTS attention_metrics (
    id              INTEGER PRIMARY KEY,
    theme           TEXT NOT NULL,
    bucket_date     TEXT NOT NULL,
    metric          TEXT NOT NULL,        -- e.g. google_trends, keyword_ratio
    keyword         TEXT,
    value           REAL,
    collected_at    TEXT NOT NULL,
    UNIQUE(theme, bucket_date, metric, keyword)
);

-- 가격 시계열 (센티먼트-가격 다이버전스 오버레이 + M7 백테스트용)
CREATE TABLE IF NOT EXISTS price_history (
    id              INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,        -- kospi|^SOX 등
    bucket_date     TEXT NOT NULL,        -- YYYY-MM-DD
    close           REAL,
    ret             REAL,                 -- 전일 대비 수익률 (M7 상관/이벤트 분석)
    collected_at    TEXT NOT NULL,
    UNIQUE(symbol, bucket_date)
);

CREATE INDEX IF NOT EXISTS idx_price_symbol_date ON price_history(symbol, bucket_date);

-- LLM 호출 비용 로그 (일 예산 관리)
CREATE TABLE IF NOT EXISTS llm_cost_log (
    id              INTEGER PRIMARY KEY,
    bucket_date     TEXT NOT NULL,        -- YYYY-MM-DD
    model           TEXT NOT NULL,
    call_type       TEXT,                 -- classify|commentary
    n_calls         INTEGER DEFAULT 0,
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0.0,
    logged_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cost_date ON llm_cost_log(bucket_date);

-- ==========================================================
-- M6 — 예측 일지 + 사전 규칙 (확증편향 교정 코어)
-- ==========================================================

-- 예측 일지: 오늘 데이터를 보기 '전에' 내 뷰를 먼저 기록한다.
-- 만기에 실제 지수 수익률과 대조 → 적중 판정 → Brier score 누적.
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY,
    theme           TEXT NOT NULL,
    created_at      TEXT NOT NULL,        -- 예측 기록 시각 (ISO8601 UTC)
    my_view         TEXT NOT NULL,        -- positive|negative
    confidence      REAL NOT NULL,        -- 50~100 (%)
    horizon_days    INTEGER NOT NULL,     -- 예측 기간 (예: 14, 30)
    basis           TEXT,                 -- 근거 한 줄

    -- 대조 대상 (지수)
    target_symbol   TEXT,                 -- kospi|^SOX 등
    entry_date      TEXT,                 -- 기준일 (YYYY-MM-DD)
    entry_close     REAL,                 -- 기준일 종가

    -- 만기/판정
    resolve_date    TEXT,                 -- 만기일 (YYYY-MM-DD)
    resolved_at     TEXT,                 -- 실제 판정 시각
    exit_close      REAL,
    actual_return   REAL,                 -- 만기 수익률 (%)
    outcome         TEXT DEFAULT 'pending', -- hit|miss|pending
    -- Brier: p=확신도(0~1), o=적중(1)/실패(0) → (p - o)^2
    brier           REAL
);

CREATE INDEX IF NOT EXISTS idx_pred_theme    ON predictions(theme, created_at);
CREATE INDEX IF NOT EXISTS idx_pred_resolve  ON predictions(outcome, resolve_date);

-- 사전 규칙: 상황 발생 '전에' 등록한 대응 규칙.
CREATE TABLE IF NOT EXISTS rules (
    id              INTEGER PRIMARY KEY,
    theme           TEXT,                 -- NULL 이면 전체 테마 공통
    name            TEXT NOT NULL,
    condition_json  TEXT NOT NULL,        -- 조건 DSL (JSON): metric/op/value/consecutive_days 등
    action_text     TEXT,                 -- 조건 충족 시 검토할 행동 (권고, 자동매매 아님)
    active          INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_triggered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_rules_active ON rules(active);

-- 규칙 수정 이력: 사후에 규칙을 바꾼 것을 스스로 볼 수 있게 스냅샷 보관.
CREATE TABLE IF NOT EXISTS rule_history (
    id              INTEGER PRIMARY KEY,
    rule_id         INTEGER NOT NULL,
    change_type     TEXT NOT NULL,        -- create|update|deactivate
    snapshot_json   TEXT NOT NULL,        -- 변경 시점의 규칙 전체 스냅샷
    changed_at      TEXT NOT NULL,
    FOREIGN KEY(rule_id) REFERENCES rules(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_rulehist_rule ON rule_history(rule_id, changed_at);
