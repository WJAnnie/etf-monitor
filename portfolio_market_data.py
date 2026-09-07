from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CN_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 8
RETRIES = 3
MAX_WORKERS = 4
OUT = Path("data/latest_market_data.json")
SUMMARY_OUT = Path("data/latest_market_summary.json")
SYMBOL_DIR = Path("data/market")

# 技术分析代理标的。场外联接基金不伪造盘中K线，使用其跟踪指数/目标ETF代理。
# tx_symbol 用于腾讯财经行情接口；secid 用于东方财富兜底。
SYMBOLS = [
    {"key":"csi500","name":"中证500","secid":"1.000905","tx_symbol":"sh000905","market":"CN","proxy_for":"中证500核心仓"},
    {"key":"hk_stock_connect_tech","name":"中证港股通科技指数","secid":"1.931573","tx_symbol":"sh931573","market":"HK_INDEX","proxy_for":"016496"},
    {"key":"hk_stock_connect_tech_etf","name":"港股通科技ETF","secid":"1.513980","tx_symbol":"sh513980","market":"CN","proxy_for":"016496目标ETF"},
    {"key":"hang_seng_tech","name":"恒生科技指数","secid":"100.HSTECH","tx_symbol":"hkHSTECH","market":"HK_INDEX","proxy_for":"013172"},
    {"key":"bse50","name":"北证50","secid":"0.899050","tx_symbol":"bj899050","market":"CN","proxy_for":"北证50"},
    {"key":"china_film","name":"中国电影","secid":"1.600977","tx_symbol":"sh600977","market":"CN","proxy_for":"600977"},
    {"key":"battery_etf","name":"电池ETF","secid":"1.561160","tx_symbol":"sh561160","market":"CN","proxy_for":"561160"},
    {"key":"medical_device","name":"中证全指医疗器械指数","secid":"1.931484","tx_symbol":"sh931484","market":"CN_INDEX","proxy_for":"017633"},
    {"key":"dividend_lowvol","name":"红利低波ETF","secid":"1.512890","tx_symbol":"sh512890","market":"CN","proxy_for":"红利组合"},
]


class MarketDataError(RuntimeError):
    pass


def request_with_retry(url: str, *, params: dict | None = None, referer: str | None = None) -> requests.Response:
    headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept":"*/*",
        "Connection":"close",
    }
    if referer:
        headers["Referer"]=referer
    last=None
    for i in range(RETRIES):
        try:
            r=requests.get(url,params=params,headers=headers,timeout=TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as e:
            last=e
            if i < RETRIES-1:
                time.sleep((0.8*(i+1))+random.uniform(0.1,0.45))
    raise MarketDataError(str(last))


def parse_rows(rows: list, source: str) -> list[dict]:
    out=[]
    for row in rows:
        if isinstance(row,str):
            a=row.split(",")
        elif isinstance(row,list):
            a=row
        else:
            continue
        if len(a)<6:
            continue
        try:
            amount=float(a[6]) if len(a)>=7 and str(a[6]).strip() not in ("", "-") else 0.0
            out.append({
                "time":str(a[0]),
                "open":float(a[1]),
                "close":float(a[2]),
                "high":float(a[3]),
                "low":float(a[4]),
                "volume":float(a[5]),
                "amount":amount,
            })
        except (TypeError,ValueError):
            continue
    if not out:
        raise MarketDataError(f"{source} returned empty/invalid kline")
    return out


def tencent_kline(tx_symbol: str, klt: int, limit: int) -> list[dict]:
    if klt==101:
        urls=[
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
        ]
        errors=[]
        for url in urls:
            try:
                r=request_with_retry(
                    url,
                    params={"param":f"{tx_symbol},day,,,{limit},qfq"},
                    referer="https://gu.qq.com/",
                )
                payload=r.json()
                stock=(payload.get("data") or {}).get(tx_symbol) or {}
                rows=stock.get("qfqday") or stock.get("day") or []
                return parse_rows(rows,"Tencent daily")
            except Exception as e:
                errors.append(f"{url}: {e}")
        raise MarketDataError(" | ".join(errors))

    if klt==15:
        urls=[
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            "https://web.ifzq.gtimg.cn/appstock/app/kline/mkline",
        ]
        errors=[]
        for url in urls:
            try:
                r=request_with_retry(
                    url,
                    params={"param":f"{tx_symbol},m15,,{limit}"},
                    referer="https://gu.qq.com/",
                )
                payload=r.json()
                stock=(payload.get("data") or {}).get(tx_symbol) or {}
                rows=stock.get("m15") or []
                return parse_rows(rows,"Tencent m15")
            except Exception as e:
                errors.append(f"{url}: {e}")
        raise MarketDataError(" | ".join(errors))

    raise ValueError(f"unsupported klt={klt}")


def eastmoney_kline(secid: str, klt: int, limit: int) -> list[dict]:
    r=request_with_retry(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid":secid,
            "fields1":"f1,f2,f3,f4,f5,f6",
            "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt":str(klt),"fqt":"1","beg":"0","end":"20500101","lmt":str(limit),
        },
        referer="https://quote.eastmoney.com/",
    )
    payload=r.json()
    rows=((payload.get("data") or {}).get("klines") or [])
    return parse_rows(rows,"EastMoney")


def kline_with_fallback(symbol: dict, klt: int, limit: int) -> tuple[list[dict],str,list[str]]:
    warnings=[]
    try:
        rows=tencent_kline(symbol["tx_symbol"],klt,limit)
        return rows,"tencent",warnings
    except Exception as e:
        warnings.append(f"tencent klt={klt}: {e}")

    try:
        rows=eastmoney_kline(symbol["secid"],klt,limit)
        return rows,"eastmoney",warnings
    except Exception as e:
        warnings.append(f"eastmoney klt={klt}: {e}")

    raise MarketDataError(" ; ".join(warnings))


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
        delta=c[i]-c[i-1]; gains.append(max(delta,0)); losses.append(max(-delta,0))
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


def load_previous_symbols() -> dict:
    if not OUT.exists():
        return {}
    try:
        payload=json.loads(OUT.read_text(encoding="utf-8"))
        symbols=payload.get("symbols") or {}
        return symbols if isinstance(symbols,dict) else {}
    except Exception as e:
        print(f"[WARN] cannot load previous market data: {e}")
        return {}


def collect_symbol(args: tuple[dict,dict]) -> dict:
    s,previous=args
    item={**s,"daily":[],"m15":[],"sources":{},"warnings":[],"errors":[]}

    for field,klt,limit in (("daily",101,180),("m15",15,320)):
        try:
            rows,source,warnings=kline_with_fallback(s,klt,limit)
            item[field]=rows
            item["sources"][field]=source
            item["warnings"].extend(warnings)
        except Exception as e:
            cached=previous.get(field) or []
            if cached:
                item[field]=cached
                item["sources"][field]="cache"
                item["errors"].append(f"{field}: live refresh failed; using cache: {e}")
            else:
                item["sources"][field]="none"
                item["errors"].append(f"{field}: {e}")

    item["indicators"]={"daily":indicators(item["daily"]),"m15":indicators(item["m15"])}
    enough=len(item["daily"])>=60 and len(item["m15"])>=80
    using_cache=any(v=="cache" for v in item["sources"].values())
    item["usable_for_analysis"]=enough
    item["fresh_for_analysis"]=enough and not using_cache and not item["errors"]
    return item


def build_summary(result: dict, generated_at: datetime) -> dict:
    symbols={}
    for key,item in result["symbols"].items():
        daily_count=len(item["daily"])
        m15_count=len(item["m15"])
        daily_ok=daily_count>=60
        m15_ok=m15_count>=80
        usable=daily_ok and m15_ok
        using_cache=any(v=="cache" for v in (item.get("sources") or {}).values())
        fresh=usable and not using_cache and not item["errors"]
        symbols[key]={
            "name":item["name"],
            "secid":item["secid"],
            "tx_symbol":item["tx_symbol"],
            "market":item["market"],
            "proxy_for":item["proxy_for"],
            "data_file":f"{SYMBOL_DIR.as_posix()}/{key}.json",
            "sources":item.get("sources") or {},
            "daily_count":daily_count,
            "m15_count":m15_count,
            "latest_daily_time":latest_time(item["daily"]),
            "latest_m15_time":latest_time(item["m15"]),
            "daily_history_ok":daily_ok,
            "m15_history_ok":m15_ok,
            "using_cache":using_cache,
            "usable_for_analysis":usable,
            "fresh_for_analysis":fresh,
            "covers_1400_bar":covers_1400_bar(item["m15"],generated_at),
            "warnings":item.get("warnings") or [],
            "errors":item["errors"],
        }
    usable_count=sum(1 for x in symbols.values() if x["usable_for_analysis"])
    fresh_count=sum(1 for x in symbols.values() if x["fresh_for_analysis"])
    covers_1400_count=sum(1 for x in symbols.values() if x["covers_1400_bar"])
    return {
        "generated_at":result["generated_at"],
        "source_file":OUT.as_posix(),
        "total_symbols":len(symbols),
        "usable_symbols":usable_count,
        "fresh_symbols":fresh_count,
        "all_usable":usable_count==len(symbols),
        "all_fresh":fresh_count==len(symbols),
        "symbols_covering_1400_bar":covers_1400_count,
        "all_cover_1400_bar":covers_1400_count==len(symbols),
        "note":"优先腾讯财经，东方财富兜底；两者都失败时保留上一次可用K线并标记using_cache。14:01后covers_1400_bar用于检查是否包含当日14:00收盘的15分钟K线。",
        "symbols":symbols,
    }


def main():
    now=datetime.now(CN_TZ)
    previous_symbols=load_previous_symbols()
    result={"generated_at":now.isoformat(),"note":"场外基金采用跟踪指数/ETF代理；缠论买卖点不由脚本机械确认。","symbols":{}}

    inputs=[(s,previous_symbols.get(s["key"]) or {}) for s in SYMBOLS]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        items=list(pool.map(collect_symbol,inputs))
    for item in items:
        result["symbols"][item["key"]]=item

    OUT.parent.mkdir(parents=True,exist_ok=True)
    SYMBOL_DIR.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")

    for key,item in result["symbols"].items():
        shard={"generated_at":result["generated_at"],"note":result["note"],"symbol":item}
        (SYMBOL_DIR/f"{key}.json").write_text(json.dumps(shard,ensure_ascii=False,indent=2),encoding="utf-8")

    summary=build_summary(result,now)
    SUMMARY_OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

    print(f"wrote {OUT}, {SUMMARY_OUT}, and {len(result['symbols'])} symbol shards")
    for k,v in summary["symbols"].items():
        print(k,v["sources"],v["daily_count"],v["m15_count"],v["latest_m15_time"],v["usable_for_analysis"],v["fresh_for_analysis"],v["covers_1400_bar"],v["errors"])


if __name__=="__main__":main()
