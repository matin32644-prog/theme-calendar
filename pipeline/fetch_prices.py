# -*- coding: utf-8 -*-
"""
테마 달력 소급용 일봉 수집 — 2026-10-07
네이버 siseJson(종목당 1회 호출)으로 코스피·코스닥 보통주 전 종목의 일봉을 받아 raw/prices.json에 저장.
사용: py -X utf8 fetch_prices.py 20260625 20261006
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import FinanceDataReader as fdr
import requests

BASE = Path(__file__).parent
H = {"User-Agent": "Mozilla/5.0"}
START, END = sys.argv[1], sys.argv[2]


def universe():
    df = fdr.StockListing("KRX")
    df = df[df.Market.isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
    df = df[df.Code.str[-1] == "0"]                       # 우선주 제외
    df = df[~df.Name.str.contains("스팩|SPAC", regex=True)]
    return {r.Code: {"name": r.Name, "market": "KOSPI" if r.Market == "KOSPI" else "KOSDAQ"} for r in df.itertuples()}


def sise(code):
    u = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
         f"&startTime={START}&endTime={END}&timeframe=day")
    for i in range(3):
        try:
            t = requests.get(u, headers=H, timeout=15).text
            rows = re.findall(r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)', t)
            return code, [[d, float(o), float(h), float(l), float(c), float(v)] for d, o, h, l, c, v in rows]
        except Exception:
            time.sleep(1 + i)
    return code, None


if __name__ == "__main__":
    uni = universe()
    print("universe", len(uni))
    out, fail = {}, []
    with ThreadPoolExecutor(6) as ex:
        futs = [ex.submit(sise, c) for c in list(uni) + ["KOSPI", "KOSDAQ"]]
        for n, f in enumerate(as_completed(futs), 1):
            c, rows = f.result()
            if rows is None:
                fail.append(c)
            else:
                out[c] = rows
            if n % 300 == 0:
                print(n, "done", flush=True)
    json.dump({"universe": uni, "prices": out, "range": [START, END]},
              open(BASE / "raw" / "prices.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("saved", len(out), "fail", len(fail), fail[:10])
