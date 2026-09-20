# InvestmentAnalyzer

트레이딩 협업 저장소. **성격이 다른 두 묶음**을 다루고, **분석 체인이 서로 달라 설계 문서도 분리**되어 있다.

| 묶음 | 상품 | 거래 수단 | 분석 체인 | 설계 문서 |
|---|---|---|---|---|
| **지수 2종** | MNQ(나스닥100) · MES(S&P500) | 해외선물 마이크로 (CME) | 지표 → 연준 → 금리 → 업종/밸류에이션 → 지수 | [PLAN.md](PLAN.md) |
| **비트코인** | BTC | 해외 거래소 무기한 선물 (OKX 등) | 유동성 → 자금흐름 → 레버리지 → 가격 (4층) | [PLAN-BTC.md](PLAN-BTC.md) |

> 2026-09-09부터 지수 대상을 **MNQ·MES**로 변경했다(이전: MNQ·M2K·MYM). 제외된 두 상품의 채점 기록은 `reviews/`에 보존한다.
> **2026-09-20부터 BTC를 추가**했다 — MNQ·MES 상관이 0.888~0.907로 변별력이 떨어져서, 3개월 상관 +0.259인 BTC로 독립 정보를 얻으려는 목적이다.
> ⚠ **틱 가치가 상품마다 다르다** — MNQ $0.50 / **MES $1.25**. 손절 비교는 틱이 아니라 달러로 한다.
> ⚠ **BTC는 틱·계약 수로 계산하지 않는다** — 자유롭게 쪼갤 수 있어 **'명목가치 얼마'**가 리스크를 정한다.

| 역할 | 담당 |
|---|---|
| 기술적 분석(차트) · 매매 판단 · 실행 | 사용자 |
| 기본적 분석(펀더멘털) · 브리핑 · 이벤트 해석 | Claude (AI) |

운영 루틴과 작업 규칙은 [CLAUDE.md](CLAUDE.md), 상품별 설계는 위 표의 문서를 참조.

**브리핑 절차는 스킬로 분리되어 있다** — `"브리핑"`이면 `briefing-index`+`briefing-btc`, `"위클리"`면 `briefing-weekly`가 호출된다. 두 체인의 규칙(임계값·채점 세션·상쇄표 적용 여부)이 서로 달라 한 문서에 두면 섞이기 때문이다.

## 구조

```
CLAUDE.md    작업 규칙 — 분석 원칙, 사실 확인 원칙, 스킬 라우팅 표, 시장 배경 핵심
.claude/skills/
             briefing-index/   MNQ·MES 브리핑 절차 + 지수 배경 상세 (상쇄표·COT·섹터)
             briefing-btc/     BTC 브리핑 절차 + BTC 배경 상세 (4층·펀딩비·ETF·상관)
             briefing-weekly/  위클리 절차 + 갱신 항목 (임계값·일정·배경·lessons)
GLOSSARY.md  용어집 — 브리핑 용어를 초보 기준으로 풀이 (모르는 말이 나오면 여기부터)
briefings/   데일리 브리핑 아카이브 (거래일 저녁 작성)
weekly/      위클리 리뷰 & 다음 주 전망 (주말 작성)
reviews/     bias-log-<상품>.md (상품별 채점·적중률, 종료 상품도 보존) + lessons.md (상품 공통 오류 분석)
journal/     매매일지 (사용자 기록, 회고용)
tools/       자동화 도구
               snapshot.py       지수용 — 시세·금리·유가·일정·COT·롤오버
               btc_snapshot.py   BTC용 — 시세·변동폭·ETF 자금흐름·스테이블코인·펀딩비·미결제약정·롱숏비율·상관
               risk_calc.py      포지션 계산 (지수는 틱 기준 / BTC는 % + 레버리지·청산 거리)
               audit_docs.py     문서 정합성 회귀 검사 — 커밋 전 필수 (0건 확인)
               calendar_2026.yaml 지표 일정
```

## 도구 빠른 시작

```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt  # 최초 1회
.venv/bin/python tools/snapshot.py         # 지수 스냅샷 (MNQ·MES 시세·참고지수·일정·COT·롤오버)
.venv/bin/python tools/btc_snapshot.py     # BTC 스냅샷 (펀딩비·미결제약정·롱숏비율·임계값·상관)

.venv/bin/python tools/risk_calc.py --list                                  # 지수 사양 비교
.venv/bin/python tools/risk_calc.py -p MES --stop-ticks 60 --account 2117   # 지수: 진입 가능 계약 수
.venv/bin/python tools/risk_calc.py -p BTC --stop-pct 2.79 --account 2117   # BTC: 포지션·레버리지·청산 거리

.venv/bin/python tools/audit_docs.py       # 문서 정합성 검사 (커밋 전 0건 확인)
```

> 본 저장소의 모든 분석은 정보 제공 목적이며, 투자 판단과 책임은 본인에게 있습니다.
