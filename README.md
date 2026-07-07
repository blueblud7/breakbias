# Market Sentiment Radar (breakbias)

다중 소스(뉴스·리포트·블로그·유튜브·커뮤니티·Google Trends)에서 특정 테마에 대한
여론을 수집하고, **긍정 / 중립 / 부정 비율(%)** 로 정량화해 시계열로 시각화하는 시스템.

> **존재 이유:** 나는 확증편향에 갇히기 쉬운 인간이다. 이 시스템은 **내 뷰와 반대되는
> 논거를 강제로 보여주기 위해** 존재한다. 센티먼트는 순응 지표가 아니라
> **역발상(contrarian) 지표**로도 쓴다 — 극단적 쏠림은 반전 경고다.
>
> ⚠️ 출력은 참고용 정량화 도구이며 매매 신호가 아니다.

## 핵심 설계

- **소스 간 괴리 = 시그널**: 기관측(리포트+뉴스) vs 리테일측(블로그+유튜브+커뮤니티)의
  NSI 괴리를 별도 집계.
- **Net Sentiment Index (NSI)** = 긍정% − 부정% (−100 ~ +100).
- **Raw / 가중 두 지표 병기**: `weight = source_weight × log(1+reach) × confidence`.
- **쏠림 경보**: 한쪽이 75% 이상이면 `extreme_flag`.
- **관심도(attention)** 는 센티먼트와 분리된 별도 트랙 (Google Trends).

## LLM 2단 구조

| 단계 | 모델 | 용도 |
|---|---|---|
| 개별 분류 | `gpt-5-nano` | 아이템별 요약 + 센티먼트 (대량·저비용) |
| 일별 총평 | `deepseek-v4-pro` | 집계 기반 종합 분석 (하루 1회, "반론 Top 3" 강제) |

## 파이프라인

```
[Collectors] → [Dedup/필터] → [gpt-5-nano 분류] → [집계 엔진] → [deepseek 총평] → [DB] → [Streamlit]
```

## 빠른 시작

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # API 키 입력
python collect.py --list-themes
python collect.py --theme 반도체
```

## 프로젝트 구조

```
config/
  settings.yaml            # 소스 가중치, 집계·수집 정책
  themes/semiconductor.yaml # 테마별 키워드 (복사해서 테마 추가)
src/sentiment_radar/
  config.py                # 설정 로더
  models.py                # Item 표준 스키마
  db/{schema.sql,database.py}
  collectors/              # BaseCollector + naver_news + newsapi (+ M3에서 확장)
  pipeline/dedup.py        # URL 정규화 + rapidfuzz 제목 유사도
collect.py                 # 수집 CLI 엔트리포인트
tests/                     # pytest
```

## 마일스톤 진행

- [x] **M1** — 골격 + DB 스키마 + config + 뉴스 수집기 2종(Naver/NewsAPI) + dedup
- [ ] **M2** — gpt-5-nano 분류 파이프라인 + 집계 엔진 + NSI
- [ ] **M3** — 유튜브 / 블로그 / 리포트 스크레이퍼 / Google Trends / Reddit
- [ ] **M4** — deepseek 총평 + Streamlit 4페이지 + 가격 오버레이
- [ ] **M5** — APScheduler 자동 수집 + 실패 알림 + 30일 백필 + 비용 대시보드

## 테스트

```bash
pip install pytest rapidfuzz pyyaml python-dotenv
pytest -q
```

## 주의사항

- API 키는 전부 `.env` (커밋 금지, `.gitignore` 처리됨).
- 스크레이핑은 robots.txt 준수, 요청 간 2초+ 간격, User-Agent 명시.
- LLM 호출은 일 예산 초과 시 수집만 하고 분류는 다음날로 미룸.
- 모든 LLM 응답은 JSON 파싱 실패 대비 방어 코드 필수.
