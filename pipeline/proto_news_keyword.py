# -*- coding: utf-8 -*-
"""
시제품(2026-10-07): 뉴스 키워드로 그날의 진짜 내러티브 잡기
사용자: "코로나19가 아니라 러시아 흑사병 관련주로 오른 것 같아" → 실제로 국제약품 기사 16건이 '페스트'
- 종목별 그날 기사 제목(전일 15:30~당일 18:00)에서 단어 빈도를 세고 일반어를 뺀 상위 키워드를 뽑는다
- 같은 상위 키워드를 가진 급등주가 2종목 이상이면 그 키워드를 그날의 '뉴스 테마' 후보로 본다
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import themecal as T  # noqa: E402

STOP = set("""특징주 상승 급등 강세 상한가 하락 약세 주가 테마 관련주 관련 종목 장중 마감 기록 오전 오후 오늘 내일 전일 대비
기대 기대감 소식 이유 영향 우려 수혜 부각 확대 발표 공시 투자 시장 증시 코스피 코스닥 외국인 기관 개인 매수 매도 순매수
등 및 또 더 위 속 후 전 중 첫 새 신규 최대 최고 사상 연속 이틀 사흘 거래 거래량 거래대금 시가총액 시총 상승세 급등세 하락세
핫종목 마켓 줌인 리포트 줍줍 종합 단독 속보 뉴스 기자 이슈 분석 전망 목표가 증권 증권사 상향 하향 실적 분기 영업이익 매출 억원 조원 만원
돌파 신고가 껑충 훨훨 불기둥 날개 왜 무슨 기업 회사 그룹 대표 주 株 원 배 개 건 일 월 년 미 美 中 한국 국내 글로벌 Why 이투데이""".split())


def words(title, name):
    t = title.replace(name, " ")
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|[‘’'\"“”…·,.!?%↑↓→\-/]|\d+(?:\.\d+)?", " ", t)
    out = []
    for w in t.split():
        w = re.sub(r"(에|의|가|이|은|는|을|를|와|과|도|로|으로|에서|까지|만|에도|라|이라)$", "", w)
        if len(w) >= 2 and w not in STOP and not re.fullmatch(r"[A-Za-z]{1,2}", w):
            out.append(w)
    return out


def top_keywords(name, d, prev_d, k=3):
    items = [x for x in (T.gnews(name, d, prev_d, fetch=False) or []) if prev_d + "1530" <= x["dt"] <= d + "1800"]
    c = Counter()
    for it in items:
        c.update(set(words(it["t"], T.clean_name(name))))
    return [(w, n) for w, n in c.most_common(k) if n >= 2], len(items)


if __name__ == "__main__":
    days = sorted(p.stem for p in (T.RAW / "days").glob("*.json"))
    for d in sys.argv[1:] or ["20261007"]:
        i = days.index(d)
        prev_d = days[i - 1]
        day = T.load(T.RAW / "days" / f"{d}.json")
        groups = defaultdict(list)
        print(f"\n== {d} ==")
        for s in [x for x in day["stocks"] if T.passes(x)]:
            kw, n = top_keywords(s["name"], d, prev_d)
            print(f"  {s['name']:10s} {s['chg']:+5.1f}% 기사 {n:2d}  {kw}")
            for w, _ in kw[:2]:
                groups[w].append(s["name"])
        print("  ▶ 뉴스 테마 후보(2종목↑):", {w: v for w, v in groups.items() if len(v) >= 2})
