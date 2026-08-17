#!/usr/bin/env python3
"""MNQ 브리핑용 데이터 스냅샷 수집기.

시세·금리·VIX·달러인덱스(야후 파이낸스), 다가오는 지표 일정(calendar_2026.yaml),
COT 포지셔닝(CFTC 공개 API), 롤오버 D-day를 모아 마크다운으로 출력한다.

숫자는 이 스냅샷을 기준으로 쓰고, 뉴스·예상치(컨센서스)·실적 일정은 웹서치로 보완할 것.

사용법:
    .venv/bin/python tools/snapshot.py            # 향후 7일 일정 포함
    .venv/bin/python tools/snapshot.py --days 14  # 향후 14일 일정 포함

최초 셋업:
    python3 -m venv .venv
    .venv/bin/pip install -r tools/requirements.txt
"""

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
WEEKDAY_KO = "월화수목금토일"

# (야후 심볼, 라벨, 표시 형식)  형식: pts=지수/가격, pct10=값/10을 %로, raw=그대로
SYMBOLS = [
    ("MNQ=F", "MNQ 선물 (마이크로 나스닥)", "pts"),
    ("^NDX", "└ 나스닥100 현물", "pts"),
    ("M2K=F", "M2K 선물 (마이크로 러셀2000)", "pts"),
    ("^RUT", "└ 러셀2000 현물", "pts"),
    ("MYM=F", "MYM 선물 (마이크로 다우)", "pts"),
    ("^DJI", "└ 다우 현물", "pts"),
    ("^IXIC", "나스닥 종합 (뉴스 기준 지수)", "pts"),
    ("^TNX", "미 10년물 국채금리", "pct10"),
    ("^VIX", "VIX (공포지수)", "raw"),
    ("DX-Y.NYB", "달러인덱스 (DXY)", "raw"),
    ("CL=F", "WTI 유가 (호르무즈 지표)", "raw"),
]

# 계약 사양 — 틱 가치는 셋 다 $0.50. 증거금은 변동하므로 HTS에서 확인할 것
SPECS = [
    # (상품, 지수심볼, 포인트당 $, 틱(포인트), 위탁증거금 참고치)
    ("MNQ", "^NDX", 2.0, 0.25, 3958),
    ("M2K", "^RUT", 5.0, 0.10, 1110),
    ("MYM", "^DJI", 0.5, 1.00, 1560),
]

COT_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"  # 레거시 보고서(선물만)
# CFTC 시장명은 유사 상품이 많아(러셀만 9종) 키워드 매칭은 오작동한다 → 정확한 명칭으로 지정
# 주의: "E-MINI RUSSELL 2000 INDEX"는 2022년에 보고가 끊긴 구 명칭 — 현행은 "RUSSELL E-MINI"
COT_MARKETS = [
    ("MNQ", "NASDAQ MINI"),
    ("M2K", "RUSSELL E-MINI"),
    ("MYM", "DJIA Consolidated"),
]


def fetch_market():
    """야후 파이낸스에서 최근 종가·등락률 수집."""
    import yfinance as yf

    symbols = [s for s, _, _ in SYMBOLS]
    try:
        data = yf.download(
            symbols, period="10d", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker", threads=True,
        )
    except Exception as e:  # 전체 실패
        return [], f"야후 파이낸스 조회 실패: {e}"

    rows = []
    for sym, label, fmt in SYMBOLS:
        try:
            close = data[sym]["Close"].dropna()
            if close.empty:
                raise ValueError("데이터 없음")
            last = float(close.iloc[-1])
            last_date = close.index[-1].date()
            prev = float(close.iloc[-2]) if len(close) >= 2 else None
            week_ago = float(close.iloc[-6]) if len(close) >= 6 else None
            rows.append({"label": label, "fmt": fmt, "last": last,
                         "prev": prev, "week": week_ago, "date": last_date})
        except Exception:
            rows.append({"label": label, "fmt": fmt, "na": True})
    return rows, None


def norm_yield(v):
    """^TNX는 데이터 소스에 따라 47.1 또는 4.71로 옴 — 20 초과면 10으로 나눠 %로 정규화."""
    return v / 10 if v > 20 else v


def fmt_value(row):
    v = row["last"]
    return f"{norm_yield(v):.2f}%" if row["fmt"] == "pct10" else f"{v:,.2f}"


def fmt_change(row, base_key):
    base = row.get(base_key)
    if base is None:
        return "-"
    if row["fmt"] == "pct10":  # 금리는 %p(퍼센트포인트) 변화로
        return f"{norm_yield(row['last']) - norm_yield(base):+.2f}%p"
    return f"{(row['last'] / base - 1) * 100:+.2f}%"


def fetch_cot_one(market_name):
    """CFTC 레거시 보고서에서 해당 시장 투기세력(비상업) 순포지션 최근 3주.

    market_name은 CFTC 시장명의 앞부분과 정확히 일치해야 한다. 러셀만 9개 시장이
    존재하는 등 유사 상품이 많아, 키워드 부분매칭은 엉뚱한 시장을 집는다.
    """
    import requests

    params = {
        "$select": ("market_and_exchange_names,report_date_as_yyyy_mm_dd,"
                    "noncomm_positions_long_all,noncomm_positions_short_all"),
        "$where": f"starts_with(market_and_exchange_names, '{market_name}')",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "10",
    }
    try:
        r = requests.get(COT_URL, params=params, timeout=30,
                         headers={"User-Agent": "InvestmentAnalyzer/1.0"})
        r.raise_for_status()
        recs = r.json()
    except Exception as e:
        return None, f"조회 실패: {e}"
    if not recs:
        return None, f"시장명 '{market_name}' 조회 결과 없음 — CFTC 명칭 변경 여부 확인 필요"

    def net_of(rec):
        return int(rec["noncomm_positions_long_all"]) - int(rec["noncomm_positions_short_all"])

    hist = [(rec["report_date_as_yyyy_mm_dd"][:10], net_of(rec)) for rec in recs[:3]]
    name = recs[0]["market_and_exchange_names"]

    # 보고가 끊긴 구 명칭을 잡는 사고 방지 — 최신 데이터가 30일 이상 오래되면 실패 처리
    latest = datetime.strptime(hist[0][0], "%Y-%m-%d").date()
    stale_days = (date.today() - latest).days
    if stale_days > 30:
        return None, (f"'{name}' 최신 데이터가 {hist[0][0]}로 {stale_days}일 경과 "
                      f"— 보고 중단된 구 명칭일 수 있음, COT_MARKETS 확인 필요")
    if len({n for _, n in hist}) == 1 and len(hist) > 1:
        return None, f"'{name}' 3주 값이 동일 — 잘못된 시장일 수 있음"
    return {"market": name, "hist": hist}, None


def daily_range_pts(symbol, days=20):
    """최근 N거래일 평균 일중 변동폭(고가-저가)을 지수 포인트로."""
    import yfinance as yf

    df = yf.download(symbol, period="2mo", interval="1d",
                     progress=False, auto_adjust=True).dropna()
    return float((df["High"] - df["Low"]).tail(days).mean().iloc[0])


def third_friday(year, month):
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def rollover_info(today):
    """현재 근월물(분기물)과 만기 D-day."""
    codes = {3: "H", 6: "M", 9: "U", 12: "Z"}
    for m in (3, 6, 9, 12):
        exp = third_friday(today.year, m)
        if exp >= today:
            break
    else:
        m, exp = 3, third_friday(today.year + 1, 3)
    return codes[m], exp, (exp - today).days


def load_events(days):
    """calendar_2026.yaml + 매주 목요일 실업수당 청구를 KST로 변환해 반환."""
    import yaml

    path = Path(__file__).parent / "calendar_2026.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))

    events = []
    for ev in cfg.get("events", []):
        d = ev["date"]
        if not isinstance(d, date):
            d = datetime.strptime(str(d), "%Y-%m-%d").date()
        hh, mm = map(int, str(ev["time"]).split(":"))
        dt_et = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
        events.append({"dt": dt_et.astimezone(KST), "name": ev["name"],
                       "imp": int(ev.get("importance", 1)),
                       "confirmed": bool(ev.get("confirmed", False))})

    # 매주 목요일 08:30 ET 신규 실업수당 청구 자동 추가
    today_et = datetime.now(ET).date()
    for i in range(days + 2):
        d = today_et + timedelta(days=i)
        if d.weekday() == 3:  # 목요일
            dt_et = datetime(d.year, d.month, d.day, 8, 30, tzinfo=ET)
            events.append({"dt": dt_et.astimezone(KST),
                           "name": "신규 실업수당 청구건수 (주간)",
                           "imp": 1, "confirmed": True})

    now = datetime.now(KST)
    horizon = now + timedelta(days=days)
    upcoming = [e for e in events if now - timedelta(hours=12) <= e["dt"] <= horizon]
    upcoming.sort(key=lambda e: e["dt"])
    return upcoming


def main():
    ap = argparse.ArgumentParser(description="MNQ 브리핑용 데이터 스냅샷")
    ap.add_argument("--days", type=int, default=7, help="일정 표시 범위(일), 기본 7")
    args = ap.parse_args()

    now = datetime.now(KST)
    print(f"# 데이터 스냅샷 — {now:%Y-%m-%d} ({WEEKDAY_KO[now.weekday()]}) {now:%H:%M} KST")

    # 1. 시장 데이터
    print("\n## 1. 시장 데이터 (최근 종가 기준 — 주말엔 금요일 종가)\n")
    rows, err = fetch_market()
    if err:
        print(f"⚠ {err}")
    else:
        print("| 항목 | 값 | 전일 대비 | 최근 5일 | 기준일 |")
        print("|---|---|---|---|---|")
        for r in rows:
            if r.get("na"):
                print(f"| {r['label']} | N/A (조회 실패 — 웹서치로 보완) | - | - | - |")
            else:
                print(f"| {r['label']} | {fmt_value(r)} | {fmt_change(r, 'prev')} "
                      f"| {fmt_change(r, 'week')} | {r['date']:%m/%d} |")

    # 2. 일정
    print(f"\n## 2. 다가오는 주요 일정 — 향후 {args.days}일 (한국시간)\n")
    try:
        events = load_events(args.days)
        if not events:
            print("(해당 기간에 등록된 일정 없음)")
        else:
            print("| 일시 (KST) | 이벤트 | 중요도 |")
            print("|---|---|---|")
            for e in events:
                dt = e["dt"]
                mark = "" if e["confirmed"] else " ⚠일정 재확인 필요"
                print(f"| {dt:%m/%d}({WEEKDAY_KO[dt.weekday()]}) {dt:%H:%M} "
                      f"| {e['name']}{mark} | {'★' * e['imp']} |")
            print("\n> 고중요도(★★★) 발표 전후 ±30분 신규 진입 금지 (PLAN.md 리스크 원칙)")
    except Exception as e:
        print(f"⚠ 일정 로드 실패: {e}")

    # 3. 계약 사양 & 하루 변동폭 (틱 기준)
    print("\n## 3. 상품별 하루 변동폭 (최근 20거래일 평균, 틱 기준)\n")
    print("| 상품 | 1틱 | 틱 가치 | 일평균 변동 | 달러 | 증거금 참고 |")
    print("|---|---|---|---|---|---|")
    for code, isym, ppt, tick_pt, margin in SPECS:
        tick_val = ppt * tick_pt
        try:
            rng_pt = daily_range_pts(isym)
            ticks = rng_pt / tick_pt
            print(f"| {code} | {tick_pt:g}pt | ${tick_val:.2f} | {rng_pt:,.0f}pt "
                  f"= {ticks:,.0f}틱 | ${ticks * tick_val:,.0f} | ${margin:,} |")
        except Exception:
            print(f"| {code} | {tick_pt:g}pt | ${tick_val:.2f} | N/A | N/A | ${margin:,} |")
    print("\n> 증거금은 참고치 — 변동하므로 HTS에서 확인. 세 상품 모두 틱 가치 $0.50로 동일하다")

    # 4. COT
    print("\n## 4. COT — 투기세력 순포지션 (CFTC, 화요일 기준 데이터)\n")
    for code, market_name in COT_MARKETS:
        cot, err = fetch_cot_one(market_name)
        if err:
            print(f"- **{code}**: ⚠ {err}")
            continue
        trail = " → ".join(f"{net:+,}" for _, net in reversed(cot["hist"]))
        last_date = cot["hist"][0][0]
        print(f"- **{code}** ({cot['market'].split(' - ')[0]}): {trail}  ({last_date} 기준)")
    print("\n> 최근 3주 순포지션(롱-숏) 추이. E-mini 기준이며 마이크로 월물은 개인 비중이 높아 제외")
    print("> ⚠ **1차 근거로 쓰지 말 것** — 상태 스냅샷이며 3~6일 시차가 있다. "
          "기준일 이후 큰 지표가 나왔다면 이미 무효일 수 있으므로, 브리핑에 기준일과 "
          "그 이후 이벤트를 함께 명시한다. 극단 도달·부호 전환에서만 신호 가치가 있다")

    # 5. 롤오버
    print("\n## 5. 롤오버 체크\n")
    code, exp, dleft = rollover_info(now.date())
    line = f"- 현재 근월물: {exp.month}월물({code}) · 만기 {exp:%Y-%m-%d}(금) · D-{dleft}"
    if dleft <= 10:
        line += " — ⚠ **롤오버 구간: 다음 월물로 이동 고려, 호가 얇아짐 주의**"
    else:
        line += " — 여유"
    print(line)

    print("\n---")
    print("※ 이 스냅샷에는 지표 예상치(컨센서스)·간밤 뉴스·실적 일정이 없다 → 웹서치로 보완할 것.")


if __name__ == "__main__":
    main()
