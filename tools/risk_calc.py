#!/usr/bin/env python3
"""마이크로 선물 포지션 사이즈 계산기 — 손절폭과 리스크 한도로 진입 가능 계약 수 계산.

⚠ **틱 가치가 상품마다 같지 않다.** MNQ·M2K·MYM은 $0.50이지만 **MES는 $1.25(2.5배)**다.
2026-09-08까지는 브리핑 3종이 전부 $0.50이라 틱 수만 비교하면 됐지만, MES가 들어온 뒤로는
**같은 손절 틱 수가 곧 같은 손실이 아니다.** 상품 간 비교는 반드시 '달러'로 한다.

사용법:
    python3 tools/risk_calc.py --product MES --stop-ticks 60 --account 2117
    python3 tools/risk_calc.py --product MNQ --stop-pts 30 --account 2117 --risk-pct 2
    python3 tools/risk_calc.py --list          # 상품별 사양 비교표

⚠ **비트코인 무기한 선물은 계산 구조가 완전히 다르다** (만기·고정 계약크기·고정 증거금이 없다).
   그래서 PRODUCTS 표에 넣지 않고 별도 모드로 분리했다 — 설계 근거는 PLAN-BTC.md 5장.

    python3 tools/risk_calc.py -p BTC --stop-pct 2.85 --account 2117
    python3 tools/risk_calc.py -p BTC --stop-pct 2 --account 2117 --leverage 10
"""

import argparse

# 상품: (이름, 포인트당 USD, 1틱=지수포인트, 위탁증거금 참고치, 일평균 변동 틱)
PRODUCTS = {
    "MNQ": ("마이크로 나스닥100", 2.0, 0.25, 3958, 1922),
    "MES": ("마이크로 S&P500", 5.0, 0.25, 2100, 298),
    "M2K": ("마이크로 러셀2000", 5.0, 0.10, 1110, 351),
    "MYM": ("마이크로 다우", 0.5, 1.00, 1560, 525),
    "MCL": ("마이크로 WTI 원유", 100.0, 0.01, 890, 403),
    "M6E": ("마이크로 유로FX", 12500.0, 0.0001, 220, 46),
}


def tick_value(code):
    _, ppt, tick_pt, _, _ = PRODUCTS[code]
    return ppt * tick_pt


# ── 비트코인 무기한 선물 (OKX BTC-USDT-SWAP) ──────────────────────────────
BTC_CT_VAL = 0.01     # 계약당 BTC (OKX API 확인: ctVal)
BTC_LOT_SZ = 0.01     # 주문 최소 단위 (계약)
BTC_MAX_LEV = 100
BTC_FALLBACK_PRICE = 80400.0   # API 실패 시 (2026-09-20 기준) — 반드시 --price로 갱신할 것
LIQ_SAFETY = 3.0      # 청산 거리 ≥ 손절 거리 × 3 (PLAN-BTC.md 5-2)


def btc_live():
    """OKX 공개 API에서 현재가·펀딩비. 실패하면 (None, None)."""
    try:
        import requests
        H = {"User-Agent": "InvestmentAnalyzer/1.0"}
        B = "https://www.okx.com/api/v5"
        t = requests.get(B + "/market/ticker", params={"instId": "BTC-USDT-SWAP"},
                         timeout=15, headers=H).json()["data"][0]["last"]
        f = requests.get(B + "/public/funding-rate", params={"instId": "BTC-USDT-SWAP"},
                         timeout=15, headers=H).json()["data"][0]["fundingRate"]
        return float(t), float(f)
    except Exception:
        return None, None


def run_btc(args):
    if args.stop_pct is None:
        raise SystemExit("BTC는 --stop-pct (손절 %)로 지정합니다. 예: -p BTC --stop-pct 2.85 --account 2117\n"
                         "  (틱이 아니라 %로 받는 이유: 무기한 선물은 계약 크기를 자유롭게 쪼갤 수 있어\n"
                         "   '몇 계약'이 아니라 '명목가치 얼마'가 리스크를 정한다 — PLAN-BTC.md 5-3)")
    if args.account is None or args.account <= 0:
        raise SystemExit("--account (계좌 잔고 USD)가 필요합니다")
    stop = args.stop_pct
    if stop <= 0:
        raise SystemExit("손절 %는 0보다 커야 합니다")

    price, funding = (args.price, None) if args.price else btc_live()
    src = "OKX 실시간"
    if price is None:
        price, src = BTC_FALLBACK_PRICE, "⚠ API 실패 → 내장 기본값(낡았을 수 있음)"
    elif args.price:
        src = "사용자 지정"

    risk_usd = args.risk_usd if args.risk_usd is not None else args.account * args.risk_pct / 100
    notional = risk_usd / (stop / 100)
    qty = notional / price
    contracts = qty / BTC_CT_VAL

    lev_cap = 100.0 / (LIQ_SAFETY * stop)          # 청산 거리 ≥ 손절 × 3
    # 상한 이하에서 '가장 높은' 단계를 권한다 — 낮출수록 안전하지만 증거금이 불필요하게 묶인다
    # (1배면 명목가치 전액이 증거금이 되어 계좌의 35%가 잠긴다)
    rec_lev = max([x for x in (1, 2, 3, 5, 10, 20, 25, 50, 100) if x <= lev_cap] or [1])
    lev = args.leverage if args.leverage else rec_lev
    margin = notional / lev
    liq_pct = 100.0 / lev

    print(f"[BTC] 무기한 선물 (OKX BTC-USDT-SWAP) · 계약당 {BTC_CT_VAL} BTC · 최소 주문 {BTC_LOT_SZ} 계약")
    print(f"현재가 ${price:,.0f} ({src})")
    print(f"계좌 ${args.account:,.0f} · 허용 리스크 "
          + (f"${risk_usd:,.2f} (지정)" if args.risk_usd is not None
             else f"{args.risk_pct}% = ${risk_usd:,.2f}"))
    print()
    print(f"손절 {stop:.2f}%  (= ${price * stop / 100:,.0f} 움직임)")
    print(f"→ 포지션 명목가치  ${notional:,.0f}")
    print(f"→ 수량             {qty:.5f} BTC  = {contracts:.2f} 계약")
    if contracts < BTC_LOT_SZ:
        print(f"  ✗ 최소 주문 단위({BTC_LOT_SZ} 계약 = {BTC_CT_VAL*BTC_LOT_SZ:.4f} BTC) 미달 → 진입 보류")
    else:
        print(f"  ✓ 주문 가능 (최소 단위 {BTC_LOT_SZ} 계약의 {contracts/BTC_LOT_SZ:.0f}배)")
    print()
    print(f"레버리지 {lev}배" + ("" if args.leverage else f" (권장 — 상한 {lev_cap:.1f}배)"))
    print(f"→ 필요 증거금      ${margin:,.0f}  (계좌의 {margin / args.account * 100:.1f}%)")
    print(f"→ 청산까지 거리    약 {liq_pct:.1f}%  (손절 {stop:.2f}%의 {liq_pct / stop:.1f}배)")
    print()

    if liq_pct < stop:
        print(f"  ✗ **위험: 청산({liq_pct:.1f}%)이 손절({stop:.2f}%)보다 먼저 온다.**")
        print(f"     손절이 작동하기 전에 포지션이 날아간다 → 레버리지를 {lev_cap:.0f}배 이하로 낮출 것")
    elif liq_pct < stop * LIQ_SAFETY:
        print(f"  ⚠ 청산 여유가 부족하다 (손절의 {liq_pct / stop:.1f}배, 권장 {LIQ_SAFETY:.0f}배 이상)")
        print(f"     → 레버리지 {lev_cap:.0f}배 이하 권장")
    else:
        print(f"  ✓ 청산 거리가 손절의 {liq_pct / stop:.1f}배 — 손절이 먼저 작동한다")

    if margin > args.account:
        print(f"  ✗ 증거금 ${margin:,.0f}이 계좌를 초과 → 레버리지를 올리거나 포지션을 줄일 것")

    if funding is not None:
        day = notional * funding * 3
        print(f"\n펀딩비 {funding*100:+.4f}%/8시간 (연율 {funding*3*365*100:+.1f}%)")
        print(f"→ 보유 비용 하루 약 ${abs(day):,.2f} "
              f"({'롱이 지불' if funding > 0 else '롱이 수령'}) = 리스크 예산의 {abs(day)/risk_usd*100:.1f}%/일")
        if funding >= 0.0005:
            print("  ⚠ 펀딩비 +0.05%/8h 초과 — **롱 장기 보유 금지** (비용 + 롱 과열 신호 · PLAN-BTC.md 5-4)")

    print("\n※ 수수료·슬리피지는 별도 — 실제 손실은 계산보다 커질 수 있음")
    print("※ **24시간 시장이다. 손절은 반드시 거래소에 주문으로 걸어둔다** — 자는 동안이 곧 갭이다")
    print("※ 레버리지는 손실 크기를 정하지 않는다. **포지션 크기가 정한다.** 레버리지가 바꾸는 건 청산 거리뿐")


def show_list():
    print(f"{'상품':<6}{'이름':<18}{'1틱':>10}{'틱가치':>9}{'일평균':>10}{'하루$':>9}{'증거금':>9}")
    print("-" * 72)
    for code, (name, ppt, tick_pt, margin, day_ticks) in PRODUCTS.items():
        tv = ppt * tick_pt
        print(f"{code:<6}{name:<18}{tick_pt:>10g}{tv:>9.2f}{day_ticks:>9,}틱"
              f"{day_ticks * tv:>8,.0f}{margin:>9,}")
    print("\n※ 증거금·일평균 변동은 참고치. 실제 값은 HTS와 snapshot.py에서 확인")
    print("⚠ 틱 가치가 상품마다 다르다 — MES는 $1.25로 나머지($0.50)의 2.5배.")
    print("   같은 틱 수라도 손실 금액이 2.5배이므로 '틱'이 아니라 '달러'로 비교할 것")
    print("⚠ MES 증거금 $2,100은 추정치 — 반드시 HTS에서 확인 (2026-09-08 기준 미확정)")


def main():
    ap = argparse.ArgumentParser(description="마이크로 선물 포지션 사이즈 계산기")
    ap.add_argument("--list", action="store_true", help="상품별 사양 비교표 출력")
    ap.add_argument("--product", "-p", default="MNQ", choices=list(PRODUCTS) + ["BTC"],
                    help="상품 코드 (기본 MNQ) · BTC는 무기한 선물 모드")
    ap.add_argument("--account", type=float, help="계좌 잔고 (USD)")
    ap.add_argument("--stop-ticks", type=float, help="손절폭 (틱) — 상품 간 비교에 편함")
    ap.add_argument("--stop-pts", type=float, help="손절폭 (지수포인트)")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="허용 리스크 %% (기본 1%%)")
    ap.add_argument("--risk-usd", type=float, default=None, help="허용 리스크 금액 USD (지정 시 %% 무시)")
    ap.add_argument("--stop-pct", type=float, default=None,
                    help="손절폭 (%%) — BTC 무기한 선물 전용")
    ap.add_argument("--leverage", type=float, default=None,
                    help="레버리지 (BTC 전용, 미지정 시 안전 권장값 자동)")
    ap.add_argument("--price", type=float, default=None,
                    help="현재가 직접 지정 (BTC 전용, 미지정 시 OKX 실시간)")
    args = ap.parse_args()

    if args.list:
        show_list()
        return
    if args.product == "BTC":
        run_btc(args)
        return
    if args.account is None or (args.stop_ticks is None and args.stop_pts is None):
        ap.error("--account 와 (--stop-ticks 또는 --stop-pts)가 필요합니다. 사양만 볼 땐 --list")

    name, ppt, tick_pt, margin, day_ticks = PRODUCTS[args.product]
    tv = ppt * tick_pt
    ticks = args.stop_ticks if args.stop_ticks is not None else args.stop_pts / tick_pt
    if ticks <= 0 or args.account <= 0:
        ap.error("계좌와 손절폭은 0보다 커야 함")

    risk_usd = args.risk_usd if args.risk_usd is not None else args.account * args.risk_pct / 100
    loss_per_contract = ticks * tv
    contracts = int(risk_usd // loss_per_contract)

    print(f"[{args.product}] {name} · 1틱 {tick_pt:g}pt = ${tv:.2f}")
    print(f"계좌 ${args.account:,.0f} · 허용 리스크 "
          + (f"${risk_usd:,.2f} (지정)" if args.risk_usd is not None
             else f"{args.risk_pct}% = ${risk_usd:,.2f}"))
    print(f"손절 {ticks:,.0f}틱 ({ticks * tick_pt:,.2f}pt) → 계약당 손실 ${loss_per_contract:,.2f}")
    print(f"참고: 이 상품 하루 평균 변동은 {day_ticks:,}틱 "
          f"→ 손절폭은 하루 변동의 {ticks / day_ticks * 100:.0f}%")
    print()

    if contracts < 1:
        max_ticks = risk_usd / tv
        print(f"✗ 진입 불가: 1계약 손실(${loss_per_contract:,.2f})이 허용 리스크를 초과")
        print(f"  → 손절을 {max_ticks:,.0f}틱 이하로 줄이거나 진입 보류")
    else:
        total = contracts * loss_per_contract
        print(f"✓ 최대 {contracts}계약 (손절 시 -${total:,.2f}, 계좌의 {total / args.account * 100:.2f}%)")
        need = contracts * margin
        if need > args.account:
            print(f"  ⚠ 다만 증거금 ${need:,}이 계좌를 초과 → 실제로는 "
                  f"{int(args.account // margin)}계약까지만 주문 가능")

    print("\n※ 수수료·슬리피지(체결 밀림)는 별도 — 실제 손실은 계산보다 커질 수 있음")
    print("※ 갭에서는 손절이 작동하지 않는다 (주말·지표 직후 보유 시 유의)")


if __name__ == "__main__":
    main()
