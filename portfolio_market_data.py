from __future__ import annotations

import json
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CN_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 15
OUT = Path("data/latest_market_data.json")
SUMMARY_OUT = Path("data/latest_market_summary.json")

# 技术分析代理标的。场外联接基金不伪造盘中K线，使用其跟踪指数/目标ETF代理。
SYMBOLS = [
    {"key":"csi500","name":"中证500","secid":"1.000905","market":"CN","proxy_for":"中证500核心仓"},
    {"key":"hk_stock_connect_tech","name":"中证港股通科技指数","secid":"1.931573","market":"HK_INDEX","proxy_for":"016496"},
    {"key":"hk_stock_connect_tech_etf","name":"港股通科技ETF","secid":"1.513980","market":"CN","proxy_for":"016496目标ETF"},
    {"key":"hang_seng_tech","name":"恒生科技指数","secid":"100.HSTECH","market":"HK_INDEX","proxy_for":"013172"},
    {"key":"bse50","name":"北证50","secid":"0.899050","market":"CN","proxy_for":"北证50"},
    {"key":"china_film","name":"中国电影","secid":"1.600977","market":"CN","proxy_for":"600977"},
    {"key":"battery_etf","name":"电池ETF","secid":"1.561160","market":"CN","proxy_for":"561160"},
    {"key":"medical_device","name":"中证全指医疗器械指数","secid":"1.931484","market":"CN_INDEX","proxy_for":"017633"},
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
    if not out:
        raise RuntimeError(f"empty kline: secid={secid}, klt={klt}")
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
    k=d=50.0
    for i in range(len(c)):
        start=max(0,i-8); ll=min(l[start:i+1]); hh=max(h[start:i+1]); rsv=50.0 if hh==ll else (c[i]-ll)/(hh-ll)*100
        k=(2*k+rsv)/3; d=(2*d+k)/3
    j=3*k-2*d
    return {"macd":{"diff":diff[-1],"dea":dea[-1],"hist":macd[-1]},"boll":{"mid":ma20,"upper":ma20+2*sd,"lower":ma20-2*sd},"rsi14":rsi,
            "kdj":{"k":k,"d":d,"j":j},"recent_high_5":max(h[-5:]),"recent_low_5":min(l[-5:])}


def latest_time(rows: list[dict]) -> str | None:
    return rows[-1]["time"] if rows else None


def covers_1400_bar(rows: list[dict], generated_at: datetime) -> bool:
    latest=latest_time(rows)
    if not latest:
        return False
    try:
        bar_time=datetime.strptime(latest,"%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
    except ValueError:
        return False
    return bar_time.date()==generated_at.date() and bar_time.time()>=dt_time(14,0)


def build_summary(result: dict, generated_at: datetime) -> dict:
    symbols={}
    for key,item in result["symbols"].items():
        daily_count=len(item["daily"])
        m15_count=len(item["m15"])
        daily_ok=daily_count>=60
        m15_ok=m15_count>=80
        no_errors=not item["errors"]
        usable=daily_ok and m15_ok and no_errors
        symbols[key]={
            "name":item["name"],
            "secid":item["secid"],
            "market":item["market"],
            "proxy_for":item["proxy_for"],
            "daily_count":daily_count,
            "m15_count":m15_count,
            "latest_daily_time":latest_time(item["daily"]),
            "latest_m15_time":latest_time(item["m15"]),
            "daily_history_ok":daily_ok,
            "m15_history_ok":m15_ok,
            "no_errors":no_errors,
            "usable_for_analysis":usable,
            "covers_1400_bar":covers_1400_bar(item["m15"],generated_at),
            "errors":item["errors"],
        }
    usable_count=sum(1 for x in symbols.values() if x["usable_for_analysis"])
    covers_1400_count=sum(1 for x in symbols.values() if x["covers_1400_bar"])
    return {
        "generated_at":result["generated_at"],
        "source_file":OUT.as_posix(),
        "total_symbols":len(symbols),
        "usable_symbols":usable_count,
        "all_usable":usable_count==len(symbols),
        "symbols_covering_1400_bar":covers_1400_count,
        "all_cover_1400_bar":covers_1400_count==len(symbols),
        "note":"14:01后运行时，covers_1400_bar用于检查数据源是否已包含当日14:00收盘的15分钟K线；休市标的可能为false。",
        "symbols":symbols,
    }


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
        item["usable_for_analysis"] = len(item["daily"]) >= 60 and len(item["m15"]) >= 80 and not item["errors"]
        result["symbols"][s["key"]]=item

    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    summary=build_summary(result,now)
    SUMMARY_OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

    print(f"wrote {OUT} and {SUMMARY_OUT}; symbols={len(result['symbols'])}")
    for k,v in summary["symbols"].items():
        print(k,v["daily_count"],v["m15_count"],v["latest_m15_time"],v["usable_for_analysis"],v["covers_1400_bar"],v["errors"])


if __name__=="__main__":main()
