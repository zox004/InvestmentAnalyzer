#!/usr/bin/env python3
"""비트코인 브리핑용 데이터 스냅샷 수집기.

⚠ 이 도구는 tools/snapshot.py(지수용)와 별개다. 비트코인은 분석 체인이 다르므로
   수집 항목도 다르다 — 자세한 설계 근거는 PLAN-BTC.md 참조.

수집 항목 (PLAN-BTC.md의 4층 체인 순서)
  1층 유동성·달러 : DXY · 10년물 · 금 (yfinance)
  2층 자금 흐름   : ⚠ 무료 API 없음 → 웹서치로 보완 (현물 ETF 순유입·스테이블코인)
  3층 레버리지    : 펀딩비 · 미결제약정 · 롱숏비율 (OKX 공개 API) ← COT 대체
  4층 자체 재료   : ⚠ 웹서치로 보완 (규제·해킹·거래소 이슈)
  공통            : 시세 · 변동폭 · 판정 임계값 · 상관계수

거래소는 OKX 공개 API(인증 불필요)를 쓴다. 사용자 거래소가 바뀌면 OKX_* 상수만 교체.
"""
import argparse
import datetime as dt

import requests

UA = {"User-Agent": "InvestmentAnalyzer/1.0"}
OKX = "https://www.okx.com/api/v5"
OKX_INST = "BTC-USDT-SWAP"      # 무기한 선물(perpetual swap) · USDT 정산
OKX_CCY = "BTC"

# 펀딩비 해석 구간 (8시간 기준) — PLAN-BTC.md 3층 표와 반드시 일치시킬 것
FUND_HOT = 0.0005      # +0.05% 이상 = 롱 과열
FUND_NEUTRAL = 0.0002  # ±0.02% 이내 = 중립(정보 없음)


def okx(path, params=None):
    """OKX 공개 API 호출. 실패해도 브리핑이 멈추지 않도록 (None, 이유)를 돌려준다."""
    try:
        r = requests.get(OKX + path, params=params, timeout=25, headers=UA)
        r.raise_for_status()
        j = r.json()
        if j.get("code") not in ("0", 0):
            return None, f"OKX 오류 code={j.get('code')} {j.get('msg')}"
        return j.get("data"), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}"


def f(v, default=None):
    """API가 빈 문자열을 돌려주는 필드가 있다 (예: nextFundingRate) → 안전 변환."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def newest_first(rows, ts_index=0):
    """⚠ API 응답 정렬을 믿지 않는다. 타임스탬프로 직접 내림차순 정렬.

    패턴 6(도구도 조용히 틀린다) 대응 — 정렬 가정이 뒤집히면 '롱 과열'과
    '숏 우세'가 정반대로 읽힌다.
    """
    return sorted(rows, key=lambda x: int(x[ts_index] if isinstance(x, list) else x["fundingTime"]),
                  reverse=True)


def fmt_pct(v, digits=2, sign=True):
    return f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"


# ─────────────────────────── 3층: 레버리지 상태 ───────────────────────────

def fetch_leverage():
    out = {}

    d, err = okx("/public/funding-rate", {"instId": OKX_INST})
    out["funding_now"] = (f(d[0]["fundingRate"]), f(d[0].get("nextFundingRate"))) if d else None
    out["funding_err"] = err

    d, err = okx("/public/funding-rate-history", {"instId": OKX_INST, "limit": "21"})
    if d:
        rows = sorted(d, key=lambda x: int(x["fundingTime"]), reverse=True)
        out["funding_hist"] = [f(x["fundingRate"]) for x in rows if f(x["fundingRate"]) is not None]
    else:
        out["funding_hist"] = None
        out["funding_err"] = out["funding_err"] or err

    d, err = okx("/public/open-interest", {"instType": "SWAP", "instId": OKX_INST})
    out["oi"] = (f(d[0]["oi"], 0), f(d[0]["oiCcy"], 0)) if d else None
    out["oi_err"] = err

    d, err = okx("/rubik/stat/contracts/long-short-account-ratio",
                 {"ccy": OKX_CCY, "period": "1D", "limit": "8"})
    if d:
        rows = newest_first(d)
        out["ls_ratio"] = [(int(x[0]), f(x[1])) for x in rows if f(x[1]) is not None]
    else:
        out["ls_ratio"] = None
        out["ls_err"] = err
    return out


def read_funding(rate):
    """펀딩비 한 값의 해석. 연율은 8시간 x 3회 x 365일."""
    ann = rate * 3 * 365 * 100
    if rate >= FUND_HOT:
        tag = "**롱 과열** — 되돌림·롱 청산 위험"
    elif rate <= -FUND_NEUTRAL:
        tag = "**숏 우세** — 숏 스퀴즈(급등) 위험"
    elif abs(rate) <= FUND_NEUTRAL:
        tag = "중립 (기본값 부근) — **정보 없음, 방향 근거로 쓰지 말 것**"
    else:
        tag = "약한 롱 우세"
    return ann, tag


# ─────────────────────── 공통: 시세·변동폭·임계값 ───────────────────────

def fetch_candles(limit=60):
    d, err = okx("/market/candles", {"instId": OKX_INST, "bar": "1Dutc", "limit": str(limit)})
    if not d:
        return None, err
    rows = newest_first(d)
    out = []
    for x in rows:
        ts, o, h, l, c = int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])
        out.append({"date": dt.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d"),
                    "o": o, "h": h, "l": l, "c": c, "range_pct": (h - l) / c * 100})
    return out, None


def fetch_macro():
    """1층 재료 + 상관계수. yfinance 사용 (지수용 snapshot.py와 같은 소스)."""
    try:
        import yfinance as yf
        tk = {"BTC-USD": "BTC", "^NDX": "NDX", "^GSPC": "SPX",
              "DX-Y.NYB": "DXY", "GC=F": "GOLD", "^TNX": "US10Y"}
        df = yf.download(list(tk), period="1y", progress=False,
                         auto_adjust=False)["Close"].rename(columns=tk)
        df = df[[v for v in tk.values() if v in df.columns]]
        r = df.pct_change(fill_method=None)
        corr = {}
        for w, lbl in [(63, "3개월"), (252, "1년")]:
            rr = r.tail(w).dropna()
            if len(rr) > 10 and "BTC" in rr:
                corr[lbl] = {c: rr["BTC"].corr(rr[c]) for c in rr.columns if c != "BTC"}
        return df, corr, None
    except Exception as e:
        return None, None, f"{type(e).__name__}: {str(e)[:80]}"


# ──────────────────────────────── 출력 ────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="비트코인 브리핑용 데이터 스냅샷")
    ap.add_argument("--days", type=int, default=20, help="변동폭·임계값 산출 구간 (기본 20)")
    a = ap.parse_args()

    kst = dt.timezone(dt.timedelta(hours=9))
    now = dt.datetime.now(kst)
    wd = "월화수목금토일"[now.weekday()]
    print(f"# 비트코인 데이터 스냅샷 — {now:%Y-%m-%d} ({wd}) {now:%H:%M} KST")
    print(f"\n> 거래소 **OKX · {OKX_INST}** (무기한 선물) 기준 · 분석 체인은 `PLAN-BTC.md` 참조")

    # ── 시세·변동폭 ──
    candles, cerr = fetch_candles(max(a.days + 5, 40))
    print("\n## 1. 시세와 변동폭 (UTC 일봉 · UTC 00:00 = KST 09:00)")
    if not candles:
        print(f"\n⚠ 일봉 조회 실패: {cerr}")
    else:
        print(f"\n| 날짜(UTC) | 종가 | 등락 | 고저폭 |")
        print("|---|---|---|---|")
        for i, c in enumerate(candles[:6]):
            chg = ((c["c"] / candles[i + 1]["c"] - 1) * 100) if i + 1 < len(candles) else None
            print(f"| {c['date']} | {c['c']:,.0f} | "
                  f"{fmt_pct(chg) if chg is not None else '—'} | {c['range_pct']:.2f}% |")
        rng = [c["range_pct"] for c in candles[:a.days]]
        avg = sum(rng) / len(rng)
        thr = round(avg / 5, 1)
        print(f"\n- **{a.days}일 평균 고저폭: {avg:.2f}%**")
        print(f"- **판정 임계값: ±{thr:.1f}%** (규칙 = 고저폭의 약 1/5 · `reviews/lessons.md`)")
        print(f"  - ⚠ `reviews/bias-log-BTC.md`에 적힌 임계값과 **다르면 위클리에서 재산정**할 것")
        big = [c for c in candles[:a.days] if c["range_pct"] > avg * 2]
        if big:
            print(f"- ⚠ 이 구간 평균은 **이벤트성 하루**에 끌려 있다: "
                  + ", ".join(f"{c['date']} {c['range_pct']:.1f}%" for c in big[:4]))

    # ── 3층: 레버리지 ──
    lev = fetch_leverage()
    print("\n## 2. ⭐ 3층 — 레버리지 상태 (COT 대체 지표)")
    print("\n> 지수 브리핑의 COT는 3~6일 시차 + 헤지 혼입 때문에 1차 근거로 못 썼다.")
    print("> **펀딩비는 시차가 없고 헤지도 섞이지 않아 1차 근거로 쓸 수 있다.**")
    print("> **단, 극단일 때만.** 중립 구간에서는 방향 근거로 쓰지 않는다 (PLAN-BTC.md 3층).")

    if lev["funding_now"]:
        cur, nxt = lev["funding_now"]
        ann, tag = read_funding(cur)
        print(f"\n| 항목 | 값 | 해석 |")
        print("|---|---|---|")
        print(f"| **현재 펀딩비** (8시간) | **{cur*100:+.4f}%** (연율 {ann:+.1f}%) | {tag} |")
        if nxt is not None:
            print(f"| 다음 예상 펀딩비 | {nxt*100:+.4f}% | — |")
    else:
        print(f"\n⚠ 펀딩비 조회 실패: {lev.get('funding_err')}")

    if lev["funding_hist"]:
        h = lev["funding_hist"]
        d3 = sum(h[:9])
        print(f"\n- **최근 9회(3일) 추이**: " + " ".join(f"{x*100:+.3f}" for x in h[:9]))
        print(f"- **3일 누적 {d3*100:+.3f}%** → 롱을 3일 들고 있었다면 그만큼 지불"
              f" (1,000달러 포지션당 약 {abs(d3)*1000:.2f}달러)")
        if len(h) >= 21:
            print(f"- 7일 누적 {sum(h[:21])*100:+.3f}%")

    if lev["oi"]:
        oi_ct, oi_btc = lev["oi"]
        px = candles[0]["c"] if candles else None
        usd = f" ≈ {oi_btc*px/1e9:,.2f}십억 달러" if px else ""
        print(f"\n- **미결제약정(OI)**: {oi_ct:,.0f} 계약 = **{oi_btc:,.0f} BTC**{usd}")
        print("  - OI가 **늘면서** 가격이 오르면 새 돈이 들어온 것 / OI가 **줄면서** 오르면 숏 청산(되돌림 쉬움)")

    if lev.get("ls_ratio"):
        ls = lev["ls_ratio"]
        print(f"\n- **롱/숏 계정 비율** (최근 → 과거): "
              + " → ".join(f"{v:.2f}" for _, v in ls[:6]))
        print(f"  - 1.0 = 롱·숏 계정 수 동일. **{ls[0][1]:.2f}**"
              + (" = 롱 쪽이 많다" if ls[0][1] > 1.05 else
                 " = 숏 쪽이 많다" if ls[0][1] < 0.95 else " = 균형"))
        if len(ls) >= 3:
            print(f"  - ⚠ **수준이 아니라 방향을 본다** (`lessons.md` ⑥·⑭): "
                  f"{ls[min(5,len(ls)-1)][1]:.2f} → {ls[0][1]:.2f}")

    # ── 1층: 유동성·달러 + 상관 ──
    df, corr, merr = fetch_macro()
    print("\n## 3. 1층 — 유동성·달러 (BTC가 금리를 보는 방식은 지수와 다르다)")
    if df is None:
        print(f"\n⚠ 조회 실패: {merr}")
    else:
        # ⚠ BTC는 주말에도 값이 있지만 DXY·금·금리는 없다. 열마다 따로 마지막 유효값을 잡는다
        # (한 행만 보면 주말에 전 항목이 NaN으로 사라진다 — 실제로 그렇게 비어 나왔다)
        print(f"\n| 항목 | 값 | 5일 전 대비 | 기준일 |")
        print("|---|---|---|---|")
        for c, lbl in [("BTC", "**BTC** (yfinance 현물)"), ("DXY", "달러인덱스 (DXY)"),
                       ("US10Y", "미 10년물"), ("GOLD", "금")]:
            if c not in df.columns:
                continue
            ser = df[c].dropna()
            if ser.empty:
                continue
            cur, day = ser.iloc[-1], ser.index[-1].strftime("%m/%d")
            ch = f"{(cur/ser.iloc[-6]-1)*100:+.2f}%" if len(ser) > 6 else "—"
            print(f"| {lbl} | {cur:,.2f} | {ch} | {day} |")
        print("\n> **달러가 오르면 BTC에 역풍**이다(상관 음수). 지수와 달리 "
              "**금리는 '이익의 할인율'이 아니라 '유동성의 수도꼭지'로 작동**한다.")

    if corr:
        print("\n### BTC 상관계수 — 왜 BTC를 넣는지의 근거")
        print("\n| 대상 | 3개월 | 1년 |")
        print("|---|---|---|")
        for c, lbl in [("NDX", "나스닥100 (MNQ)"), ("SPX", "S&P500 (MES)"),
                       ("DXY", "달러인덱스"), ("GOLD", "금"), ("US10Y", "미 10년물")]:
            v3 = corr.get("3개월", {}).get(c)
            v1 = corr.get("1년", {}).get(c)
            if v3 is not None or v1 is not None:
                print(f"| {lbl} | {v3:+.3f} | {v1:+.3f} |" if v3 is not None and v1 is not None
                      else f"| {lbl} | {v3 if v3 is None else f'{v3:+.3f}'} | {v1 if v1 is None else f'{v1:+.3f}'} |")
        n3 = corr.get("3개월", {}).get("NDX")
        if n3 is not None:
            print(f"\n> BTC-나스닥 상관 **{n3:+.3f}** — MNQ·MES 상관(+0.90)과 비교하면 거의 딴 몸이다.")
            print("> **그래서 MNQ·MES가 같이 움직일 때 BTC는 독립 정보를 준다.**")
            if abs(n3) > 0.6:
                print("> ⚠ **단, 지금은 상관이 높아졌다 — 위험자산으로 동조하는 국면이면 독립 정보 가치가 떨어진다.**")

    # ── 웹서치 보완 안내 ──
    print("\n## 4. ⚠ 이 스냅샷에 없는 것 — 웹서치로 반드시 보완")
    print("""
| 층 | 항목 | 왜 필요한가 |
|---|---|---|
| **2층** | **현물 ETF 순유입/유출** (일별·주별) | **지금 BTC의 한계 매수자다.** 무료 API가 없어 자동 수집 못 함 |
| 2층 | 스테이블코인 총 공급량 (USDT·USDC) | 크립토에 들어와 대기 중인 현금 |
| **4층** | 규제·정책 (SEC · CLARITY Act 등) | BTC 고유 재료 — 지수엔 없는 축 |
| 4층 | 거래소·ETF 구조 이슈, 대형 해킹 | 급락의 단독 원인이 될 수 있다 |
| 1층 | 연준 대차대조표(QT) 속도 | **금리보다 이게 BTC에 직접이다** |

> 2층이 비어 있으면 **편향 확신도를 '중' 이상으로 올리지 않는다** (PLAN-BTC.md 편향 결정 규칙).
""")
    print("---")
    print("※ 펀딩비·OI·롱숏비율은 OKX 실시간이라 시차가 없다. 반면 **ETF 자금 흐름은 1일 시차**가 있으니")
    print("  인용할 때 기준일을 반드시 적는다 (`CLAUDE.md` 3-1 — 언제 측정된 값인가).")


if __name__ == "__main__":
    main()
