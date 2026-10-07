# -*- coding: utf-8 -*-
"""
시제품(2026-10-07): 테마 확산도로 "찐 주도주"를 거를 수 있나
- 그룹: 주도 테마 안에서 대장(거래대금 1위) / 추종, × 테마 확산도(구성종목 중 상승 비율) 높음·낮음
- 성과: 다음 날 시가 매수 → +1·+5·+20거래일 종가, 같은 시장 지수 대비, 날짜별 평균을 다시 평균
- 비교: 주도 테마 아닌 급등주
"""
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import themecal as T  # noqa: E402

sg, _ = T.stock_groups()
days = sorted(p.stem for p in (T.RAW / "days").glob("*.json"))
px = T.load(T.RAW / "px.json")
cal = sorted(px["KOSPI"])


def fwd(c, d, h):
    if d not in cal:
        return None
    j = cal.index(d)
    if j + h >= len(cal):
        return None
    a, b = px.get(c, {}).get(cal[j + 1]), px.get(c, {}).get(cal[j + h])
    return (b[1] / a[0] - 1) * 100 if a and b and a[0] > 0 else None


def run(cut):
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for d in days:
        day = T.load(T.RAW / "days" / f"{d}.json")
        br = day.get("breadth") or {}
        for t in T.classify(day["stocks"], sg):
            if t.get("etc") or not t["lead"]:
                for s in t["stocks"]:
                    rows = [("비교군", s)]
                    for g, x in rows:
                        ix = "KOSPI" if x.get("mk") == "KOSPI" else "KOSDAQ"
                        for h in (1, 5, 20):
                            r, m = fwd(x["code"], d, h), fwd(ix, d, h)
                            if r is not None and m is not None:
                                agg[g][h][d].append(r - m)
                continue
            b = br.get(t["name"])
            hi = b and b[1] >= 5 and b[0] / b[1] >= cut
            lead_code = max(t["stocks"], key=lambda x: x["val"])["code"]
            for s in t["stocks"]:
                role = "대장" if s["code"] == lead_code else "추종"
                g = f"{role}·확산{'높음' if hi else '낮음'}"
                ix = "KOSPI" if s.get("mk") == "KOSPI" else "KOSDAQ"
                for h in (1, 5, 20):
                    r, m = fwd(s["code"], d, h), fwd(ix, d, h)
                    if r is not None and m is not None:
                        agg[g][h][d].append(r - m)
    return agg


if __name__ == "__main__":
    for cut in (0.7, 0.8):
        agg = run(cut)
        print(f"\n== 확산 높음 기준 {int(cut * 100)}% 이상 (구성 5종목 이상) ==")
        for g in ("대장·확산높음", "대장·확산낮음", "추종·확산높음", "추종·확산낮음", "비교군"):
            line = []
            for h in (1, 5, 20):
                per = [st.mean(v) for v in agg[g][h].values()]
                if per:
                    n = sum(len(v) for v in agg[g][h].values())
                    line.append(f"+{h}일 {st.mean(per):+.1f}% (이긴날 {sum(x > 0 for x in per) / len(per) * 100:.0f}%, 날 {len(per)}, n {n})")
            print(f"  {g}: " + " | ".join(line))
