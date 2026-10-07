# -*- coding: utf-8 -*-
"""
국내장 테마주 달력 — 2026-10-07 착수 (사용자 요청: 그날 실제 주도한 테마·주도주 아카이브)

흐름
  collect  평일 16:30  네이버 상승 상위(코스피·코스닥) → raw/days/YYYYMMDD.json (기준보다 넉넉히 +4%·10억 이상 저장)
  nxt      평일 20:40  그날 급등주의 넥스트레이드 장후 가격 → raw/nxt/YYYYMMDD.json
  news               급등주별 그날 뉴스 1건(이유·출처) → raw/news_cache/<code>.json
  build              raw → site/data/YYYY-MM.json (테마 배정, 3개월 주도 횟수)
  publish            site/ 를 깃허브에 push (GitHub Pages)
  daily --close|--nxt  위 단계를 한 번에
  backfill           raw/prices.json(fetch_prices.py) → 과거 raw/days 생성

급등 기준(THRESH_*)은 바꿔도 된다. build가 raw 전체로 다시 계산한다.
RAW_MIN_* 아래로 내리면 collect/backfill부터 다시 돌려야 한다.
"""
import html
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from theme_keywords import keyword  # noqa: E402   (2026-10-07 굵은 40개 묶음 → 키워드 테마로 교체)
import math

# ── 설정 ─────────────────────────────────────────────
THRESH_CHG = 7.0          # 급등: 정규장 등락률 % 이상
THRESH_VALUE = 200.0      # 급등: 거래대금 억원 이상 (2026-10-07 사용자 지시로 30 → 200 "타이트하게")
RAW_MIN_CHG = 4.0         # 원자료 저장 하한
RAW_MIN_VALUE = 10.0
LEAD_MIN = 3              # 주도(빨강) 테마 최소 종목 수
LEAD_SCORE = 4.0         # 또는 거래대금 점수 합이 이 이상이면 2종목이어도 주도 (예: 1.7조+수백억 = MLCC 8/13)
LEAD_MAX = 3              # 주도 테마 최대 개수
THEME_MIN = 2             # 테마로 묶는 최소 종목 수
WINDOW = 63               # "3개월" = 63거래일
VALUE_UNIT = 200.0       # 거래대금 가점: 200억=1점, 2,000억=2점, 2조=3점 (2026-10-07 사용자 "수천억~조 단위엔 가점")
BACKFILL_FROM = "20260701"
NEW_LOOKBACK = 20        # "NEW" = 직전 20거래일 동안 한 번도 테마로 안 나왔던 키워드
COOL_DAYS = 3            # "식는 테마" = 직전 3거래일 안에 주도였는데 오늘은 주도 아님(최대 3개)
STAT_H = (1, 5, 20)      # 성과 통계: 다음 날 시가 매수 → N거래일 뒤 종가, 지수 대비

BASE = Path(__file__).parent              # 저장소/pipeline
RAW = BASE / "raw"
SITE = BASE.parent                         # 저장소 루트 = GitHub Pages
IN_ACTIONS = bool(os.environ.get("GITHUB_ACTIONS"))
KST = timezone(timedelta(hours=9))
H = {"User-Agent": "Mozilla/5.0"}
API = "https://m.stock.naver.com/api"
for d in ("days", "nxt", "news_cache", "search_cache", "gnews_cache"):
    (RAW / d).mkdir(parents=True, exist_ok=True)
(SITE / "data").mkdir(parents=True, exist_ok=True)


def jget(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1 + i)
    return None


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return 0.0


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ── 테마 구성 ─────────────────────────────────────────
def refresh_themes(force=False):
    p = RAW / "naver_themes.json"
    if p.exists() and not force and time.time() - p.stat().st_mtime < 7 * 86400:
        return
    groups = []
    for pg in (1, 2, 3, 4):
        j = jget(f"{API}/stocks/theme?page={pg}&pageSize=100")
        if not j or not j.get("groups"):
            break
        groups += j["groups"]
    mem = {}
    for g in groups:
        out, pg = [], 1
        while True:
            j = jget(f"{API}/stocks/theme/{g['no']}?page={pg}&pageSize=100") or {}
            st = j.get("stocks", [])
            out += [[s["itemCode"], s["stockName"]] for s in st]
            if len(st) < 100:
                break
            pg += 1
        mem[str(g["no"])] = {"name": g["name"], "stocks": out}
    if len(mem) > 200:                       # 수집 실패 시 기존 파일 유지
        save(p, mem)
    print("themes", len(mem))


TSIZE = {}                                # 키워드 테마별 구성 종목 수(작을수록 구체적)


def stock_groups():
    mem = load(RAW / "naver_themes.json", {})
    sg = defaultdict(set)
    members = defaultdict(set)
    for v in mem.values():
        g = keyword(v["name"])
        if g is None:
            continue
        for code, _ in v["stocks"]:
            sg[code].add(g)
            members[g].add(code)
    TSIZE.clear()
    TSIZE.update({g: len(c) for g, c in members.items()})
    return sg, set()


def weight(s):
    return 1 + math.log10(max(s["val"], VALUE_UNIT) / VALUE_UNIT)


# ── 수집 ─────────────────────────────────────────────
def index_today():
    out = {}
    for ix in ("KOSPI", "KOSDAQ"):
        j = jget(f"{API}/index/{ix}/basic") or {}
        out[ix] = {"chg": num(j.get("fluctuationsRatio")), "at": j.get("localTradedAt", "")[:10]}
    return out


def collect():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    ix = index_today()
    if ix["KOSPI"]["at"] != today:
        print("휴장 또는 데이터 없음:", ix)
        return None
    stocks = []
    for mk in ("KOSPI", "KOSDAQ"):
        pg = 1
        while pg < 20:
            j = jget(f"{API}/stocks/up/{mk}?page={pg}&pageSize=100") or {}
            st = j.get("stocks", [])
            for s in st:
                chg = num(s.get("fluctuationsRatio"))
                code, name = s["itemCode"], s["stockName"]
                if s.get("stockEndType") != "stock" or code[-1] != "0" or "스팩" in name:
                    continue
                val = num(s.get("accumulatedTradingValue")) / 100     # 백만원 → 억원
                if chg >= RAW_MIN_CHG and val >= RAW_MIN_VALUE:
                    stocks.append({"code": code, "name": name, "mk": mk, "chg": round(chg, 2),
                                   "val": round(val, 1), "close": num(s.get("closePrice"))})
            if not st or num(st[-1].get("fluctuationsRatio")) < RAW_MIN_CHG:
                break
            pg += 1
    d = today.replace("-", "")
    save(RAW / "days" / f"{d}.json", {"date": d, "kospi": ix["KOSPI"]["chg"], "kosdaq": ix["KOSDAQ"]["chg"],
                                      "src": "naver_live", "stocks": stocks})
    print("collect", d, len(stocks))
    return d


def nxt(d=None):
    d = d or datetime.now(KST).strftime("%Y%m%d")
    day = load(RAW / "days" / f"{d}.json")
    if not day:
        print("본장 기록 없음", d)
        return
    out = {}
    for s in day["stocks"]:
        if not passes(s):
            continue
        j = jget(f"{API}/stock/{s['code']}/basic") or {}
        o = j.get("overMarketPriceInfo") or {}
        p = num(o.get("overPrice"))
        if p > 0 and s["close"] > 0 and str(o.get("localTradedAt", ""))[:10].replace("-", "") == d:
            prev = s["close"] / (1 + s["chg"] / 100)
            out[s["code"]] = {"tot": round((p / prev - 1) * 100, 2), "ext": round((p / s["close"] - 1) * 100, 2),
                              "sess": o.get("tradingSessionType", "")}
        time.sleep(0.05)
    save(RAW / "nxt" / f"{d}.json", out)
    print("nxt", d, len(out))


def backfill():
    p = load(RAW / "prices.json")
    uni, pr = p["universe"], p["prices"]
    ix = {k: {r[0]: r[4] for r in pr[k]} for k in ("KOSPI", "KOSDAQ")}
    dates = sorted(d for d in ix["KOSPI"] if d >= BACKFILL_FROM)
    alld = sorted(ix["KOSPI"])
    by_date = defaultdict(list)
    for code, rows in pr.items():
        if code in ("KOSPI", "KOSDAQ"):
            continue
        for i in range(1, len(rows)):
            d, prev, cl, vol = rows[i][0], rows[i - 1][4], rows[i][4], rows[i][5]
            if d < BACKFILL_FROM or prev <= 0:
                continue
            chg = (cl / prev - 1) * 100
            val = cl * vol / 1e8
            if chg >= RAW_MIN_CHG and val >= RAW_MIN_VALUE:
                info = uni.get(code, {})
                by_date[d].append({"code": code, "name": info.get("name", code), "mk": info.get("market", ""),
                                   "chg": round(chg, 2), "val": round(val, 1), "close": cl})
    for d in dates:
        i = alld.index(d)
        k = {m: round((ix[m][d] / ix[m][alld[i - 1]] - 1) * 100, 2) for m in ("KOSPI", "KOSDAQ")}
        f = RAW / "days" / f"{d}.json"
        if f.exists() and (load(f) or {}).get("src") == "naver_live":
            continue                                   # 실시간 수집본이 있으면 덮지 않음
        save(f, {"date": d, "kospi": k["KOSPI"], "kosdaq": k["KOSDAQ"], "src": "backfill",
                 "stocks": by_date.get(d, [])})
    print("backfill days", len(dates))


# ── 뉴스 ─────────────────────────────────────────────
UPW = re.compile(r"급등|상승|강세|상한가|오름|↑|신고가|돌파|껑충|날개|훨훨|불기둥")


def passes(s):
    return s["chg"] >= THRESH_CHG and s["val"] >= THRESH_VALUE


def news_items(code, oldest, newest):
    """oldest~newest(YYYYMMDD) 날짜의 이유를 고를 수 있게 기사를 캐시에 모은다. 이미 덮고 있으면 재사용."""
    f = RAW / "news_cache" / f"{code}.json"
    c = load(f, {"items": [], "oldest": "99999999", "fetched": ""})
    now = datetime.now(KST).strftime("%Y%m%d%H%M")
    need_old = c["oldest"] > oldest
    need_new = c["fetched"] < newest + "1800" and c["fetched"] < now
    if not need_old and not need_new:
        return c["items"]
    stop = oldest if need_old else c["fetched"][:8]
    got_all = []
    for pg in range(1, 41):
        j = jget(f"{API}/news/stock/{code}?pageSize=50&page={pg}")
        got = [it for g in (j or []) for it in g.get("items", [])]
        if not got:
            break
        got_all += [{"dt": it["datetime"], "t": html.unescape(it.get("titleFull") or it["title"]),
                     "o": it.get("officeName", ""), "u": it.get("mobileNewsUrl", "")} for it in got]
        if min(it["datetime"] for it in got)[:8] < stop:
            break
    nm = clean_name(load(RAW / "names.json", {}).get(code, ""))
    if nm:                                   # 이유로 쓰는 건 종목명이 제목에 있는 기사뿐 → 나머지는 저장 안 함
        got_all = [x for x in got_all if nm in x["t"]]
    merged = {x["u"] or x["dt"] + x["t"]: x for x in c["items"] + got_all}
    allit = sorted(merged.values(), key=lambda x: x["dt"], reverse=True)
    reached = min((x["dt"][:8] for x in got_all), default="99999999")
    c = {"items": allit, "oldest": min(c["oldest"], reached if need_old else c["oldest"]), "fetched": now}
    save(f, c)
    return allit


SH = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36", "Accept-Language": "ko-KR,ko;q=0.9"}
ROBOT = re.compile(r"주가,? ?\d+월 ?\d+일|장중 [\d,]+원|회전율|거래량 상위|상승률 상위|\d+% (?:상승|하락) 마감$|"
                   r"장중수급포착|동시 순매수|급등세\.\.\. ?왜|주가 [+-]?\d")
WHY = re.compile(r"수혜|기대|계약|공급|실적|인수|승인|임상|개발|협력|MOU|정책|수주|흑자|상장|지정|선정|출시|투자|"
                 r"합병|특허|정부|美|中|엔비디아|마이크론|테슬라|애플|삼성|SK|부각|러브콜|모멘텀|소식|발표")


def clean_name(name):
    return re.sub(r"\(.*?\)", "", name).strip()


def search_news(name, d, fetch=True):
    """네이버 뉴스 웹 검색(그날 날짜 필터) 결과를 캐시. 종목 뉴스 API는 소형주 기사가 거의 안 걸려서 이게 주 소스다."""
    q = clean_name(name)
    f = RAW / "search_cache" / f"{d}_{re.sub(r'[^0-9A-Za-z가-힣]', '_', q)}.json"
    c = load(f)
    if c is not None or not fetch:
        return c
    dd = f"{d[:4]}.{d[4:6]}.{d[6:]}"
    u = ("https://search.naver.com/search.naver?where=news&sort=0&query=" + requests.utils.quote(q) +
         f"&ds={dd}&de={dd}&nso=so:r,p:from{d}to{d}")
    for i in range(3):
        try:
            r = requests.get(u, headers=SH, timeout=15)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(2 + i * 3)
    else:
        return None
    t = r.text
    out = []
    for href, ti in re.findall(r'href="([^"]+)"[^>]*data-heatmap-target="\.tit"><span[^>]*>(.*?)</span>', t):
        ti = html.unescape(re.sub(r"<[^>]+>", "", ti)).strip()
        j = t.find(href)
        press = re.findall(r'data-heatmap-target="\.prof"[^>]*>(?:<[^>]+>)*([^<]{2,30})<', t[max(0, j - 4000):j])
        out.append({"t": ti, "u": html.unescape(href), "o": press[-1] if press else ""})
    save(f, out)
    return out


def gnews(name, d, prev_d, fetch=True):
    """구글 뉴스 RSS(after/before 날짜 연산자). 네이버 웹검색은 40건 남짓에서 403으로 막혀서 이게 주 소스다(2026-10-07)."""
    q = clean_name(name)
    f = RAW / "gnews_cache" / f"{d}_{re.sub(r'[^0-9A-Za-z가-힣]', '_', q)}.json"
    c = load(f)
    if c is not None or not fetch:
        return c
    a = datetime.strptime(prev_d, "%Y%m%d").strftime("%Y-%m-%d")
    b = (datetime.strptime(d, "%Y%m%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    u = ("https://news.google.com/rss/search?q=" + requests.utils.quote(f'"{q}" after:{a} before:{b}') +
         "&hl=ko&gl=KR&ceid=KR:ko")
    for i in range(4):
        try:
            r = requests.get(u, headers=H, timeout=15)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(5 * (i + 1))
    else:
        return None
    out = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        t = html.unescape(re.search(r"<title>(.*?)</title>", it, re.S).group(1))
        src = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
        src = html.unescape(src.group(1)) if src else ""
        if src and t.endswith(" - " + src):
            t = t[: -len(src) - 3]
        pd = re.search(r"<pubDate>(.*?)</pubDate>", it).group(1)
        dt = (datetime.strptime(pd[5:25], "%d %b %Y %H:%M:%S") + timedelta(hours=9)).strftime("%Y%m%d%H%M")
        link = re.search(r"<link>(.*?)</link>", it, re.S).group(1).strip()
        if q in t:                           # 종목명 없는 기사는 이유로 안 쓰므로 저장 안 함
            out.append({"t": t.strip(), "u": link, "o": src if not src.startswith("http") else "", "dt": dt})
    save(f, out)
    return out


def best_search(items, name, d=None):
    q = clean_name(name)
    best, bs = None, -99
    for it in items or []:
        t = it["t"]
        if q not in t:
            continue
        s = (3 if "특징주" in t else 0) + min(3, len(WHY.findall(t))) + (1 if UPW.search(t) else 0) \
            - (4 if ROBOT.search(t) else 0) + (2 if (d is None or it.get("dt", d)[:8] == d) else 0)
        if s > bs:
            best, bs = it, s
    return best


def pick_news(items, name, d, prev_d):
    lo, hi = prev_d + "1530", d + "1800"
    best, bs = None, -9
    q = clean_name(name)
    for it in items:
        if not (lo <= it["dt"] <= hi) or q not in it["t"]:
            continue                                   # 종목명이 없는 기사는 이유로 쓰지 않는다
        t = it["t"]
        s = (3 if "특징주" in t else 0) + (1 if UPW.search(t) else 0)
        if s > bs or (s == bs and best and it["dt"] < best["dt"] and it["dt"][:8] == d):
            best, bs = it, s
    if best is None:
        return None
    return {**best, "own": name in best["t"]}


def news_all():
    days = sorted(p.stem for p in (RAW / "days").glob("*.json"))
    need = defaultdict(list)
    for i, d in enumerate(days):
        day = load(RAW / "days" / f"{d}.json")
        for s in day["stocks"]:
            if passes(s):
                need[s["code"]].append(d)
    names = load(RAW / "names.json", {})
    for d in days:
        for x in load(RAW / "days" / f"{d}.json")["stocks"]:
            names[x["code"]] = x["name"]
    save(RAW / "names.json", names)
    print("news stocks", len(need))

    def job(code):
        oldest = min(need[code])
        try:
            news_items(code, (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d"),
                       max(need[code]))
        except Exception as e:
            print("news fail", code, e)
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(job, list(need)))

    pairs = []
    for i, d in enumerate(days):
        prev_d = days[i - 1] if i else d
        for s in load(RAW / "days" / f"{d}.json")["stocks"]:
            if passes(s) and gnews(s["name"], d, prev_d, fetch=False) is None:
                pairs.append((s["name"], d, prev_d))
    print("gnews pairs", len(pairs), flush=True)

    def sjob(p):
        r = gnews(*p)
        time.sleep(1.0)
        return r is not None
    with ThreadPoolExecutor(2) as ex:
        ok = sum(ex.map(sjob, pairs))
    print("gnews ok", ok, "/", len(pairs), flush=True)


# ── 분류·빌드 ─────────────────────────────────────────
def classify(stocks, sg):
    rem = [s for s in stocks if passes(s)]
    themes = []
    while rem:
        cnt = defaultdict(list)
        for s in rem:
            for g in sg.get(s["code"], ()):
                cnt[g].append(s)
        if not cnt:
            break
        # 거래대금 가점 합 → 종목 수 → 더 구체적인(작은) 테마 순
        g, ss = max(cnt.items(), key=lambda kv: (round(sum(weight(x) for x in kv[1]), 6), len(kv[1]),
                                                  -TSIZE.get(kv[0], 999)))
        if len(ss) < THEME_MIN:
            break
        themes.append({"name": g, "stocks": sorted(ss, key=lambda x: -x["chg"]),
                       "score": round(sum(weight(x) for x in ss), 2)})
        ids = {x["code"] for x in ss}
        rem = [s for s in rem if s["code"] not in ids]
    if rem:
        themes.append({"name": "기타(개별)", "stocks": sorted(rem, key=lambda x: -x["chg"]), "etc": True})
    lead = 0
    for t in themes:
        t["lead"] = (not t.get("etc")) and lead < LEAD_MAX and \
            (len(t["stocks"]) >= LEAD_MIN or t.get("score", 0) >= LEAD_SCORE)
        lead += t["lead"]
    return themes


def build():
    sg, unknown = stock_groups()
    if unknown:
        print("⚠ 매핑 안 된 네이버 테마:", sorted(unknown))
    days = sorted(p.stem for p in (RAW / "days").glob("*.json"))
    hist_lead, hist_seen = [], []
    stat_rows = []
    months = defaultdict(list)
    search = defaultdict(set)
    for i, d in enumerate(days):
        day = load(RAW / "days" / f"{d}.json")
        prev_d = days[i - 1] if i else d
        themes = classify(day["stocks"], sg)
        nx = load(RAW / "nxt" / f"{d}.json", {})
        hist_lead.append({t["name"] for t in themes if t["lead"]})
        hist_seen.append({t["name"] for t in themes if not t.get("etc")})
        win_l, win_s = hist_lead[-WINDOW:], hist_seen[-WINDOW:]
        srcs = []
        out_t = []
        for t in themes:
            rows = []
            for s in t["stocks"]:
                cand = (search_news(s["name"], d, fetch=False) or []) +                     [x for x in (gnews(s["name"], d, prev_d, fetch=False) or [])
                     if prev_d + "1530" <= x["dt"] <= d + "1800"]
                nw = best_search(cand, s["name"], d)
                if nw is None:
                    nc = load(RAW / "news_cache" / f"{s['code']}.json", {"items": []})
                    nw = pick_news(nc["items"], s["name"], d, prev_d)
                r = {"c": s["code"], "n": s["name"], "chg": s["chg"], "val": s["val"]}
                kws = sorted(sg.get(s["code"], set()) - {t["name"]}, key=lambda k: TSIZE.get(k, 999))[:3]
                if kws:
                    r["kw"] = kws
                if s["code"] in nx:
                    r["nx"] = nx[s["code"]]["tot"]
                    r["nxe"] = nx[s["code"]]["ext"]
                if nw:
                    r["why"], r["u"], r["o"] = nw["t"], nw["u"], nw["o"]
                    srcs.append({"t": nw["t"], "u": nw["u"], "o": nw["o"], "chg": s["chg"]})
                else:
                    r["co"] = 1
                rows.append(r)
                search[s["name"]].add(d)
            nm = t["name"]
            search[nm].add(d)
            life = None
            if not t.get("etc"):
                k = 0
                while k < len(hist_lead) and nm in hist_lead[-1 - k]:
                    k += 1
                if t["lead"] and k >= 2:
                    life = f"{k}일째"
                elif not any(nm in x for x in hist_seen[-1 - NEW_LOOKBACK:-1]) and i >= NEW_LOOKBACK:
                    life = "NEW"
            out_t.append({"name": nm, "lead": t["lead"], "etc": bool(t.get("etc")), "stocks": rows, "life": life,
                          "val": round(sum(r["val"] for r in rows), 1),
                          "lead3m": sum(nm in x for x in win_l), "seen3m": sum(nm in x for x in win_s)})
        n = sum(len(t["stocks"]) for t in themes)
        leads = [t for t in out_t if t["lead"]]
        top = max((r for t in out_t for r in t["stocks"]), key=lambda r: r["chg"], default=None)
        if leads:
            summ = "·".join(f"{t['name']}({len(t['stocks'])})" for t in leads) + " 주도."
        elif out_t:
            summ = "뚜렷한 주도 테마 없이 개별 종목 장세."
        else:
            summ = "기준을 넘긴 급등 종목이 없었다."
        if top:
            summ += f" 최고 상승 {top['n']} {top['chg']:+.1f}%."
        srcs = sorted({s["u"]: s for s in srcs}.values(), key=lambda s: -s["chg"])[:8]
        cool = []
        for back in range(1, COOL_DAYS + 1):
            if i - back < 0:
                break
            for nm in sorted(hist_lead[-1 - back]):
                if nm not in hist_lead[-1] and nm not in cool:
                    cool.append(nm)
        cool = cool[:3]
        for t in out_t:
            if t["lead"]:
                k = int(t["life"][:-2]) if t["life"] and t["life"].endswith("일째") else 1
                grp = "lead1" if k == 1 else "lead2"
            else:
                grp = "other"
            for r in t["stocks"]:
                stat_rows.append((d, grp, r["c"]))
        months[d[:6]].append({"d": d, "kospi": day["kospi"], "kosdaq": day["kosdaq"], "n": n,
                              "src": day.get("src"), "summary": summ, "themes": out_t, "win": len(win_l),
                              "news": [{"t": s["t"], "u": s["u"], "o": s["o"]} for s in srcs], "cool": cool})
    for m, arr in months.items():
        save(SITE / "data" / f"{m[:4]}-{m[4:]}.json", {"month": m, "days": arr})
    save(SITE / "data" / "index.json", {
        "months": sorted(f"{m[:4]}-{m[4:]}" for m in months), "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "rule": {"chg": THRESH_CHG, "val": THRESH_VALUE, "lead_min": LEAD_MIN, "lead_score": LEAD_SCORE,
                 "unit": VALUE_UNIT, "window": WINDOW},
        "backfill_until": max((d for d in days if (load(RAW / "days" / f"{d}.json") or {}).get("src") == "backfill"),
                              default=""),
        "search": {k: sorted(v) for k, v in search.items()}})
    print("build", len(days), "days", len(months), "months")
    stats(stat_rows, days)


# ── 가격 캐시·성과 통계 (2026-10-07 시제품 → 본편) ─────────────────────
def px_update(days):
    """급등했던 종목 + 지수의 일봉(시가·종가)을 raw/px.json에 모은다. 최근 30거래일 급등주만 새로 받는다."""
    f = RAW / "px.json"
    px = load(f, {})
    if not px and (RAW / "prices.json").exists():          # 최초 1회: 소급용 대량 파일에서 급등주만 추림
        P = load(RAW / "prices.json")["prices"]
        codes = {x["code"] for d in days for x in load(RAW / "days" / f"{d}.json")["stocks"] if passes(x)}
        px = {c: {r[0]: [r[1], r[4]] for r in P[c]} for c in codes | {"KOSPI", "KOSDAQ"} if c in P}
    recent = days[-30:]
    codes = {x["code"] for d in recent for x in load(RAW / "days" / f"{d}.json")["stocks"] if passes(x)}
    codes |= {"KOSPI", "KOSDAQ"}
    start = (datetime.strptime(recent[0], "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")
    end = datetime.now(KST).strftime("%Y%m%d")

    def job(c):
        u = (f"https://api.finance.naver.com/siseJson.naver?symbol={c}&requestType=1"
             f"&startTime={start}&endTime={end}&timeframe=day")
        try:
            t = requests.get(u, headers=H, timeout=15).text
        except Exception:
            return c, []
        return c, re.findall(r'\["(\d{8})",\s*([\d.]+),\s*[\d.]+,\s*[\d.]+,\s*([\d.]+)', t)
    with ThreadPoolExecutor(6) as ex:
        for c, rows in ex.map(job, sorted(codes)):
            for d, o, cl in rows:
                px.setdefault(c, {})[d] = [float(o), float(cl)]
    save(f, px)
    return px


def stats(rows, days):
    px = load(RAW / "px.json", {})
    if not px:
        print("px.json 없음 — 통계 건너뜀")
        return
    mk = {x["code"]: x.get("mk", "KOSDAQ") for d in days for x in load(RAW / "days" / f"{d}.json")["stocks"]}
    cal = sorted(px.get("KOSPI", {}))

    def fwd(c, d, h):
        if d not in cal:
            return None
        j = cal.index(d)
        if j + h >= len(cal):
            return None
        d1, dh = cal[j + 1], cal[j + h]
        a, b = px.get(c, {}).get(d1), px.get(c, {}).get(dh)
        return (b[1] / a[0] - 1) * 100 if a and b and a[0] > 0 else None
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for d, grp, c in rows:
        ix = "KOSPI" if mk.get(c) == "KOSPI" else "KOSDAQ"
        for h in STAT_H:
            r, m = fwd(c, d, h), fwd(ix, d, h)
            if r is not None and m is not None:
                agg[grp][h][d].append(r - m)
    out = {}
    for grp, hs in agg.items():
        out[grp] = {}
        for h, byd in hs.items():
            per = [sum(v) / len(v) for v in byd.values()]
            n = sum(len(v) for v in byd.values())
            out[grp][str(h)] = {"mean": round(sum(per) / len(per), 2), "win": round(sum(x > 0 for x in per) / len(per) * 100),
                                "days": len(per), "n": n}
    save(SITE / "data" / "stats.json", {"groups": out, "from": days[0], "to": days[-1],
                                        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M")})
    print("stats", {g: {h: v["mean"] for h, v in hs.items()} for g, hs in out.items()})


# ── 공모주 일정 (2026-10-07 사용자: "공모주 청약일정 내 캘린더에 저장해줘. 우선 멜콘/엘리스그룹만") ──
IPO_WATCH = ["멜콘", "엘리스그룹"]      # 빈 리스트로 두면 38커뮤니케이션 목록 전체를 싣는다
# ⚠ 2026-10-07 사용자 정정: 공모주는 폰 캘린더(ics)로, 사이트는 테마 달력만. daily에서 호출하지 않는다(수동 `ipo` 명령만 남김)


def ipo():
    """38커뮤니케이션 청약일정 표 → 상세 페이지에서 청약·환불·상장일을 읽어 site/data/ipo.json"""
    def page(u):
        r = requests.get(u, headers=H, timeout=20)
        return r.content.decode("euc-kr", "ignore")
    t = page("https://www.38.co.kr/html/fund/index.htm?o=k")
    rows = re.findall(r'href="/html/fund/\?o=v&(?:amp;)?no=(\d+)[^"]*"[^>]*>(.*?)</a>', t, re.S)
    old = {e["no"]: e for e in load(SITE / "data" / "ipo.json", {"items": []})["items"]}
    items, done = [], set()
    for no, nm in rows:
        nm = html.unescape(re.sub(r"<[^>]+>", "", nm)).strip()
        if no in done or re.match(r"\d{2}/\d{2} ", nm):      # 옆 메뉴의 "10/07 종목" 링크는 건너뜀
            continue
        done.add(no)
        if IPO_WATCH and not any(w in nm for w in IPO_WATCH):
            continue
        dt = page(f"https://www.38.co.kr/html/fund/?o=v&no={no}")
        tx = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", dt)))

        def grab(label, pat=r"(\d{4}\.\d{2}\.\d{2})(?: ?~ ?(\d{4}\.\d{2}\.\d{2}|\d{2}\.\d{2}|\d{2}))?"):
            m = re.search(re.escape(label) + r"\s*:?\s*" + pat, tx)
            return m.groups() if m else None
        sub, ref, lst = grab("공모청약일"), grab("환불일"), grab("상장일")
        price = re.search(r"확정공모가\s*:?\s*([\d,]+)\s*원", tx)
        band = re.search(r"희망공모가액\s*:?\s*([\d,]+\s*~\s*[\d,]+)", tx)
        broker = re.search(r"주간사\s*:?\s*([^\s][^주]{1,40}?)\s+(?:주식수|인수회사)", tx)
        comp = re.search(r"청약경쟁률\s*:?\s*([\d.,]+\s*:\s*1)", tx)

        def ymd(a, b=None):
            if not a:
                return None
            s = a.replace(".", "")
            if not b:
                return [s, s]
            e = b.replace(".", "")
            e = s[:8 - len(e)] + e                   # "08" / "10.08" / 전체 날짜 모두 처리
            return [s, e]
        ev = {"no": no, "name": re.sub(r"\(구\..*?\)", "", nm).strip(),
              "sub": ymd(*sub) if sub else None, "refund": ymd(ref[0]) if ref else None,
              "list": ymd(lst[0]) if lst else None,
              "price": price.group(1) if price else "", "band": band.group(1).replace(" ", "") if band else "",
              "broker": broker.group(1).strip() if broker else "", "comp": comp.group(1).replace(" ", "") if comp else "",
              "url": f"https://www.38.co.kr/html/fund/?o=v&no={no}"}
        if not ev["sub"] and no in old:              # 파싱 실패 시 이전 값 유지
            ev = old[no]
        items.append(ev)
        time.sleep(0.3)
    for no, e in old.items():                        # 목록에서 빠진 지난 공모주도 기록으로 남김
        if no not in {x["no"] for x in items}:
            items.append(e)
    save(SITE / "data" / "ipo.json", {"items": items, "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M")})
    print("ipo", [(e["name"], e["sub"], e["refund"], e["list"], e["price"]) for e in items])


def publish(msg=None):
    msg = msg or "update " + datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    run = lambda *a: subprocess.run(["git", "-C", str(SITE), *a], capture_output=True, text=True)  # noqa: E731
    run("add", "-A")
    c = run("commit", "-m", msg)
    if "nothing to commit" in c.stdout:
        print("변경 없음")
        return True
    run("pull", "--rebase", "--autostash", "origin", "main")
    p = run("push", "origin", "main")
    print("push", p.returncode, p.stderr[-300:])
    return p.returncode == 0


def notify_fail(what):
    try:
        TELEGRAM_TOKEN, ADMIN_ID = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("ADMIN_ID")
        if not TELEGRAM_TOKEN:
            sys.path.insert(0, r"C:\kiwoom")
            from kiwoom_config import TELEGRAM_TOKEN, ADMIN_ID
        what = f"{what} ({'깃허브 액션' if IN_ACTIONS else 'PC'})"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": ADMIN_ID, "text": f"⚠️ <b>테마 달력</b> {what} 실패", "parse_mode": "HTML"},
                      timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "backfill":
        backfill()
    elif cmd == "collect":
        collect()
    elif cmd == "nxt":
        nxt(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "news":
        news_all()
    elif cmd == "build":
        build()
    elif cmd == "px":
        px_update(sorted(p.stem for p in (RAW / "days").glob("*.json")))
    elif cmd == "publish":
        publish()
    elif cmd == "ipo":
        ipo()
    elif cmd == "probe":                     # 깃허브 액션에서 외부 접속 시험(쓰기 없음)
        print("index", index_today())
        j = jget(f"{API}/stocks/up/KOSDAQ?page=1&pageSize=5") or {}
        print("up", [(x["stockName"], x["fluctuationsRatio"]) for x in j.get("stocks", [])])
        j = jget(f"{API}/stock/005930/basic") or {}
        print("nxt", (j.get("overMarketPriceInfo") or {}).get("overPrice"))
        t0 = datetime.now(KST)
        r = gnews("삼성전자", t0.strftime("%Y%m%d"), (t0 - timedelta(days=1)).strftime("%Y%m%d"), fetch=True)
        print("gnews", len(r or []), (r or [{}])[0].get("t"))
        print("stocknews", bool(jget(f"{API}/news/stock/005930?pageSize=5&page=1")))
        print("themes", len((jget(f"{API}/stocks/theme?page=1&pageSize=100") or {}).get("groups", [])))
        t = requests.get("https://api.finance.naver.com/siseJson.naver?symbol=005930&requestType=1&startTime=20261001"
                         "&endTime=20261006&timeframe=day", headers=H, timeout=15).text
        print("siseJson", t.count('["2026'))
    elif cmd == "daily":
        today = datetime.now(KST).strftime("%Y%m%d")
        if not IN_ACTIONS:                   # PC = 예비 실행. 깃허브 액션이 이미 했으면 건너뜀
            subprocess.run(["git", "-C", str(SITE), "pull", "--rebase", "--autostash", "origin", "main"],
                           capture_output=True, text=True)
            done = (RAW / "nxt" / f"{today}.json").exists() if "--nxt" in sys.argv else \
                (load(RAW / "days" / f"{today}.json") or {}).get("src") == "naver_live"
            if done:
                print("이미 갱신됨(깃허브 액션) — PC 예비 실행 건너뜀")
                sys.exit(0)
        try:
            if "--nxt" in sys.argv:
                nxt()
            else:
                refresh_themes()
                if collect() is None:
                    sys.exit(0)
                news_all()
                try:
                    px_update(sorted(p.stem for p in (RAW / "days").glob("*.json")))
                except Exception as e:
                    print("px_update fail", e)
            build()
            if not publish():
                notify_fail("깃허브 push")
        except Exception as e:
            print("daily error", e)
            notify_fail(f"{'넥장' if '--nxt' in sys.argv else '본장'} 갱신 ({e})")
            raise
