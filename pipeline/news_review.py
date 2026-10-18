# -*- coding: utf-8 -*-
"""
뉴스 테마 AI 검토 — 2026-10-18 (사용자 텔레그램 10008 «2번»)
10/16 «향해» 사건 뒤 규칙(같은 계열 짝 제외·STOP)을 넣었지만 일반어 하나를 막으면 같은 날 다음 일반어가 올라왔다
(애프터마켓→첫날→KRX, 신고서→북미: 대동 "북미 매출 신기록"과 에코프로비엠 "북미 매출 급감"이 한 테마).
그래서 뉴스 테마 후보마다 키워드·종목·키워드가 든 기사 제목을 Haiku에 보내 «이 종목들이 같이 오른 공통 재료의 이름인가»만 묻는다.
- 판정은 raw/news_review.json에 캐시(날짜|키워드|종목코드). 같은 후보는 다시 묻지 않는다
- 키가 없거나 호출이 실패하면 후보를 그대로 둔다(규칙 결과로 발행). 실패한 후보는 캐시에 안 남겨 다음 실행 때 다시 묻는다
- 비용: 후보 있는 날 1회 약 1,500토큰. Haiku 5.5 $0.10/$0.50 per MTok 기준 월 1센트 미만
"""
import json
import os
import sys

MODEL = "claude-haiku-5-5"
SYSTEM = """너는 한국 주식 '테마주 달력'의 검수자다. 그날 7% 이상 오른 종목들의 기사 제목에서 단어 빈도로 뽑은 '뉴스 테마' 후보를 받는다.
후보마다 키워드, 그 키워드로 묶인 종목들, 종목별로 그 키워드가 들어간 기사 제목이 주어진다.

기준은 하나다. 사이트를 보는 투자자가 이 키워드를 그날의 테마 이름으로 보고 납득하는가.
ok=true(기본값): 사건·정책·제품·기술·업종·사업·지역 이슈·인물의 행보 이름이고 종목들 기사가 그 이름과 관련된 소식일 때.
  회사마다 소식 내용이 달라도 같은 업종·제품·사업이면 ok다.
  예) 페스트(흑사병 확산에 진단·백신주), 호르무즈(해협 봉쇄 우려), 변압기(변압기 수주·증설 소식 여럿), 데이터센터, K뷰티, 기술수출, KDDX, 젠슨(젠슨 황 방한)
  여러 관련주를 한꺼번에 다룬 기사를 종목들이 같이 받은 것은 정상이다. 그것만으로 반려하지 않는다
ok=false: 아래가 분명할 때만
1. 키워드가 재료 이름이 아닌 일반어다: 동사·부사(향해, 불쑥), 날짜·시간(첫날, 연휴), 시장·거래 용어(애프터마켓, KRX, 신고서),
   실적·범위 일반어(이익, 매출, 세계, 차원), 회사 이름 일부
2. 같은 단어가 종목마다 무관하거나 반대인 이야기다. 예) 북미: 한 종목은 북미 매출 신기록, 다른 종목은 북미 매출 급감
3. 한 회사의 개별 사건(합병·지분·실적·인사)을 계열사·지주사가 같이 받은 것이다
판단이 서지 않으면 ok=true. reason은 한국어 20자 이내."""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "ok", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def api_key():
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    try:                                       # PC 예비 실행: 뉴스 브리핑과 같은 키
        sys.path.insert(0, r"C:\kiwoom")
        from kiwoom_config import ANTHROPIC_KEY
        return ANTHROPIC_KEY
    except Exception:
        return None


def ask(cands):
    """cands: [{"id", "kw", "stocks": [{"n", "titles": [...]}]}] → {id: {"ok", "reason"}} 또는 None(검토 못 함)"""
    key = api_key()
    if not key:
        print("뉴스 테마 검토 건너뜀: API 키 없음")
        return None
    try:
        import anthropic
    except ImportError:
        print("뉴스 테마 검토 건너뜀: anthropic 패키지 없음")
        return None
    lines = []
    for c in cands:
        lines.append(f"[{c['id']}] 키워드: {c['kw']}")
        for s in c["stocks"]:
            lines.append(f"  - {s['n']}: " + " / ".join(s["titles"]) if s["titles"] else f"  - {s['n']}: (키워드 든 제목 없음)")
    try:
        client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=60)
        r = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
    except Exception as e:
        print("뉴스 테마 검토 실패:", type(e).__name__, str(e)[:200])
        return None
    if r.stop_reason != "end_turn":
        print("뉴스 테마 검토 실패: stop_reason", r.stop_reason)
        return None
    try:
        text = next(b.text for b in r.content if b.type == "text")
        res = {x["id"]: {"ok": bool(x["ok"]), "reason": x["reason"][:40]} for x in json.loads(text)["results"]}
    except Exception as e:
        print("뉴스 테마 검토 실패: 응답 해석", type(e).__name__)
        return None
    u = r.usage
    print(f"뉴스 테마 검토 {len(cands)}건 · 입력 {u.input_tokens} 출력 {u.output_tokens} 토큰")
    return res


def review(d, ng, titles, names, cache, fresh):
    """d 날짜의 뉴스 테마 후보 ng {kw: codes}에서 검토로 떨어진 것을 뺀 dict를 돌려준다.
    titles {code: [제목]}, names {code: 이름}, cache = news_review.json 내용(제자리 갱신).
    fresh에는 이번 실행에서 새로 판정한 것 [(d, kw, 이름들, ok, reason)]과 실패 [(d, "FAIL", ...)]를 붙인다"""
    keep = dict(ng)
    need = []
    for k, cs in ng.items():
        cid = f"{d}|{k}|{','.join(sorted(cs))}"
        v = cache.get(cid)
        if v is None:
            need.append((cid, k, cs))
        elif not v["ok"]:
            keep.pop(k)
    if not need:
        return keep
    cands = []
    for j, (cid, k, cs) in enumerate(need):
        stocks = []
        for c in sorted(cs):
            ts = [t for t in dict.fromkeys(" ".join(x.split()) for x in titles.get(c, [])) if k in t]
            stocks.append({"n": names.get(c, c), "titles": ts[:4]})
        cands.append({"id": f"c{j + 1}", "kw": k, "stocks": stocks})
    res = ask(cands)
    if res is None:
        fresh.append((d, "FAIL", [k for _, k, _ in need], None, None))
        return keep
    for j, (cid, k, cs) in enumerate(need):
        v = res.get(f"c{j + 1}")
        if v is None:
            continue
        cache[cid] = v
        fresh.append((d, k, [names.get(c, c) for c in sorted(cs)], v["ok"], v["reason"]))
        if not v["ok"]:
            keep.pop(k)
    return keep
