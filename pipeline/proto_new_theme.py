# -*- coding: utf-8 -*-
"""
2026-10-07 사용자 요청: 1월 이후 데이터로 "NEW + 확산 높음" 테마(새 내러티브가 태어난 날) 통계
- NEW: 직전 20거래일 동안 테마로 한 번도 안 나온 키워드 / 확산 높음: 구성 5종목↑ 중 70%↑ 상승
- ① 지속성: 다음 날(d+1)에도 테마로 나오나·주도하나, 5일 안에 다시 나오나
- ② 바로 산 경우: 그날 급등한 그 테마 종목을 d+1 시가에 사서 +1·+5·+20거래일 종가(지수 대비, 날짜별 평균)
- ③ 이틀째 확인 후: d+1에도 테마가 나왔을 때만 d+2 시가에 산 경우 vs d+1에 사라진 경우
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
D = {d: T.load(T.RAW / "days" / f"{d}.json") for d in days}
TH = {d: T.classify(D[d]["stocks"], sg) for d in days}
seen = [{t["name"] for t in TH[d] if not t.get("etc")} for d in days]
lead = [{t["name"] for t in TH[d] if t["lead"]} for d in days]


def ret(c, entry_d, h):
    """entry_d 시가 → entry_d 포함 h거래일째 종가"""
    if entry_d not in cal:
        return None
    j = cal.index(entry_d)
    if j + h - 1 >= len(cal):
        return None
    a, b = px.get(c, {}).get(entry_d), px.get(c, {}).get(cal[j + h - 1])
    return (b[1] / a[0] - 1) * 100 if a and b and a[0] > 0 else None


def nxt(d, k):
    j = cal.index(d)
    return cal[j + k] if j + k < len(cal) else None


def rel(c, mk, entry, h):
    r, m = ret(c, entry, h), ret("KOSPI" if mk == "KOSPI" else "KOSDAQ", entry, h)
    return None if r is None or m is None else r - m


def summarize(agg):
    out = []
    for h in (1, 5, 20):
        per = [st.mean(v) for v in agg[h].values()]
        if per:
            out.append(f"+{h}일 {st.mean(per):+.1f}% (이긴 날 {sum(x > 0 for x in per) / len(per) * 100:.0f}%, 날 {len(per)})")
    return " | ".join(out)


if __name__ == "__main__":
    cats = defaultdict(list)          # 범주 → [(i, theme)]
    for i, d in enumerate(days):
        if i < 20:
            continue
        br = D[d].get("breadth") or {}
        for t in TH[d]:
            if t.get("etc"):
                continue
            nm = t["name"]
            new = not any(nm in s for s in seen[i - 20:i])
            b = br.get(nm)
            hi = bool(b and b[1] >= 5 and b[0] / b[1] >= 0.7)
            cats[("NEW" if new else "기존") + "·" + ("확산높음" if hi else "확산낮음")].append((i, t))

    print("== ① 지속성 (다음 날도 테마로 등장 / 다음 날 주도 / 5일 안 재등장) ==")
    for k in ("NEW·확산높음", "NEW·확산낮음", "기존·확산높음", "기존·확산낮음"):
        ev = [(i, t) for i, t in cats[k] if i + 5 < len(days)]
        if not ev:
            continue
        a1 = sum(t["name"] in seen[i + 1] for i, t in ev) / len(ev) * 100
        l1 = sum(t["name"] in lead[i + 1] for i, t in ev) / len(ev) * 100
        a5 = sum(any(t["name"] in seen[i + k2] for k2 in range(1, 6)) for i, t in ev) / len(ev) * 100
        print(f"  {k}: 건수 {len(ev)} · 다음 날 등장 {a1:.0f}% · 다음 날 주도 {l1:.0f}% · 5일 안 재등장 {a5:.0f}%")

    print("\n== ② 그날 급등한 테마 종목을 다음 날 시가에 바로 산 경우 (지수 대비) ==")
    for k in ("NEW·확산높음", "NEW·확산낮음", "기존·확산높음", "기존·확산낮음"):
        agg = defaultdict(lambda: defaultdict(list))
        for i, t in cats[k]:
            e = nxt(days[i], 1)
            for s in t["stocks"]:
                for h in (1, 5, 20):
                    r = rel(s["code"], s.get("mk"), e, h) if e else None
                    if r is not None:
                        agg[h][days[i]].append(r)
        print(f"  {k}: {summarize(agg)}")

    print("\n== ③ NEW·확산높음: 이틀째 확인 후 d+2 시가 매수 (지수 대비) ==")
    for label, cond in (("다음 날도 테마로 등장", lambda i, nm: nm in seen[i + 1]),
                        ("다음 날 사라짐", lambda i, nm: nm not in seen[i + 1])):
        agg = defaultdict(lambda: defaultdict(list))
        n = 0
        for i, t in cats["NEW·확산높음"]:
            if i + 1 >= len(days) or not cond(i, t["name"]):
                continue
            n += 1
            e = nxt(days[i], 2)
            for s in t["stocks"]:
                for h in (1, 5, 20):
                    r = rel(s["code"], s.get("mk"), e, h) if e else None
                    if r is not None:
                        agg[h][days[i]].append(r)
        print(f"  {label} (건수 {n}): {summarize(agg)}")

    print("\n== 최근 NEW·확산높음 사례 ==")
    for i, t in cats["NEW·확산높음"][-8:]:
        b = D[days[i]]["breadth"][t["name"]]
        nxt_seen = "다음 날 등장" if i + 1 < len(days) and t["name"] in seen[i + 1] else "다음 날 사라짐"
        print(f"  {days[i]} {t['name']} 확산 {b[0]}/{b[1]} · 급등 {len(t['stocks'])}종목 · {nxt_seen}")
