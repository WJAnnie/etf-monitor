from __future__ import annotations

import json
import random
import subprocess
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

# 场外联接基金不伪造盘中K线，使用其跟踪指数/目标ETF作为技术分析代理。
# yahoo_symbol 仅作后备；部分指数没有 Yahoo 代码时使用其目标ETF，并在 proxy_note 中明确标记。
SYMBOLS = [
    {"key":"csi500","name":"中证500","secid":"1.000905","tx_symbol":"sh000905","yahoo_symbol":"000905.SS","market":"CN","proxy_for":"中证500核心仓","proxy_note":"指数本身"},
    {"key":"hk_stock_connect_tech","name":"中证港股通科技指数","secid":"1.931573","tx_symbol":"sh931573","yahoo_symbol":"513980.SS","market":"CN_INDEX","proxy_for":"016496","proxy_note":"主取931573；Yahoo后备使用目标ETF 513980"},
    {"key":"hk_stock_connect_tech_etf","name":"港股通科技ETF","secid":"1.513980","tx_symbol":"sh513980","yahoo_symbol":"513980.SS","market":"CN","proxy_for":"016496目标ETF","proxy_note":"目标ETF本身"},
    {"key":"hang_seng_tech","name":"恒生科技指数","secid":"100.HSTECH","tx_symbol":"hkHSTECH","yahoo_symbol":"HSTECH.HK","market":"HK_INDEX","proxy_for":"013172","proxy_note":"恒生科技指数"},
    {"key":"bse50","name":"北证50","secid":"0.899050","tx_symbol":"bj899050","yahoo_symbol":None,"market":"CN_INDEX","proxy_for":"北证50","proxy_note":"指数本身；无可靠Yahoo后备"},
    {"key":"china_film","name":"中国电影","secid":"1.600977","tx_symbol":"sh600977","yahoo_symbol":"600977.SS","market":"CN","proxy_for":"600977","proxy_note":"个股本身"},
    {"key":"battery_etf","name":"电池ETF","secid":"1.561160","tx_symbol":"sh561160","yahoo_symbol":"561160.SS","market":"CN","proxy_for":"561160","proxy_note":"ETF本身"},
    {"key":"medical_device","name":"中证全指医疗器械指数","secid":"1.931484","tx_symbol":"sh931484","yahoo_symbol":"159797.SZ","market":"CN_INDEX","proxy_for":"017633","proxy_note":"主取931484；Yahoo后备使用目标ETF 159797"},
    {"key":"dividend_lowvol","name":"红利低波ETF","secid":"1.512890","tx_symbol":"sh512890","yahoo_symbol":"512890.SS","market":"CN","proxy_for":"红利组合","proxy_note":"红利组合技术代理"},
]


class MarketDataError(RuntimeError):
    pass


def request_with_retry(url: str, *, params: dict | None = None, referer: str | None = None) -> requests.Response:
    headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept":"application/json,text/plain,*/*",
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


def decode_json_or_jsonp(text: str):
    text=text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    if "=(" in text and text.endswith(");"):
        return json.loads(text.split("=(",1)[1][:-2])
    if "=" in text:
        body=text.split("=",1)[1].strip().rstrip(";")
        if body.startswith("(") and body.endswith(")"):
            body=body[1:-1]
        return json.loads(body)
    raise MarketDataError(f"non-JSON response: {text[:160]!r}")


def parse_array_rows(rows: list, source: str) -> list[dict]:
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
            amount=float(a[6]) if len(a)>=7 and str(a[6]).strip() not in ("", "-", "None") else 0.0
            out.append({
                "time":str(a[0]),"open":float(a[1]),"close":float(a[2]),"high":float(a[3]),
                "low":float(a[4]),"volume":float(a[5]),"amount":amount,
            })
        except (TypeError,ValueError):
            continue
    if not out:
        raise MarketDataError(f"{source} returned empty/invalid kline")
    return out


def parse_object_rows(rows: list, source: str) -> list[dict]:
    out=[]
    for a in rows:
        if not isinstance(a,dict):
            continue
        try:
            t=a.get("day") or a.get("date") or a.get("time")
            if not t:
                continue
            out.append({
                "time":str(t),"open":float(a["open"]),"close":float(a["close"]),"high":float(a["high"]),
                "low":float(a["low"]),"volume":float(a.get("volume") or 0),"amount":float(a.get("amount") or 0),
            })
        except (KeyError,TypeError,ValueError):
            continue
    if not out:
        raise MarketDataError(f"{source} returned empty/invalid kline")
    return out


def tencent_kline(symbol: dict, klt: int, limit: int) -> list[dict]:
    code=symbol["tx_symbol"]
    if klt==101:
        urls=[
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
        ]
        errors=[]
        for url in urls:
            try:
                r=request_with_retry(url,params={"param":f"{code},day,,,{limit},qfq"},referer="https://gu.qq.com/")
                payload=decode_json_or_jsonp(r.text)
                stock=(payload.get("data") or {}).get(code) or {}
                rows=stock.get("qfqday") or stock.get("day") or []
                return parse_array_rows(rows,"Tencent daily")
            except Exception as e:
                errors.append(f"{url}: {e}")
        raise MarketDataError(" | ".join(errors))

    if klt==15:
        urls=[
            "http://ifzq.gtimg.cn/appstock/app/kline/mkline",
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
        ]
        errors=[]
        for url in urls:
            try:
                r=request_with_retry(
                    url,
                    params={"param":f"{code},m15,,{limit}","_var":"m15_today","r":f"{random.random():.16f}"},
                    referer="https://gu.qq.com/",
                )
                payload=decode_json_or_jsonp(r.text)
                stock=(payload.get("data") or {}).get(code) or {}
                rows=stock.get("m15") or []
                if not rows:
                    raise MarketDataError(f"Tencent m15 empty; stock_keys={list(stock.keys())[:8]}")
                return parse_array_rows(rows,"Tencent m15")
            except Exception as e:
                errors.append(f"{url}: {e}")
        raise MarketDataError(" | ".join(errors))

    raise ValueError(f"unsupported klt={klt}")


def sina_kline(symbol: dict, klt: int, limit: int) -> list[dict]:
    code=symbol["tx_symbol"]
    if not code.startswith(("sh","sz","bj")):
        raise MarketDataError(f"Sina CN endpoint unsupported symbol: {code}")
    scale="240" if klt==101 else "15"
    r=request_with_retry(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData",
        params={"symbol":code,"scale":scale,"ma":"no","datalen":str(min(limit,1970))},
        referer="https://finance.sina.com.cn/",
    )
    payload=decode_json_or_jsonp(r.text)
    if not isinstance(payload,list):
        raise MarketDataError(f"Sina unexpected payload type={type(payload).__name__}")
    return parse_object_rows(payload,f"Sina scale={scale}")


def yahoo_kline(symbol: dict, klt: int, limit: int) -> list[dict]:
    code=symbol.get("yahoo_symbol")
    if not code:
        raise MarketDataError("Yahoo fallback not configured")
    interval="1d" if klt==101 else "15m"
    range_="1y" if klt==101 else "60d"
    r=request_with_retry(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{code}",
        params={"interval":interval,"range":range_,"includePrePost":"false","events":"div,splits"},
        referer="https://finance.yahoo.com/",
    )
    payload=r.json()
    chart=payload.get("chart") or {}
    if chart.get("error"):
        raise MarketDataError(f"Yahoo chart error: {chart['error']}")
    result=(chart.get("result") or [None])[0]
    if not result:
        raise MarketDataError("Yahoo chart missing result")
    timestamps=result.get("timestamp") or []
    quotes=(((result.get("indicators") or {}).get("quote") or [{}])[0])
    opens=quotes.get("open") or []
    closes=quotes.get("close") or []
    highs=quotes.get("high") or []
    lows=quotes.get("low") or []
    vols=quotes.get("volume") or []
    out=[]
    for i,ts in enumerate(timestamps):
        try:
            o,c,h,l=opens[i],closes[i],highs[i],lows[i]
            if None in (o,c,h,l):
                continue
            dt=datetime.fromtimestamp(int(ts),CN_TZ)
            t=dt.strftime("%Y-%m-%d") if klt==101 else dt.strftime("%Y-%m-%d %H:%M")
            out.append({"time":t,"open":float(o),"close":float(c),"high":float(h),"low":float(l),"volume":float(vols[i] or 0),"amount":0.0})
        except (IndexError,TypeError,ValueError,OSError):
            continue
    if not out:
        raise MarketDataError("Yahoo returned empty/invalid kline")
    return out[-limit:]


def eastmoney_kline(symbol: dict, klt: int, limit: int) -> list[dict]:
    r=request_with_retry(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid":symbol["secid"],"fields1":"f1,f2,f3,f4,f5,f6",
            "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt":str(klt),"fqt":"1","beg":"0","end":"20500101","lmt":str(limit),
        },
        referer="https://quote.eastmoney.com/",
    )
    payload=r.json()
    rows=((payload.get("data") or {}).get("klines") or [])
    return parse_array_rows(rows,"EastMoney")


def kline_with_fallback(symbol: dict, klt: int, limit: int) -> tuple[list[dict],str,list[str]]:
    warnings=[]
    providers=(
        ("tencent",tencent_kline),
        ("sina",sina_kline),
        ("yahoo",yahoo_kline),
        ("eastmoney",eastmoney_kline),
    )
    for name,fn in providers:
        try:
            rows=fn(symbol,klt,limit)
            return rows,name,warnings
        except Exception as e:
            warnings.append(f"{name} klt={klt}: {e}")
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


def read_json_file(path: Path) -> dict:
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload,dict) else {}
    except Exception:
        return {}


def git_historical_payloads(max_commits: int=30):
    try:
        commits=subprocess.check_output(
            ["git","log",f"-n{max_commits}","--format=%H","--","data/latest_market_data.json"],
            text=True,stderr=subprocess.DEVNULL,
        ).splitlines()
    except Exception as e:
        print(f"[WARN] git history unavailable: {e}")
        return
    for sha in commits:
        try:
            raw=subprocess.check_output(["git","show",f"{sha}:data/latest_market_data.json"],text=True,stderr=subprocess.DEVNULL)
            payload=json.loads(raw)
            if isinstance(payload,dict):
                yield sha,payload
        except Exception:
            continue


def load_previous_symbols() -> dict:
    # 先读当前文件；若某个级别已被一次上游故障写成空数组，则从 Git 历史中回捞最近一次足量K线。
    current=(read_json_file(OUT).get("symbols") or {}) if OUT.exists() else {}
    best={k:dict(v) for k,v in current.items() if isinstance(v,dict)}
    thresholds={"daily":60,"m15":80}
    missing={(s["key"],field) for s in SYMBOLS for field,n in thresholds.items() if len((best.get(s["key"]) or {}).get(field) or [])<n}
    if not missing:
        return best

    for sha,payload in git_historical_payloads():
        hist=payload.get("symbols") or {}
        for key,field in list(missing):
            candidate=(hist.get(key) or {}).get(field) or []
            if len(candidate)>=thresholds[field]:
                best.setdefault(key,{})[field]=candidate
                best[key].setdefault("_recovered_from",{})[field]=sha
                missing.discard((key,field))
                print(f"[CACHE] recovered {key}.{field} from {sha[:8]} rows={len(candidate)}")
        if not missing:
            break
    return best


def collect_symbol(args: tuple[dict,dict]) -> dict:
    s,previous=args
    item={**s,"daily":[],"m15":[],"sources":{},"warnings":[],"errors":[],"cache_origin":previous.get("_recovered_from") or {}}
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
        daily_count=len(item["daily"]); m15_count=len(item["m15"])
        daily_ok=daily_count>=60; m15_ok=m15_count>=80
        usable=daily_ok and m15_ok
        using_cache=any(v=="cache" for v in (item.get("sources") or {}).values())
        fresh=usable and not using_cache and not item["errors"]
        symbols[key]={
            "name":item["name"],"secid":item["secid"],"tx_symbol":item["tx_symbol"],"market":item["market"],
            "proxy_for":item["proxy_for"],"proxy_note":item["proxy_note"],"data_file":f"{SYMBOL_DIR.as_posix()}/{key}.json",
            "sources":item.get("sources") or {},"cache_origin":item.get("cache_origin") or {},
            "daily_count":daily_count,"m15_count":m15_count,"latest_daily_time":latest_time(item["daily"]),"latest_m15_time":latest_time(item["m15"]),
            "daily_history_ok":daily_ok,"m15_history_ok":m15_ok,"using_cache":using_cache,"usable_for_analysis":usable,"fresh_for_analysis":fresh,
            "covers_1400_bar":covers_1400_bar(item["m15"],generated_at),"warnings":item.get("warnings") or [],"errors":item["errors"],
        }
    usable_count=sum(1 for x in symbols.values() if x["usable_for_analysis"])
    fresh_count=sum(1 for x in symbols.values() if x["fresh_for_analysis"])
    covers_1400_count=sum(1 for x in symbols.values() if x["covers_1400_bar"])
    return {
        "generated_at":result["generated_at"],"source_file":OUT.as_posix(),"total_symbols":len(symbols),
        "usable_symbols":usable_count,"fresh_symbols":fresh_count,"all_usable":usable_count==len(symbols),"all_fresh":fresh_count==len(symbols),
        "symbols_covering_1400_bar":covers_1400_count,"all_cover_1400_bar":covers_1400_count==len(symbols),
        "note":"数据源顺序：腾讯→新浪→Yahoo→东方财富；实时源全部失败时从当前文件或Git历史回捞最近可用K线并标记cache。14:01后covers_1400_bar检查当日14:00的15分钟K线。",
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

    OUT.parent.mkdir(parents=True,exist_ok=True); SYMBOL_DIR.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    for key,item in result["symbols"].items():
        shard={"generated_at":result["generated_at"],"note":result["note"],"symbol":item}
        (SYMBOL_DIR/f"{key}.json").write_text(json.dumps(shard,ensure_ascii=False,indent=2),encoding="utf-8")
    summary=build_summary(result,now)
    SUMMARY_OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

    print(f"wrote {OUT}, {SUMMARY_OUT}, and {len(result['symbols'])} symbol shards")
    print(f"quality: usable={summary['usable_symbols']}/{summary['total_symbols']} fresh={summary['fresh_symbols']}/{summary['total_symbols']} covers_1400={summary['symbols_covering_1400_bar']}/{summary['total_symbols']}")
    for k,v in summary["symbols"].items():
        print(k,v["sources"],v["daily_count"],v["m15_count"],v["latest_m15_time"],v["usable_for_analysis"],v["fresh_for_analysis"],v["covers_1400_bar"],v["errors"])


if __name__=="__main__":main()
