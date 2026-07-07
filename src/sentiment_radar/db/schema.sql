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

-- 가격 시계열 (센티먼트-가격 다이버전스 오버레이용)
CREATE TABLE IF NOT EXISTS price_series (
    id              INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,        -- kospi|^SOX 등
    bucket_date     TEXT NOT NULL,
    close           REAL,
    collected_at    TEXT NOT NULL,
    UNIQUE(symbol, bucket_date)
);

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
