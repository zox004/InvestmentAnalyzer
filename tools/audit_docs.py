#!/usr/bin/env python3
"""문서 정합성 회귀 검사 — 2026-09-20 감사(1~18차)에서 나온 결함 유형을 전부 자동화.

왜 있나: BTC 체인을 만들면서 턴마다 새 오류가 나왔다(낡은 수치 잔존·깨진 참조·번호 체계
충돌·브리핑이 규칙 어휘를 안 씀). 사람 눈으로 다시 읽는 방식으로는 반복 검출이 안 되므로
기계 검사로 고정한다. 브리핑 작성 후 / 수치를 고친 후 반드시 실행한다.

    .venv/bin/python tools/audit_docs.py
"""
import os
import re
import subprocess
import sys

FILES = ["PLAN-BTC.md", "PLAN.md", "CLAUDE.md", "GLOSSARY.md", "README.md",
         "reviews/lessons.md", "reviews/bias-log-BTC.md", "reviews/bias-log-MNQ.md",
         "reviews/bias-log-MES.md", "tools/btc_snapshot.py", "tools/risk_calc.py"]
SKILLS = sorted(__import__("glob").glob(".claude/skills/*/SKILL.md"))

# 스킬이 선언해야 하는 임계값 — lessons.md가 정본이고 스킬은 복사본이므로 대조한다
THRESHOLDS = {"briefing-index": ["±0.3%", "±0.7%", "±0.2%", "±0.5%"],
              "briefing-btc": ["±0.6%", "±1.2%"],
              "briefing-weekly": ["±0.3%", "±0.2%", "±0.6%"]}
BRIEFS = sorted(f for f in __import__("glob").glob("briefings/*/*.md"))

# 낡은 값 → 정정 문맥이 없으면 결함. 값이 바뀌면 여기에 추가한다.
STALE = ["-0.432", "+0.904", "+0.257", "±1.5%", "2.85%", "4.5배", "±0.57%",
         "-0.215", "+0.377", "+0.610", "-0.298"]
CORRECTION_WORDS = (r"틀렸|틀리|정정|철회|버그|잘못|개정|과장|이전 버전|올바르게|"
                    r"20일 창|관대|결번|번호 체계|깨진 참조|섞어 쓰면|쓰지 않는다|표기를 쓴다")
TOOLS = [["tools/btc_snapshot.py"], ["tools/risk_calc.py", "--list"],
         ["tools/risk_calc.py", "-p", "BTC", "--stop-pct", "3", "--account", "2117"],
         ["tools/risk_calc.py", "-p", "MES", "--stop-ticks", "40", "--account", "2117"]]


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except FileNotFoundError:
        return None


def main():
    issues = []
    les = read("reviews/lessons.md") or ""
    plan = read("PLAN-BTC.md") or ""
    clau = read("CLAUDE.md") or ""
    err = set(re.findall(r"### ([①-⑭])", les))
    pat = set(re.findall(r"^(\d+)\. ", les, re.M))
    targets = [f for f in FILES + SKILLS + BRIEFS if os.path.exists(f)]

    # 0) 스킬 무결성 — 프론트매터·임계값 일치·라우팅 표 존재
    for sk in SKILLS:
        t = read(sk)
        name = os.path.basename(os.path.dirname(sk))
        if not t.startswith("---\n"):
            issues.append(f"{sk}: YAML 프론트매터가 파일 맨 앞에 없다")
        fm = t.split("---")[1] if t.count("---") >= 2 else ""
        if f"name: {name}" not in fm:
            issues.append(f"{sk}: frontmatter name이 디렉터리명({name})과 다르다")
        if "description:" not in fm:
            issues.append(f"{sk}: frontmatter description 없음")
        for v in THRESHOLDS.get(name, []):
            if v not in t:
                issues.append(f"{sk}: 임계값 {v} 표기 없음 (lessons.md와 불일치 위험)")
    for sk_name in THRESHOLDS:
        if not os.path.exists(f".claude/skills/{sk_name}/SKILL.md"):
            issues.append(f"스킬 누락: {sk_name}")
    if os.path.exists("CLAUDE.md"):
        c = read("CLAUDE.md")
        for sk_name in THRESHOLDS:
            if sk_name not in c:
                issues.append(f"CLAUDE.md 라우팅 표에 {sk_name} 없음 — 호출 기준이 사라진다")

    # 1) 참조 무결성
    for f in targets:
        t = read(f)
        for m in re.finditer(r"lessons\.md`?\s*([①-⑳])", t):
            if m.group(1) not in err:
                issues.append(f"{f}: lessons.md 오류 {m.group(1)} 없음")
        for m in re.finditer(r"패턴\s*(\d+)", t):
            if m.group(1) not in pat:
                issues.append(f"{f}: lessons.md 패턴 {m.group(1)} 없음")
        for m in re.finditer(r"[⑮-⑳]", t):
            win = t[max(0, m.start() - 320):m.end() + 240]
            if not re.search(CORRECTION_WORDS, win):
                issues.append(f"{f}: 결번 동그라미 {m.group(0)} (설명 문맥 아님)")
        for m in re.finditer(r"PLAN-BTC\.md`?\s*(\d)-(\d)", t):
            if f"### {m.group(1)}-{m.group(2)}." not in plan:
                issues.append(f"{f}: PLAN-BTC {m.group(1)}-{m.group(2)}절 없음")
        for m in re.finditer(r"CLAUDE\.md`?\s*(\d)-(\d)", t):
            if f"### {m.group(1)}-{m.group(2)}." not in clau:
                issues.append(f"{f}: CLAUDE {m.group(1)}-{m.group(2)}절 없음")
        for m in re.finditer(r"\]\(([^)]+\.md)\)", t):
            tgt = m.group(1)
            if not (os.path.exists(os.path.normpath(os.path.join(os.path.dirname(f), tgt)))
                    or os.path.exists(tgt)):
                issues.append(f"{f}: 링크 깨짐 {tgt}")

    # 2) 낡은 수치
    for val in STALE:
        for f in targets:
            t = read(f)
            for m in re.finditer(re.escape(val), t):
                win = t[max(0, m.start() - 300):m.end() + 220]
                if not re.search(CORRECTION_WORDS, win):
                    issues.append(f"{f}: 낡은 값 {val} (정정 문맥 없음)")
                    break

    # 3) BTC 브리핑이 규칙 어휘를 쓰는지 + 편향이 규칙 출력과 맞는지
    dm = {("강세", "강세"): ("강세", "중"), ("약세", "약세"): ("약세", "중"),
          ("강세", "중립"): ("강세", "하"), ("중립", "강세"): ("강세", "하"),
          ("약세", "중립"): ("약세", "하"), ("중립", "약세"): ("약세", "하"),
          ("강세", "약세"): ("중립", "중"), ("약세", "강세"): ("중립", "중"),
          ("중립", "중립"): ("중립", "중")}
    for f in BRIEFS:
        if "-BTC.md" not in f:
            continue
        t = read(f)
        d = {}
        for layer, p in [("1", r"\*\*1층 유동성·달러\*\* \| \*\*(중립|약세|강세)\*\* \|"),
                         ("2", r"\*\*2층 자금 흐름\*\* \| \*\*(중립|약세|강세)\*\* \|"),
                         ("3", r"\*\*3층 레버리지\*\* \| \*\*(중립|약세|강세)\*\* \|")]:
            m = re.search(p, t)
            d[layer] = m.group(1) if m else None
        if None in d.values():
            issues.append(f"{f}: 층 판정을 규칙 어휘(강세/약세/중립)로 읽을 수 없다 {d}")
            continue
        st = re.search(r"\*\*편향: (강세|중립|약세) \(확신도: (상|중|하)\)\*\*", t)
        if not st:
            issues.append(f"{f}: 편향 선언 문장을 찾을 수 없다")
            continue
        bias, conf = dm[(d["1"], d["2"])]
        if d["3"] == "중립" and (st.group(1), st.group(2)) != (bias, conf):
            issues.append(f"{f}: 편향 {st.group(1)}/{st.group(2)} ≠ 규칙 출력 {bias}/{conf}")

    # 4) 도구 실행
    for cmd in TOOLS:
        if not os.path.exists(cmd[0]):
            continue
        r = subprocess.run([".venv/bin/python"] + cmd, capture_output=True, timeout=500)
        if r.returncode != 0:
            issues.append(f"도구 실패: {' '.join(cmd)} — {r.stderr.decode()[:100]}")

    uniq = list(dict.fromkeys(issues))
    print(f"문서 감사: 파일 {len(targets)}개 (스킬 {len(SKILLS)} · 브리핑 {len(BRIEFS)})")
    print(f"결과: 문제 {len(uniq)}건")
    for i in uniq:
        print(f"  ⚠ {i}")
    return 1 if uniq else 0


if __name__ == "__main__":
    sys.exit(main())
