# -*- coding: utf-8 -*-
"""KRX 정규장 종가 복원 (2026-10-14)

왜: 네이버 일봉(siseJson·/api/stock/{code}/price)의 «종가»는 넥스트레이드 애프터마켓(~20:00)까지 포함한 마지막 가격이다.
    9월 중순부터 소형주까지 애프터마켓 거래가 붙으면서 정규장 등락률과 크게 어긋났다
    (예: 미투온 9/15 네이버 4,030원 +34.1% / KRX 정규장 2,955원 −5.0%, 트레이딩뷰 KRX 일봉으로 확인).
어떻게: 같은 API의 «전일 대비»는 KRX 정규장 종가 기준이다. 그래서
    KRX 종가(D−1) = 네이버 종가(D) − 전일 대비(D)   (미투온 9/14~9/16·국제약품·안랩 10/12~10/13 트레이딩뷰와 일치)
    마지막 날 종가는 다음 날 기록이 있어야 나온다 → 오늘 값은 collect()의 트레이딩뷰 스캐너가 맡는다.
출력: raw/krx_px.json {code: {yyyymmdd: [시가, KRX 종가, 거래량(통합), KRX 확정 1/0]}} (PC 전용 소급 자료)

    py -X utf8 krx_close.py [시작일 yyyymmdd]
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = Path(__file__).parent
RAW = BASE / "raw"
H = {"User-Agent": "Mozilla/5.0"}
START = "20260102"


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def rows_of(code, start=None):
    start = start or START
    out = []
    for pg in range(1, 8):
        j = None
        for i in range(3):
            try:
                r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/price?pageSize=60&page={pg}",
                                 headers=H, timeout=15)
                j = r.json()
                break
            except Exception:
                time.sleep(1 + i)
        if not j:
            break
        for x in j:
            d = x["localTradedAt"][:10].replace("-", "")
            out.append((d, num(x.get("closePrice")), num(x.get("compareToPreviousClosePrice")),
                        num(x.get("openPrice")), num(x.get("accumulatedTradingVolume"))))
        if len(j) < 60 or out[-1][0] < start:
            break
    return code, sorted(set(out))


def krx_closes(rows, start=None):
    start = start or START
    """[(날짜, 네이버 종가, 전일 대비, 시가, 거래량)] 오름차순 → {날짜: KRX 종가} (마지막 날은 빠짐)"""
    got = {}
    for k in range(1, len(rows)):
        d_prev = rows[k - 1][0]
        cl, cmp_ = rows[k][1], rows[k][2]
        if cl is not None and cmp_ is not None and d_prev >= start:
            got[d_prev] = cl - cmp_
    return got


def krx_px(rows, start=None):
    start = start or START
    """→ {날짜: [시가, KRX 종가, 거래량(통합)]}. 마지막 날은 KRX 종가를 모르므로 네이버 종가로 두고 4번째 칸에 0 표시"""
    kc = krx_closes(rows, start)
    out = {}
    for d, cl, _, op, vol in rows:
        if d < start or op is None:
            continue
        out[d] = [op, kc[d], vol or 0, 1] if d in kc else [op, cl, vol or 0, 0]
    return out


def universe():
    codes = set()
    p = RAW / "prices.json"
    if p.exists():
        codes |= set(json.load(open(p, encoding="utf-8"))["universe"])
    for f in (RAW / "days").glob("*.json"):
        codes |= {x["code"] for x in json.load(open(f, encoding="utf-8"))["stocks"]}
    codes |= set(json.load(open(RAW / "base_codes.json", encoding="utf-8")))
    return sorted(codes)


if __name__ == "__main__":
    START = sys.argv[1] if len(sys.argv) > 1 else START
    uni = universe()
    print("universe", len(uni), "from", START, flush=True)
    out, fail = {}, []
    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        for n, (c, rows) in enumerate(ex.map(rows_of, uni), 1):
            if not rows:
                fail.append(c)
            else:
                out[c] = krx_px(rows)
            if n % 500 == 0:
                print(n, "done", round(time.time() - t0), "s", flush=True)
    json.dump(out, open(RAW / "krx_px.json", "w", encoding="utf-8"), separators=(",", ":"))
    print("saved", len(out), "fail", len(fail), fail[:10], round(time.time() - t0), "s")
