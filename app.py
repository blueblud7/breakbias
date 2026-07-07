#!/usr/bin/env python3
"""Market Sentiment Radar — Streamlit 대시보드 (M4).

실행: streamlit run app.py

4페이지:
  1) 오늘의 뷰 (게이지 + 스택바 + 총평/반론 Top3)
  2) 시계열 추이 (스택영역 + NSI/가격 오버레이 + 기관vs리테일 + 관심도)
  3) 소스 드릴다운 (히트맵 + 아이템 테이블, 내 뷰 반대 우선 정렬)
  4) 설정 (테마/내 뷰/가중치)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from sentiment_radar import dashboard_data as dd
from sentiment_radar.config import list_themes, load_theme, settings
from sentiment_radar.db import get_db

POS, NEU, NEG = "#2ca02c", "#9e9e9e", "#d62728"  # 녹/회/빨

st.set_page_config(page_title="Market Sentiment Radar", layout="wide")


@st.cache_resource
def _db():
    return get_db()


def _theme_pick() -> str:
    themes = list_themes() or ["semiconductor"]
    return st.sidebar.selectbox("테마", themes, index=0)


# ---------------- 페이지 1 : 오늘의 뷰 ----------------
def page_today(db, theme: str):
    st.header("오늘의 뷰")
    g = dd.get_today_gauge(db, theme)
    if not g:
        st.warning("집계 데이터가 없습니다. `python scripts/seed_demo.py` 로 데모 데이터를 넣거나 수집/분류를 먼저 실행하세요.")
        return

    c1, c2 = st.columns([1, 1])
    with c1:
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=g["nsi_wt"],
            delta={"reference": g["nsi_wt"] - (g["delta_wt"] or 0),
                   "increasing": {"color": POS}, "decreasing": {"color": NEG}},
            title={"text": f"가중 NSI ({g['date']})"},
            gauge={
                "axis": {"range": [-100, 100]},
                "bar": {"color": "#444"},
                "steps": [
                    {"range": [-100, -20], "color": "#f4cccc"},
                    {"range": [-20, 20], "color": "#e0e0e0"},
                    {"range": [20, 100], "color": "#d9ead3"},
                ],
            },
        ))
        fig.update_layout(height=300, margin=dict(t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)
        if g["delta_wt"] is not None:
            arrow = "▲" if g["delta_wt"] > 0 else ("▼" if g["delta_wt"] < 0 else "▬")
            st.metric("어제 대비 NSI", f"{g['nsi_wt']}", f"{arrow} {g['delta_wt']}")
        if g["extreme"]:
            st.error("⚠️ 쏠림 경보: 한 방향 75% 이상 — 역발상 관점 점검")
        if g["divergence"] is not None:
            st.info(f"기관-리테일 괴리: {g['divergence']}pt")

    with c2:
        # Raw vs 가중 100% 스택바
        fig = go.Figure()
        for label, vals in [("Raw", g["pct_raw"]), ("가중", g["pct_wt"])]:
            fig.add_bar(y=[label], x=[vals[0]], name="긍정", orientation="h",
                        marker_color=POS, showlegend=(label == "Raw"))
            fig.add_bar(y=[label], x=[vals[1]], name="중립", orientation="h",
                        marker_color=NEU, showlegend=(label == "Raw"))
            fig.add_bar(y=[label], x=[vals[2]], name="부정", orientation="h",
                        marker_color=NEG, showlegend=(label == "Raw"))
        fig.update_layout(barmode="stack", height=220, title="긍/중/부 비율 (Raw vs 가중)",
                          margin=dict(t=40, b=10), xaxis=dict(range=[0, 100]))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"집계 아이템 {g['n_items']}건 · Raw와 가중의 차이가 크면 그 자체가 정보")

    # 총평 + 반론 Top 3 (항상 노출)
    st.subheader("오늘의 총평")
    com = dd.get_commentary(db, theme)
    if com:
        st.write(com["commentary"])
        st.markdown("#### 🔁 다수 의견에 대한 반론 Top 3")
        if com["counter_arguments"]:
            for i, ca in enumerate(com["counter_arguments"], 1):
                st.markdown(f"**{i}. {ca.get('claim','')}**  \n　근거: {ca.get('basis','')}")
        else:
            st.caption("반론 데이터 없음")
        st.caption(f"모델: {com['model']} · {com['date']}")
    else:
        st.caption("총평이 아직 생성되지 않았습니다. (deepseek 총평 또는 데모 시드 필요)")


# ---------------- 페이지 2 : 시계열 ----------------
def page_timeseries(db, theme: str):
    st.header("시계열 추이")
    ts = dd.get_timeseries(db, theme, "all", limit=60)
    if not ts:
        st.warning("데이터 없음.")
        return
    df = pd.DataFrame(ts)

    # 100% 스택 영역
    fig = go.Figure()
    fig.add_scatter(x=df["bucket_date"], y=df["pct_pos_wt"], name="긍정",
                    stackgroup="one", mode="none", fillcolor=POS)
    fig.add_scatter(x=df["bucket_date"], y=df["pct_neu_wt"], name="중립",
                    stackgroup="one", mode="none", fillcolor=NEU)
    fig.add_scatter(x=df["bucket_date"], y=df["pct_neg_wt"], name="부정",
                    stackgroup="one", mode="none", fillcolor=NEG)
    fig.update_layout(title="여론 지형 (100% 스택, 가중)", height=300,
                      yaxis=dict(range=[0, 100]), margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

    # NSI + 가격 오버레이 (이중 y축)
    prices = dd.get_prices(db, load_theme(theme).price_symbols.get("kospi", "1001"))
    fig2 = make_subplots(specs=[[{"secondary_y": True}]])
    fig2.add_scatter(x=df["bucket_date"], y=df["nsi_wt"], name="가중 NSI",
                     line=dict(color="#1f77b4"), secondary_y=False)
    # 쏠림 경보 마커
    ex = df[df["extreme_flag"] == 1]
    if not ex.empty:
        fig2.add_scatter(x=ex["bucket_date"], y=ex["nsi_wt"], name="쏠림⚠",
                         mode="markers", marker=dict(color=NEG, size=10, symbol="triangle-up"),
                         secondary_y=False)
    if prices:
        pdf = pd.DataFrame(prices)
        fig2.add_scatter(x=pdf["bucket_date"], y=pdf["close"], name="KOSPI",
                         line=dict(color="#ff7f0e", dash="dot"), secondary_y=True)
    fig2.update_layout(title="센티먼트-가격 다이버전스", height=320, margin=dict(t=40, b=10))
    fig2.update_yaxes(title_text="NSI", secondary_y=False)
    fig2.update_yaxes(title_text="지수", secondary_y=True)
    st.plotly_chart(fig2, use_container_width=True)

    # 기관 vs 리테일
    inst = pd.DataFrame(dd.get_timeseries(db, theme, "institutional", 60))
    retail = pd.DataFrame(dd.get_timeseries(db, theme, "retail", 60))
    fig3 = go.Figure()
    if not inst.empty:
        fig3.add_scatter(x=inst["bucket_date"], y=inst["nsi_wt"], name="기관",
                         line=dict(color="#1f77b4"))
    if not retail.empty:
        fig3.add_scatter(x=retail["bucket_date"], y=retail["nsi_wt"], name="리테일",
                         line=dict(color="#e377c2"))
    fig3.update_layout(title="기관 vs 리테일 NSI (괴리)", height=280, margin=dict(t=40, b=10))
    st.plotly_chart(fig3, use_container_width=True)

    # 관심도
    att = dd.get_attention_ratio(db, theme)
    if att:
        adf = pd.DataFrame(att)
        fig4 = go.Figure(go.Scatter(x=adf["bucket_date"], y=adf["value"],
                                    name="관심도 비율", line=dict(color="#9467bd")))
        fig4.add_hline(y=0.5, line_dash="dash", line_color="gray")
        fig4.update_layout(title="Google Trends 방향성 관심도 (>0.5 강세)", height=240,
                           yaxis=dict(range=[0, 1]), margin=dict(t=40, b=10))
        st.plotly_chart(fig4, use_container_width=True)


# ---------------- 페이지 3 : 소스 드릴다운 ----------------
def page_sources(db, theme: str):
    st.header("소스 드릴다운")
    mat = dd.get_source_matrix(db, theme, limit=30)
    if mat["sources"] and mat["dates"]:
        z = [[mat["nsi"][s].get(d) for d in mat["dates"]] for s in mat["sources"]]
        fig = go.Figure(go.Heatmap(z=z, x=mat["dates"], y=mat["sources"],
                                   colorscale="RdYlGn", zmid=0, zmin=-100, zmax=100,
                                   colorbar=dict(title="NSI")))
        fig.update_layout(title="소스별 NSI 히트맵 (선행/후행 패턴 발견용)", height=320,
                          margin=dict(t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)

    my_view = load_theme(theme).my_view
    st.subheader("아이템")
    if my_view in ("positive", "negative"):
        st.caption(f"내 뷰='{my_view}' → **반대 센티먼트 우선 정렬** (확증편향 방지)")
    cols = st.columns(3)
    sflt = cols[0].selectbox("센티먼트", ["전체", "positive", "neutral", "negative"])
    srcs = ["전체"] + sorted(set(r["source_type"] for r in db.fetch_classified_detailed(theme)))
    srcflt = cols[1].selectbox("소스", srcs)
    limit = cols[2].slider("표시 개수", 10, 300, 100)

    items = dd.get_items(
        db, theme, my_view=my_view,
        sentiment=None if sflt == "전체" else sflt,
        source_type=None if srcflt == "전체" else srcflt, limit=limit,
    )
    if items:
        df = pd.DataFrame(items)[
            ["sentiment", "source_type", "one_line_summary", "confidence",
             "reach_score", "url"]
        ]
        st.dataframe(df, use_container_width=True, height=420,
                     column_config={"url": st.column_config.LinkColumn("원문")})
    else:
        st.caption("표시할 아이템이 없습니다.")


# ---------------- 페이지 4 : 설정 ----------------
def page_settings(db, theme: str):
    st.header("설정")
    th = load_theme(theme)
    st.subheader(f"테마: {th.display_name} ({th.theme})")

    st.markdown("**내 뷰 (확증편향 방지 정렬 기준)**")
    new_view = st.radio("내 현재 뷰", ["none", "positive", "negative", "neutral"],
                        index=["none", "positive", "negative", "neutral"].index(th.my_view),
                        horizontal=True)
    if st.button("내 뷰 저장"):
        _save_my_view(theme, new_view)
        st.success(f"저장됨: my_view={new_view} (페이지 3 정렬에 반영, 새로고침)")

    st.markdown("**키워드**")
    st.write("국내:", ", ".join(th.keywords_ko))
    st.write("해외:", ", ".join(th.keywords_en))

    st.markdown("**소스 가중치 (현재값)**")
    st.json(settings().get("source_weights", {}))
    st.caption("가중치/채널 화이트리스트 편집은 config/settings.yaml 에서 관리합니다.")


def _save_my_view(theme: str, view: str) -> None:
    from sentiment_radar.config import THEMES_DIR
    import yaml
    path = THEMES_DIR / f"{theme}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["my_view"] = view
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def main():
    st.sidebar.title("📡 Sentiment Radar")
    theme = _theme_pick()
    page = st.sidebar.radio("페이지", ["오늘의 뷰", "시계열 추이", "소스 드릴다운", "설정"])
    db = _db()
    if page == "오늘의 뷰":
        page_today(db, theme)
    elif page == "시계열 추이":
        page_timeseries(db, theme)
    elif page == "소스 드릴다운":
        page_sources(db, theme)
    else:
        page_settings(db, theme)

    st.sidebar.markdown("---")
    st.sidebar.caption("⚠️ 이 시스템의 출력은 참고용 정량화 도구이며 **매매 신호가 아닙니다**.")


if __name__ == "__main__":
    main()
