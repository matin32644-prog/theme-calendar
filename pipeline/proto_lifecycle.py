# -*- coding: utf-8 -*-
"""
시제품(2026-10-07): 테마 생애주기 꼬리표 + 주도 이후 성과 통계
- 생애주기: 연속 주도 N일째 / 첫 등장(직전 20거래일 무등장) / 식는 중(직전 3일 안에 주도였는데 오늘은 주도 아님)
- 성과: 주도 테마 종목을 "다음 날 시가"에 샀다고 보고 +1·+5·+20거래일 종가까지 수익률.
  비교군 = 같은 날 기준을 넘긴 급등주 중 주도 테마에 안 든 종목(기타·비주도 테마)
사용: py -X utf8 proto_lifecycle.py  (raw/prices.json 필요: PC에만 있음)
"""
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import themecal as T  # noqa: E402

sg, _ = T.stock_groups()
days = sorted(p.stem for p in (T.RAW / "days").glob("*.json"))
D = {d: T.load(T.RAW / "days" / f"{d}.json") for d in days}
P = json.load(open(T.RAW / "prices.json", encoding="utf-8"))["prices"]
px = {c: {r[0]: (r[1], r[4]) for r in rows} for c, rows in P.items()}     # 날짜 → (시가, 종가)
alld = sorted(px["KOSPI"])

lead_hist, seen_hist, by_day = [], [], {}
for d in days:
    th = T.classify(D[d]["stocks"], sg)
    by_day[d] = th
    lead_hist.append({t["name"] for t in th if t["lead"]})
    seen_hist.append({t["name"] for t in th if not t.get("etc")})


def tags(i):
    out = {}
    for t in by_day[days[i]]:
        if t.get("etc"):
            continue
        nm = t["name"]
        if t["lead"]:
            k = 0
            while i - k >= 0 and nm in lead_hist[i - k]:
                k += 1
            out[nm] = f"연속 {k}일째 주도" if k > 1 else "주도 1일차"
        if not any(nm in s for s in seen_hist[max(0, i - 20):i]):
            out[nm] = out.get(nm, "") + (" · " if nm in out else "") + "첫 등장(20거래일 만)"
    cooling = [nm for nm in set().union(*lead_hist[max(0, i - 3):i]) if nm not in lead_hist[i]]
    return out, cooling


def fwd(code, d, h):
    if code not in px or d not in alld:
        return None
    j = alld.index(d)
    if j + h >= len(alld):
        return None
    d1, dh = alld[j + 1], alld[j + h]
    if d1 not in px[code] or dh not in px[code]:
        return None
    o = px[code][d1][0]
    return (px[code][dh][1] / o - 1) * 100 if o > 0 else None


if __name__ == "__main__":
    for d in ("20261006", "20261001", "20260813"):
        i = days.index(d)
        tg, cool = tags(i)
        print(f"\n== {d} ==")
        for t in by_day[d]:
            if t["lead"]:
                print(f"  ★{t['name']} ({len(t['stocks'])}종목) — {tg.get(t['name'], '')}")
        print("  식는 중:", ", ".join(cool) if cool else "없음")

    res = defaultdict(lambda: defaultdict(list))
    for i, d in enumerate(days):
        for t in by_day[d]:
            if t["lead"]:
                k = 0
                while i - k >= 0 and t["name"] in lead_hist[i - k]:
                    k += 1
                grp = "주도 1일차" if k == 1 else ("주도 2일차" if k == 2 else "주도 3일차+")
            else:
                grp = "비교군(주도 아닌 급등주)"
            for s in t["stocks"]:
                for h in (1, 5, 20):
                    r = fwd(s["code"], d, h)
                    if r is not None:
                        res[grp][h].append(r)
    print("\n== 다음 날 시가 매수 → N거래일 뒤 종가 ==")
    for grp in ("주도 1일차", "주도 2일차", "주도 3일차+", "비교군(주도 아닌 급등주)"):
        line = []
        for h in (1, 5, 20):
            a = res[grp][h]
            if a:
                line.append(f"+{h}일 평균 {st.mean(a):+.1f}% 중앙 {st.median(a):+.1f}% 승률 {sum(x > 0 for x in a) / len(a) * 100:.0f}% (n={len(a)})")
        print(f"  {grp}: " + " | ".join(line))
