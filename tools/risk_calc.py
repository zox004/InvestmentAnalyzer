#!/usr/bin/env python3
"""마이크로 선물 포지션 사이즈 계산기 — 손절폭과 리스크 한도로 진입 가능 계약 수 계산.

⚠ **틱 가치가 상품마다 같지 않다.** MNQ·M2K·MYM은 $0.50이지만 **MES는 $1.25(2.5배)**다.
2026-09-08까지는 브리핑 3종이 전부 $0.50이라 틱 수만 비교하면 됐지만, MES가 들어온 뒤로는
**같은 손절 틱 수가 곧 같은 손실이 아니다.** 상품 간 비교는 반드시 '달러'로 한다.

사용법:
    python3 tools/risk_calc.py --product MES --stop-ticks 60 --account 2117
    python3 tools/risk_calc.py --product MNQ --stop-pts 30 --account 2117 --risk-pct 2
    python3 tools/risk_calc.py --list          # 상품별 사양 비교표
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
    ap.add_argument("--product", "-p", default="MNQ", choices=list(PRODUCTS),
                    help="상품 코드 (기본 MNQ)")
    ap.add_argument("--account", type=float, help="계좌 잔고 (USD)")
    ap.add_argument("--stop-ticks", type=float, help="손절폭 (틱) — 상품 간 비교에 편함")
    ap.add_argument("--stop-pts", type=float, help="손절폭 (지수포인트)")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="허용 리스크 %% (기본 1%%)")
    ap.add_argument("--risk-usd", type=float, default=None, help="허용 리스크 금액 USD (지정 시 %% 무시)")
    args = ap.parse_args()

    if args.list:
        show_list()
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
