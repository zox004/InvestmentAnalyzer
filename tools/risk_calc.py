#!/usr/bin/env python3
"""MNQ 포지션 사이즈 계산기 — 손절폭과 리스크 한도로 진입 가능 계약 수 계산.

MNQ는 1포인트 = $2 (1틱 0.25pt = $0.50).

사용법:
    python3 tools/risk_calc.py --account 3000 --stop-pts 30            # 리스크 1% 기본
    python3 tools/risk_calc.py --account 3000 --stop-pts 30 --risk-pct 2
    python3 tools/risk_calc.py --account 3000 --stop-pts 30 --risk-usd 50
"""

import argparse

POINT_VALUE = 2.0  # MNQ $2/pt


def main():
    ap = argparse.ArgumentParser(description="MNQ 포지션 사이즈 계산기")
    ap.add_argument("--account", type=float, required=True, help="계좌 잔고 (USD)")
    ap.add_argument("--stop-pts", type=float, required=True, help="손절폭 (포인트)")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="허용 리스크 %% (기본 1%%)")
    ap.add_argument("--risk-usd", type=float, default=None, help="허용 리스크 금액 USD (지정 시 %% 무시)")
    args = ap.parse_args()

    if args.stop_pts <= 0 or args.account <= 0:
        ap.error("계좌와 손절폭은 0보다 커야 함")

    risk_usd = args.risk_usd if args.risk_usd is not None else args.account * args.risk_pct / 100
    loss_per_contract = args.stop_pts * POINT_VALUE
    contracts = int(risk_usd // loss_per_contract)

    print(f"계좌 ${args.account:,.0f} · 허용 리스크 "
          + (f"${risk_usd:,.2f} (지정 금액)" if args.risk_usd is not None
             else f"{args.risk_pct}% = ${risk_usd:,.2f}"))
    print(f"손절폭 {args.stop_pts:g}pt → 계약당 손실 ${loss_per_contract:,.2f}")
    print()
    if contracts < 1:
        max_stop = risk_usd / POINT_VALUE
        print(f"✗ 진입 불가: 1계약 손실(${loss_per_contract:,.2f})이 허용 리스크를 초과")
        print(f"  → 이 리스크로 1계약을 열려면 손절폭 {max_stop:.2f}pt 이하로 줄이거나 진입 보류")
    else:
        total = contracts * loss_per_contract
        print(f"✓ 진입 가능: 최대 {contracts}계약 (손절 시 -${total:,.2f}, 계좌의 {total / args.account * 100:.2f}%)")
    print("\n※ 수수료·슬리피지(체결 밀림)는 별도 — 실제 손실은 계산보다 커질 수 있음")


if __name__ == "__main__":
    main()
