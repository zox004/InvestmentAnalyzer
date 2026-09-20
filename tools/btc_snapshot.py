#!/usr/bin/env python3
"""비트코인 브리핑용 데이터 스냅샷 수집기.

⚠ 이 도구는 tools/snapshot.py(지수용)와 별개다. 비트코인은 분석 체인이 다르므로
   수집 항목도 다르다 — 자세한 설계 근거는 PLAN-BTC.md 참조.

수집 항목 (PLAN-BTC.md의 4층 체인 순서)
  1층 유동성·달러 : DXY · 10년물 · 금 (yfinance)
  2층 자금 흐름   : 현물 ETF 일별 순유입 (Farside) · 스테이블코인 총공급 (DefiLlama)
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


# ─────────────────────── 2층: 현물 ETF 자금 흐름 ───────────────────────

ETF_URL = "https://farside.co.uk/btc/"
# 브라우저 UA가 아니면 막힌다
ETF_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")}


def _etf_num(v):
    """Farside 표기: 음수는 괄호 (16.6) · 빈칸·하이픈은 결측."""
    t = str(v).strip().replace(",", "")
    if t in ("", "-", "nan", "\u2013"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        x = float(t)
    except ValueError:
        return None
    return -x if neg else x


def fetch_etf_flows(days=10):
    """현물 BTC ETF 일별 순유입(백만 달러). 실패하면 (None, 이유) — 브리핑을 멈추지 않는다."""
    try:
        import io

        import pandas as pd

        r = requests.get(ETF_URL, timeout=30, headers=ETF_UA)
        r.raise_for_status()
        t = pd.read_html(io.StringIO(r.text))[0]

        # ⚠ MultiIndex 주의: Total 열은 level0에만 이름이 있고 level1은 Unnamed다.
        #    level1만 취하면 Total이 'Unnamed: 13_level_1'이 되어 조용히 전부 None이 된다
        #    (실제로 그렇게 한 번 틀렸다 — 패턴 6).
        flat = []
        for c in t.columns:
            if isinstance(c, tuple):
                flat.append(str(next((x for x in c if not str(x).startswith("Unnamed")), c[-1])))
            else:
                flat.append(str(c))
        t.columns = flat
        t = t.rename(columns={t.columns[0]: "Date", t.columns[-1]: "Total"})

        rows = []
        for _, x in t.iterrows():
            d = pd.to_datetime(str(x["Date"]), format="%d %b %Y", errors="coerce")
            if pd.isna(d):
                continue
            total = _etf_num(x["Total"])
            # 개별 ETF 합으로 Total을 검산한다 (열 구조가 바뀌면 여기서 걸린다)
            comp = sum(v for c in t.columns[1:-1] if (v := _etf_num(x[c])) is not None)
            rows.append({"date": d.date(), "total": total, "check": round(comp, 1),
                         "ibit": _etf_num(x.get("IBIT"))})
        if not rows:
            return None, "표를 찾았으나 날짜 행을 해석하지 못함 — 사이트 구조 변경 확인 필요"
        rows.sort(key=lambda z: z["date"], reverse=True)
        return rows[:days], None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:90]}"


# ─────────────────── 2층 보조: 스테이블코인 총공급 ───────────────────

STAB_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
# 해석 구간은 최근 2년 7일 변화율 분포에서 뽑았다 (감으로 정하지 않았다)
#   10분위 -0.46% · 25분위 -0.07% · 50분위 +0.45% · 75분위 +1.02% · 90분위 +1.83%
#   평균 +0.57% — 즉 스테이블코인 공급은 '보통 늘어난다'(양수인 주가 71%)
STAB_BANDS = [(1.83, "**강한 확장** (90분위 이상)"),
              (1.02, "확장 (75~90분위)"),
              (-0.07, "보통 — **정보 없음**(25~75분위)"),
              (-0.46, "둔화 (10~25분위)"),
              (-99.0, "**수축** (10분위 이하) — 크립토에서 현금이 빠지는 중")]


def fetch_stablecoins():
    """USD 스테이블코인 총공급과 7/30/90일 변화. 실패하면 (None, 이유)."""
    try:
        import datetime as _dt

        r = requests.get(STAB_URL, timeout=40, headers=ETF_UA)
        r.raise_for_status()
        rows = {}
        for x in r.json():
            v = (x.get("totalCirculatingUSD") or {}).get("peggedUSD")
            if v:
                rows[_dt.datetime.utcfromtimestamp(int(x["date"])).date()] = float(v)
        if len(rows) < 100:
            return None, f"시계열이 너무 짧다({len(rows)}일) — 응답 구조 변경 확인 필요"
        days = sorted(rows)
        cur = rows[days[-1]]
        out = {"date": days[-1], "total": cur, "chg": {}}
        for d in (7, 30, 90):
            if len(days) > d:
                prev = rows[days[-1 - d]]
                out["chg"][d] = (cur - prev, (cur / prev - 1) * 100)
        return out, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:90]}"


def read_stab(pct7):
    for lo, tag in STAB_BANDS:
        if pct7 >= lo:
            return tag
    return STAB_BANDS[-1][1]


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

        # ⚠ BTC는 주말에도 거래되지만 DXY·금·금리는 안 된다. 순서를 틀리면 상관이 조용히 왜곡된다.
        #    (이전 버전: 가격에 주말 행이 남은 채 pct_change → DXY의 월요일 수익률이 NaN이 되어
        #     **모든 월요일이 통째로 빠졌고**, tail(63)도 거래일 63일이 아니었다.
        #     그 결과 BTC-DXY 3개월 상관이 -0.432로 나왔는데, 올바르게 계산하면 -0.384다.)
        #    → 먼저 공통 거래일만 남기고(dropna) 그 다음에 수익률을 낸다.
        aligned = df.dropna()
        r = aligned.pct_change(fill_method=None).dropna()
        corr = {}
        for w, lbl in [(63, "3개월"), (252, "1년")]:
            rr = r.tail(w)
            if len(rr) > 10 and "BTC" in rr:
                corr[lbl] = {c: rr["BTC"].corr(rr[c]) for c in rr.columns if c != "BTC"}
                corr.setdefault("_n", {})[lbl] = len(rr)
        return df, corr, None
    except Exception as e:
        return None, None, f"{type(e).__name__}: {str(e)[:80]}"


# ──────────────────────────────── 출력 ────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="비트코인 브리핑용 데이터 스냅샷")
    ap.add_argument("--days", type=int, default=60,
                    help="변동폭·임계값 산출 구간 (기본 60 — 20일은 불안정해 개정됨, lessons.md 참조)")
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

    # ── 2층: 자금 흐름 ──
    flows, ferr = fetch_etf_flows(10)
    print("\n## 2. ⭐ 2층 — 현물 ETF 자금 흐름 (지금 BTC의 한계 매수자)")
    if not flows:
        print(f"\n⚠ 자동 수집 실패: {ferr}")
        print("→ **웹서치로 보완하고, 확인 못 하면 확신도 '중'을 넘기지 않는다** (PLAN-BTC.md 4장)")
    else:
        bad = [f for f in flows if f["total"] is not None and abs(f["total"] - f["check"]) > 1.0]
        print(f"\n| 날짜 | 순유입 (백만$) | IBIT |")
        print("|---|---|---|")
        for f in flows[:7]:
            tot = f"**{f['total']:+,.1f}**" if f["total"] is not None else "—"
            ib = f"{f['ibit']:+,.1f}" if f["ibit"] is not None else "—"
            print(f"| {f['date']} | {tot} | {ib} |")
        vals = [f["total"] for f in flows if f["total"] is not None]
        if vals:
            s5, s10 = sum(vals[:5]), sum(vals[:10])
            up, dn = sum(1 for v in vals[:10] if v > 0), sum(1 for v in vals[:10] if v < 0)
            print(f"\n- **최근 5거래일 합계: {s5:+,.1f}백만 달러**")
            print(f"- **최근 10거래일 합계: {s10:+,.1f}백만 달러** (유입 {up}일 / 유출 {dn}일)")
            tag = ("**순유입 우세**" if s10 > 300 else "**순유출 우세**" if s10 < -300
                   else "**사실상 중립** — 방향 근거로 쓰기 어렵다")
            print(f"- 판정: {tag}")
            print("- ⚠ **가격과 함께 읽는다**: 순유출인데 가격이 올랐다면 "
                  "**레버리지가 만든 상승**이다 → 3층에서 확인 (PLAN-BTC.md 2-2)")
        if bad:
            print(f"\n⚠ **Total과 개별 ETF 합이 어긋난 행이 {len(bad)}개** — 사이트 열 구조 변경 의심, 수동 확인 필요")
        print(f"\n> 출처 Farside Investors · **1일 시차** — 인용 시 기준일을 함께 적는다")

    # ── 2층 보조: 스테이블코인 ──
    stab, serr = fetch_stablecoins()
    print("\n### 2층 보조 — 스테이블코인 총공급 (⚠ 확인용, 방향 근거 아님)")
    if not stab:
        print(f"\n⚠ 조회 실패: {serr}")
    else:
        print(f"\n- **USD 스테이블코인 총공급: {stab['total']/1e9:,.1f}십억 달러** ({stab['date']} 기준)")
        for d in (7, 30, 90):
            if d in stab["chg"]:
                amt, pct = stab["chg"][d]
                extra = f"  ← {read_stab(pct)}" if d == 7 else ""
                print(f"- {d}일 변화: **{amt/1e9:+.1f}십억$ ({pct:+.2f}%)**{extra}")
        print("""
> ⚠ **선행 지표가 아니다 — 측정으로 확인했다.** 7일 변화율 기준 BTC와의 상관은
> **동시 +0.359 / 7일 선행 +0.096 / 14일 선행 -0.049 / 30일 선행 -0.092**다.
> "대기 자금이 쌓이면 나중에 오른다"는 통념은 **선행 상관 0으로 성립하지 않는다.**
> **COT와 같은 취급을 한다 — 다른 근거로 세운 판단을 확인하는 데만 쓰고, 1차 근거로 쓰지 않는다.**
> (PLAN-BTC.md 2-2)""")

    # ── 3층: 레버리지 ──
    lev = fetch_leverage()
    print("\n## 3. ⭐ 3층 — 레버리지 상태 (COT 대체 지표)")
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
    print("\n## 4. 1층 — 유동성·달러 (BTC가 금리를 보는 방식은 지수와 다르다)")
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
        n = corr.get("_n", {})
        print("\n### BTC 상관계수 — 왜 BTC를 넣는지의 근거")
        print(f"\n> 공통 거래일만 비교 (3개월 {n.get('3개월','?')}일 · 1년 {n.get('1년','?')}일).")
        print("> **부호 = 방향, 절댓값 = 강도. 0이 '관계 없음'이다.**")
        print("> **R²(설명력) = 상관²** — BTC 움직임의 몇 %를 그 변수로 설명할 수 있나. 나머지는 다른 이유다.")
        print("\n| 대상 | 3개월 | R² | 1년 | R² |")
        print("|---|---|---|---|---|")
        for c, lbl in [("NDX", "나스닥100 (MNQ)"), ("SPX", "S&P500 (MES)"),
                       ("DXY", "달러인덱스"), ("GOLD", "금"), ("US10Y", "미 10년물")]:
            v3 = corr.get("3개월", {}).get(c)
            v1 = corr.get("1년", {}).get(c)
            if v3 is None and v1 is None:
                continue
            f3 = f"{v3:+.3f}" if v3 is not None else "—"
            r3 = f"{v3**2*100:.0f}%" if v3 is not None else "—"
            f1 = f"{v1:+.3f}" if v1 is not None else "—"
            r1 = f"{v1**2*100:.0f}%" if v1 is not None else "—"
            print(f"| {lbl} | {f3} | {r3} | {f1} | {r1} |")
        n3 = corr.get("3개월", {}).get("NDX")
        if n3 is not None:
            print(f"\n> BTC-나스닥 상관 **{n3:+.3f}** — MNQ·MES 상관(+0.90)과 비교하면 거의 딴 몸이다.")
            print("> **그래서 MNQ·MES가 같이 움직일 때 BTC는 독립 정보를 준다.**")
            if abs(n3) > 0.6:
                print("> ⚠ **단, 지금은 상관이 높아졌다 — 위험자산으로 동조하는 국면이면 독립 정보 가치가 떨어진다.**")

    # ── 웹서치 보완 안내 ──
    print("\n## 5. ⚠ 이 스냅샷에 없는 것 — 웹서치로 반드시 보완")
    print("""
| 층 | 항목 | 왜 필요한가 |
|---|---|---|
| **4층** | 규제·정책 (SEC · CLARITY Act 등) | BTC 고유 재료 — 지수엔 없는 축 |
| 4층 | 거래소·ETF 구조 이슈, 대형 해킹 | 급락의 단독 원인이 될 수 있다 |
| 1층 | 연준 대차대조표(QT) 속도 | **금리보다 이게 BTC에 직접이다** |

> **ETF 자금 흐름은 2번 항목에서 자동 수집된다.** 그 수집이 실패한 날만 웹서치로 보완하고,
> 끝까지 확인 못 하면 **편향 확신도를 '중' 이상으로 올리지 않는다** (PLAN-BTC.md 4장).
""")
    print("---")
    print("※ 펀딩비·OI·롱숏비율은 OKX 실시간이라 시차가 없다. 반면 **ETF 자금 흐름은 1일 시차**가 있으니")
    print("  인용할 때 기준일을 반드시 적는다 (`CLAUDE.md` 3-1 — 언제 측정된 값인가).")


if __name__ == "__main__":
    main()
