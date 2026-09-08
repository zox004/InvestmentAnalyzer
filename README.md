# InvestmentAnalyzer

해외선물 지수 마이크로 **2종 — MNQ(나스닥100) · MES(S&P500)** 트레이딩을 위한 협업 저장소.

> 2026-09-09부터 대상 상품을 **MNQ·MES**로 변경했다(이전: MNQ·M2K·MYM). 제외된 두 상품의 채점 기록은 `reviews/`에 보존한다.
> ⚠ **틱 가치가 상품마다 다르다** — MNQ $0.50 / **MES $1.25**. 손절 비교는 틱이 아니라 달러로 한다.

| 역할 | 담당 |
|---|---|
| 기술적 분석(차트) · 매매 판단 · 실행 | 사용자 |
| 기본적 분석(펀더멘털) · 브리핑 · 이벤트 해석 | Claude (AI) |

전체 계획과 운영 루틴은 [PLAN.md](PLAN.md) 참조.

## 구조

```
CLAUDE.md    작업 규칙 — 브리핑 전 체크리스트, 분석 원칙, 오류에서 나온 규칙 모음
GLOSSARY.md  용어집 — 브리핑 용어를 초보 기준으로 풀이 (모르는 말이 나오면 여기부터)
briefings/   데일리 브리핑 아카이브 (거래일 저녁 작성)
weekly/      위클리 리뷰 & 다음 주 전망 (주말 작성)
reviews/     bias-log-<상품>.md (상품별 채점·적중률, 종료 상품도 보존) + lessons.md (상품 공통 오류 분석)
journal/     매매일지 (사용자 기록, 회고용)
tools/       자동화 도구 — snapshot.py(시세·일정·COT 수집), risk_calc.py(포지션 계산), calendar_2026.yaml(지표 일정)
```

## 도구 빠른 시작

```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt  # 최초 1회
.venv/bin/python tools/snapshot.py        # 브리핑용 데이터 스냅샷 (2종 시세·참고지수·일정·COT·롤오버)
.venv/bin/python tools/risk_calc.py --list                             # 상품별 사양 비교
.venv/bin/python tools/risk_calc.py -p MES --stop-ticks 60 --account 2117  # 진입 가능 계약 수
```

> 본 저장소의 모든 분석은 정보 제공 목적이며, 투자 판단과 책임은 본인에게 있습니다.
