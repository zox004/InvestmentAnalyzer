# InvestmentAnalyzer

해외선물 **MNQ(마이크로 나스닥)** 트레이딩을 위한 협업 저장소.

| 역할 | 담당 |
|---|---|
| 기술적 분석(차트) · 매매 판단 · 실행 | 사용자 |
| 기본적 분석(펀더멘털) · 브리핑 · 이벤트 해석 | Claude (AI) |

전체 계획과 운영 루틴은 [PLAN.md](PLAN.md) 참조.

## 구조

```
GLOSSARY.md  용어집 — 브리핑 용어를 초보 기준으로 풀이 (모르는 말이 나오면 여기부터)
briefings/   데일리 브리핑 아카이브 (거래일 저녁 작성)
weekly/      위클리 리뷰 & 다음 주 전망 (주말 작성)
reviews/     bias-log-<상품>.md (상품별 채점·적중률) + lessons.md (상품 공통 오류 분석)
journal/     매매일지 (사용자 기록, 회고용)
tools/       자동화 도구 — snapshot.py(시세·일정·COT 수집), risk_calc.py(포지션 계산), calendar_2026.yaml(지표 일정)
```

## 도구 빠른 시작

```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt  # 최초 1회
.venv/bin/python tools/snapshot.py        # 브리핑용 데이터 스냅샷
.venv/bin/python tools/risk_calc.py --account 3000 --stop-pts 30  # 진입 가능 계약 수
```

> 본 저장소의 모든 분석은 정보 제공 목적이며, 투자 판단과 책임은 본인에게 있습니다.
