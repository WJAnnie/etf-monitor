from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CN_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 15
OUT = Path("data/latest_market_data.json")

# 技术分析代理标的。场外联接基金不伪造盘中K线，使用其主要跟踪指数/场内代理。
SYMBOLS = [
    {"key":"csi500","name":"中证500","secid":"1.000905","market":"CN","proxy_for":"中证500核心仓"},
    {"key":"hk_stock_connect_tech","name":"中证港股通科技指数","secid":"100.H30533","market":"HK","proxy_for":"016496"},
    {"key":"hang_seng_tech","name":"恒生科技指数","secid":"100.HSTECH","market":"HK","proxy_for":"013172"},
    {"key":"bse50","name":"北证50","secid":"0.899050","market":"CN","proxy_for":"北证50"},
    {"key":"china_film","name":"中国电影","secid":"1.600977","market":"CN","proxy_for":"600977"},
    {"key":"battery_etf","name":"电池ETF","secid":"1.561160","market":"CN","proxy_for":"561160"},
    {"key":"medical_device","name":"中证全指医疗器械指数","secid":"1.931484","market":"CN","proxy_for":"017633"},
    {"key":"dividend_lowvol","name":"红利低波ETF","secid":"1.512890","market":"CN","proxy_for":"红利组合"},
]


def get_json(url: str, params: dict) -> dict:
    headers={"User-Agent":"Mozilla/5.0","Referer":"https://quote.eastmoney.com/"}
    last=None
    for i in range(4):
        try:
            r=requests.get(url,params=params,headers=headers,timeout=TIMEOUT)
            r.raise_for_status(); return r.json()
        except Exception as e:
            last=e; time.sleep(1.5*(i+1))
    raise RuntimeError(str(last))


def kline(secid: str, klt: int, limit: int) -> list[dict]:
    p=get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get",{
        "secid":secid,"fields1":"f1,f2,f3,f4,f5,f6","fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt":str(klt),"fqt":"1","beg":"0","end":"20500101","lmt":str(limit)})
    rows=((p.get("data") or {}).get("klines") or [])
    out=[]
    for x in rows:
        a=str(x).split(",")
        if len(a)>=7:
            out.append({"time":a[0],"open":float(a[1]),"close":float(a[2]),"high":float(a[3]),"low":float(a[4]),"volume":float(a[5]),"amount":float(a[6])})
    return out


def ema(xs,n):
    if not xs:return []
    a=2/(n+1); out=[xs[0]]
    for x in xs[1:]:out.append(a*x+(1-a)*out[-1])
    return out


def indicators(rows):
    c=[x["close"] for x in rows]; h=[x["high"] for x in rows]; l=[x["low"] for x in rows]
    if len(c)<30:return {}
    e12,e26=ema(c,12),ema(c,26); diff=[a-b for a,b in zip(e12,e26)]; dea=ema(diff,9); macd=[2*(a-b) for a,b in zip(diff,dea)]
    ma20=sum(c[-20:])/20; sd=(sum((x-ma20)**2 for x in c[-20:])/20)**.5
    gains=[]; losses=[]
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains[-14:])/14; al=sum(losses[-14:])/14; rsi=100 if al==0 else 100-100/(1+ag/al)
    ll=min(l[-9:]); hh=max(h[-9:]); rsv=50 if hh==ll else (c[-1]-ll)/(hh-ll)*100
    # 输出当前指标；结构/买卖点由分析层结合完整K线判定，避免脚本伪判缠论。
    return {"macd":{"diff":diff[-1],"dea":dea[-1],"hist":macd[-1]},"boll":{"mid":ma20,"upper":ma20+2*sd,"lower":ma20-2*sd},"rsi14":rsi,"kdj_rsv9":rsv,
            "recent_high_5":max(h[-5:]),"recent_low_5":min(l[-5:])}


def main():
    now=datetime.now(CN_TZ)
    result={"generated_at":now.isoformat(),"note":"场外基金采用跟踪指数/ETF代理；缠论买卖点不由脚本机械确认。","symbols":{}}
    for s in SYMBOLS:
        item={**s,"daily":[],"m15":[],"errors":[]}
        try:item["daily"]=kline(s["secid"],101,180)
        except Exception as e:item["errors"].append("daily: "+str(e))
        try:item["m15"]=kline(s["secid"],15,320)
        except Exception as e:item["errors"].append("m15: "+str(e))
        item["indicators"]={"daily":indicators(item["daily"]),"m15":indicators(item["m15"])}
        result["symbols"][s["key"]]=item
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"wrote {OUT}; symbols={len(result['symbols'])}")
    for k,v in result["symbols"].items():print(k,len(v["daily"]),len(v["m15"]),v["errors"])

if __name__=="__main__":main()
