from __future__ import annotations

import json
import random
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from scripts.cn_market_calendar import calendar_status

CN_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 8
RETRIES = 3
MAX_WORKERS = 4
OUT = Path("data/latest_market_data.json")
SUMMARY_OUT = Path("data/latest_market_summary.json")
SYMBOL_DIR = Path("data/market")
MIN_ROWS = {101: 60, 15: 80}

# 场外联接基金不伪造盘中K线，使用其跟踪指数/目标ETF作为技术分析代理。
# yahoo_symbol_is_proxy=True 表示 Yahoo 代码只是后备代理，优先尝试能返回原指数的行情源。
SYMBOLS = [
    {"key":"csi500","name":"中证500","secid":"1.000905","tx_symbol":"sh000905","yahoo_symbol":"000905.SS","yahoo_symbol_is_proxy":False,"market":"CN","proxy_for":"中证500核心仓","proxy_note":"指数本身"},
    {"key":"hk_stock_connect_tech","name":"中证港股通科技指数","secid":"1.931573","tx_symbol":"sh931573","yahoo_symbol":"513980.SS","yahoo_symbol_is_proxy":True,"market":"CN_INDEX","proxy_for":"016496","proxy_note":"主取931573；Yahoo后备使用目标ETF 513980"},
    {"key":"hk_stock_connect_tech_etf","name":"港股通科技ETF","secid":"1.513980","tx_symbol":"sh513980","yahoo_symbol":"513980.SS","yahoo_symbol_is_proxy":False,"market":"CN","proxy_for":"016496目标ETF","proxy_note":"目标ETF本身"},
    {"key":"hang_seng_tech","name":"恒生科技指数","secid":"100.HSTECH","tx_symbol":"hkHSTECH","yahoo_symbol":"HSTECH.HK","yahoo_symbol_is_proxy":False,"market":"HK_INDEX","proxy_for":"013172","proxy_note":"恒生科技指数"},
    {"key":"bse50","name":"北证50","secid":"0.899050","tx_symbol":"bj899050","yahoo_symbol":None,"yahoo_symbol_is_proxy":False,"provider_order":["eastmoney","sina","tencent","yahoo"],"market":"CN_INDEX","proxy_for":"北证50","proxy_note":"指数本身；优先东方财富，避免腾讯仅返回单根日K"},
    {"key":"china_film","name":"中国电影","secid":"1.600977","tx_symbol":"sh600977","yahoo_symbol":"600977.SS","yahoo_symbol_is_proxy":False,"market":"CN","proxy_for":"600977","proxy_note":"个股本身"},
    {"key":"battery_etf","name":"电池ETF","secid":"1.561160","tx_symbol":"sh561160","yahoo_symbol":"561160.SS","yahoo_symbol_is_proxy":False,"market":"CN","proxy_for":"561160","proxy_note":"ETF本身"},
    {"key":"medical_device","name":"中证全指医疗器械指数","secid":"1.931484","tx_symbol":"sh931484","yahoo_symbol":"159797.SZ","yahoo_symbol_is_proxy":True,"market":"CN_INDEX","proxy_for":"017633","proxy_note":"主取931484；Yahoo后备使用目标ETF 159797"},
    {"key":"dividend_lowvol","name":"红利低波ETF","secid":"1.512890","tx_symbol":"sh512890","yahoo_symbol":"512890.SS","yahoo_symbol_is_proxy":False,"market":"CN","proxy_for":"红利组合","proxy_note":"红利组合技术代理"},
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
        a=row.split(",") if isinstance(row,str) else row if isinstance(row,list) else None
        if not a or len(a)<6:
            continue
        try:
            amount=float(a[6]) if len(a)>=7 and str(a[6]).strip() not in ("", "-", "None") else 0.0
            out.append({"time":str(a[0]),"open":float(a[1]),"close":float(a[2]),"high":float(a[3]),"low":float(a[4]),"volume":float(a[5]),"amount":amount})
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
            out.append({"time":str(t),"open":float(a["open"]),"close":float(a["close"]),"high":float(a["high"]),"low":float(a["low"]),"volume":float(a.get("volume") or 0),"amount":float(a.get("amount") or 0)})
        except (KeyError,TypeError,ValueError):
            continue
    if not out:
        raise MarketDataError(f"{source} returned empty/invalid kline")
    return out


def parse_bar_time(value: str | None) -> datetime | None:
    if not value:
        return None
    s=str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y-%m-%d"):
        try:
            return datetime.strptime(s,fmt).replace(tzinfo=CN_TZ)
        except ValueError:
            pass
    try:
        dt=datetime.fromisoformat(s)
        return dt.replace(tzinfo=CN_TZ) if dt.tzinfo is None else dt.astimezone(CN_TZ)
    except ValueError:
        return None


def yahoo_15m_start_is_session_bar(dt: datetime, market: str) -> bool:
    t=dt.time()
    if dt.minute % 15 != 0 or dt.second != 0:
        return False
    if market.startswith("CN"):
        return dt_time(9,30)<=t<=dt_time(11,15) or dt_time(13,0)<=t<=dt_time(14,45)
    if market.startswith("HK"):
        return dt_time(9,30)<=t<=dt_time(11,45) or dt_time(13,0)<=t<=dt_time(15,45)
    return True


def tencent_kline(symbol: dict, klt: int, limit: int) -> list[dict]:
    code=symbol["tx_symbol"]
    if klt==101:
        urls=["https://web.ifzq.gtimg.cn/appstock/app/fqkline/get","https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"]
        errors=[]
        for url in urls:
            try:
                r=request_with_retry(url,params={"param":f"{code},day,,,{limit},qfq"},referer="https://gu.qq.com/")
                payload=decode_json_or_jsonp(r.text)
                stock=(payload.get("data") or {}).get(code) or {}
                return parse_array_rows(stock.get("qfqday") or stock.get("day") or [],"Tencent daily")
            except Exception as e:
                errors.append(f"{url}: {e}")
        raise MarketDataError(" | ".join(errors))
    if klt==15:
        urls=["http://ifzq.gtimg.cn/appstock/app/kline/mkline","https://ifzq.gtimg.cn/appstock/app/kline/mkline"]
        errors=[]
        for url in urls:
            try:
                r=request_with_retry(url,params={"param":f"{code},m15,,{limit}","_var":"m15_today","r":f"{random.random():.16f}"},referer="https://gu.qq.com/")
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
    r=request_with_retry("https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData",params={"symbol":code,"scale":scale,"ma":"no","datalen":str(min(limit,1970))},referer="https://finance.sina.com.cn/")
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
    r=request_with_retry(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}",params={"interval":interval,"range":range_,"includePrePost":"false","events":"div,splits"},referer="https://finance.yahoo.com/")
    payload=r.json(); chart=payload.get("chart") or {}
    if chart.get("error"):
        raise MarketDataError(f"Yahoo chart error: {chart['error']}")
    result=(chart.get("result") or [None])[0]
    if not result:
        raise MarketDataError("Yahoo chart missing result")
    timestamps=result.get("timestamp") or []
    quotes=(((result.get("indicators") or {}).get("quote") or [{}])[0])
    opens=quotes.get("open") or []; closes=quotes.get("close") or []; highs=quotes.get("high") or []; lows=quotes.get("low") or []; vols=quotes.get("volume") or []
    out=[]; now=datetime.now(CN_TZ)
    for i,ts in enumerate(timestamps):
        try:
            o,c,h,l=opens[i],closes[i],highs[i],lows[i]
            if None in (o,c,h,l):
                continue
            dt=datetime.fromtimestamp(int(ts),CN_TZ)
            if klt==15:
                # Yahoo 的15分钟时间戳按区间起点理解；去掉非15分钟对齐的实时快照和尚未收完的K线。
                if not yahoo_15m_start_is_session_bar(dt,symbol["market"]):
                    continue
                if dt+timedelta(minutes=15)>now:
                    continue
            t=dt.strftime("%Y-%m-%d") if klt==101 else dt.strftime("%Y-%m-%d %H:%M")
            out.append({"time":t,"open":float(o),"close":float(c),"high":float(h),"low":float(l),"volume":float(vols[i] or 0),"amount":0.0})
        except (IndexError,TypeError,ValueError,OSError):
            continue
    if not out:
        raise MarketDataError("Yahoo returned empty/invalid kline")
    return out[-limit:]


def eastmoney_kline(symbol: dict, klt: int, limit: int) -> list[dict]:
    r=request_with_retry("https://push2his.eastmoney.com/api/qt/stock/kline/get",params={"secid":symbol["secid"],"fields1":"f1,f2,f3,f4,f5,f6","fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61","klt":str(klt),"fqt":"1","beg":"0","end":"20500101","lmt":str(limit)},referer="https://quote.eastmoney.com/")
    payload=r.json(); rows=((payload.get("data") or {}).get("klines") or [])
    return parse_array_rows(rows,"EastMoney")


PROVIDERS={"tencent":tencent_kline,"sina":sina_kline,"yahoo":yahoo_kline,"eastmoney":eastmoney_kline}


def kline_with_fallback(symbol: dict, klt: int, limit: int) -> tuple[list[dict],str,list[str]]:
    warnings=[]
    order=symbol.get("provider_order")
    if not order:
        order=["tencent","sina","eastmoney","yahoo"] if symbol.get("yahoo_symbol_is_proxy") else ["tencent","sina","yahoo","eastmoney"]
    minimum=MIN_ROWS[klt]
    for name in order:
        fn=PROVIDERS[name]
        try:
            rows=fn(symbol,klt,limit)
            if len(rows)<minimum:
                raise MarketDataError(f"only {len(rows)} rows, need >= {minimum}")
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
    return {"macd":{"diff":diff[-1],"dea":dea[-1],"hist":macd[-1]},"boll":{"mid":ma20,"upper":ma20+2*sd,"lower":ma20-2*sd},"rsi14":rsi,"kdj":{"k":k,"d":d,"j":j},"recent_high_5":max(h[-5:]),"recent_low_5":min(l[-5:])}


def latest_time(rows: list[dict]) -> str | None:
    return rows[-1]["time"] if rows else None


def latest_completed_m15_end(rows: list[dict], source: str) -> datetime | None:
    if not rows:
        return None
    for row in reversed(rows):
        dt=parse_bar_time(row.get("time"))
        if not dt:
            continue
        return dt+timedelta(minutes=15) if source=="yahoo" else dt
    return None


def has_today(rows: list[dict], generated_at: datetime) -> bool:
    dt=parse_bar_time(latest_time(rows))
    return bool(dt and dt.date()==generated_at.date())


def covers_1400_bar(rows: list[dict], source: str, generated_at: datetime) -> bool:
    end=latest_completed_m15_end(rows,source)
    return bool(end and end.date()==generated_at.date() and end.time()>=dt_time(14,0))


def read_json_file(path: Path) -> dict:
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload,dict) else {}
    except Exception:
        return {}


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(path)


def git_historical_payloads(max_commits: int=30):
    try:
        commits=subprocess.check_output(["git","log",f"-n{max_commits}","--format=%H","--","data/latest_market_data.json"],text=True,stderr=subprocess.DEVNULL).splitlines()
    except Exception as e:
        print(f"[WARN] git history unavailable: {e}")
        return
    for sha in commits:
        try:
            raw=subprocess.check_output(["git","show",f"{sha}:data/latest_market_data.json"],text=True,stderr=subprocess.DEVNULL)
            payload=json.loads(raw)
            if isinstance(payload,dict):yield sha,payload
        except Exception:continue


def load_previous_symbols() -> dict:
    current=(read_json_file(OUT).get("symbols") or {}) if OUT.exists() else {}
    best={k:dict(v) for k,v in current.items() if isinstance(v,dict)}
    thresholds={"daily":60,"m15":80}
    missing={(s["key"],field) for s in SYMBOLS for field,n in thresholds.items() if len((best.get(s["key"]) or {}).get(field) or [])<n}
    if not missing:return best
    for sha,payload in git_historical_payloads():
        hist=payload.get("symbols") or {}
        for key,field in list(missing):
            candidate=(hist.get(key) or {}).get(field) or []
            if len(candidate)>=thresholds[field]:
                best.setdefault(key,{})[field]=candidate
                best[key].setdefault("_recovered_from",{})[field]=sha
                missing.discard((key,field)); print(f"[CACHE] recovered {key}.{field} from {sha[:8]} rows={len(candidate)}")
        if not missing:break
    return best


def collect_symbol(args: tuple[dict,dict]) -> dict:
    s,previous=args
    item={**s,"daily":[],"m15":[],"sources":{},"warnings":[],"errors":[],"cache_origin":dict(previous.get("_recovered_from") or {})}
    for field,klt,limit in (("daily",101,180),("m15",15,320)):
        try:
            rows,source,warnings=kline_with_fallback(s,klt,limit)
            item[field]=rows; item["sources"][field]=source; item["warnings"].extend(warnings)
            item["cache_origin"].pop(field,None)
        except Exception as e:
            cached=previous.get(field) or []
            if len(cached)>=MIN_ROWS[klt]:
                item[field]=cached; item["sources"][field]="cache"; item["errors"].append(f"{field}: live refresh failed; using cache: {e}")
            else:
                item["sources"][field]="none"; item["errors"].append(f"{field}: {e}")
    item["indicators"]={"daily":indicators(item["daily"]),"m15":indicators(item["m15"])}
    enough=len(item["daily"])>=60 and len(item["m15"])>=80
    using_cache=any(v=="cache" for v in item["sources"].values())
    today=has_today(item["daily"],datetime.now(CN_TZ)) and has_today(item["m15"],datetime.now(CN_TZ))
    item["usable_for_analysis"]=enough
    item["fresh_for_analysis"]=enough and today and not using_cache and not item["errors"]
    return item


def build_summary(result: dict, generated_at: datetime) -> dict:
    symbols={}
    for key,item in result["symbols"].items():
        daily_count=len(item["daily"]); m15_count=len(item["m15"])
        daily_ok=daily_count>=60; m15_ok=m15_count>=80; usable=daily_ok and m15_ok
        using_cache=any(v=="cache" for v in (item.get("sources") or {}).values())
        daily_today=has_today(item["daily"],generated_at); m15_today=has_today(item["m15"],generated_at)
        fresh=usable and daily_today and m15_today and not using_cache and not item["errors"]
        m15_source=(item.get("sources") or {}).get("m15","none")
        completed_end=latest_completed_m15_end(item["m15"],m15_source)
        proxy_substitution={field:((item.get("sources") or {}).get(field)=="yahoo" and bool(item.get("yahoo_symbol_is_proxy"))) for field in ("daily","m15")}
        cover=covers_1400_bar(item["m15"],m15_source,generated_at)
        symbols[key]={
            "name":item["name"],"secid":item["secid"],"tx_symbol":item["tx_symbol"],"market":item["market"],"proxy_for":item["proxy_for"],"proxy_note":item["proxy_note"],"data_file":f"{SYMBOL_DIR.as_posix()}/{key}.json",
            "sources":item.get("sources") or {},"proxy_substitution":proxy_substitution,"cache_origin":item.get("cache_origin") or {},
            "daily_count":daily_count,"m15_count":m15_count,"latest_daily_time":latest_time(item["daily"]),"latest_m15_time":latest_time(item["m15"]),"latest_completed_m15_end_time":completed_end.isoformat() if completed_end else None,
            "daily_history_ok":daily_ok,"m15_history_ok":m15_ok,"daily_is_today":daily_today,"m15_is_today":m15_today,"using_cache":using_cache,"usable_for_analysis":usable,"fresh_for_analysis":fresh,"covers_1400_bar":cover,
            "warnings":item.get("warnings") or [],"errors":item["errors"],
        }
    usable_count=sum(x["usable_for_analysis"] for x in symbols.values()); fresh_count=sum(x["fresh_for_analysis"] for x in symbols.values()); covers_count=sum(x["covers_1400_bar"] for x in symbols.values()); today_count=sum(x["daily_is_today"] and x["m15_is_today"] for x in symbols.values())
    calendar=calendar_status(generated_at.date())
    expected_trading_day=bool(calendar["expected_trading_day"])
    observed_market_activity=today_count>=max(3,len(symbols)//2)
    ready_count=sum(x["fresh_for_analysis"] and x["covers_1400_bar"] for x in symbols.values())
    return {
        "generated_at":result["generated_at"],"source_file":OUT.as_posix(),"total_symbols":len(symbols),
        "usable_symbols":usable_count,"fresh_symbols":fresh_count,"symbols_with_today_data":today_count,
        "expected_trading_day":expected_trading_day,"market_activity_today":expected_trading_day,
        "observed_market_activity_today":observed_market_activity,"market_calendar_reason":calendar["reason"],
        "all_usable":usable_count==len(symbols),"all_fresh":fresh_count==len(symbols),"symbols_covering_1400_bar":covers_count,"all_cover_1400_bar":covers_count==len(symbols),"ready_for_1400_analysis_symbols":ready_count,"all_ready_for_1400_analysis":ready_count==len(symbols),
        "note":"交易日状态由中国A股交易日历判断；行情新鲜度和1400点完整性单独质量门判定，不因缺少当日数据而推断休市。行情源按标的选择；代理Yahoo代码会在proxy_substitution标明。Yahoo 15分钟按区间起点处理并过滤未收完K线；其他源按区间结束时间处理。实时源全失败时才回捞缓存。",
        "symbols":symbols,
    }


def main():
    now=datetime.now(CN_TZ); previous_symbols=load_previous_symbols()
    result={"generated_at":now.isoformat(),"note":"场外基金采用跟踪指数/ETF代理；15分钟仅保留已收完K线；缠论买卖点不由脚本机械确认。","symbols":{}}
    inputs=[(s,previous_symbols.get(s["key"]) or {}) for s in SYMBOLS]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:items=list(pool.map(collect_symbol,inputs))
    for item in items:result["symbols"][item["key"]]=item

    summary=build_summary(result,now)
    # 本地先完整生成临时文件，再原子替换；GitHub 只有最后 git commit/push 时才看到整套快照。
    atomic_write_json(OUT,result)
    for key,item in result["symbols"].items():atomic_write_json(SYMBOL_DIR/f"{key}.json",{"generated_at":result["generated_at"],"note":result["note"],"symbol":item})
    atomic_write_json(SUMMARY_OUT,summary)

    print(f"wrote {OUT}, {SUMMARY_OUT}, and {len(result['symbols'])} symbol shards")
    print(f"calendar: expected_trading_day={summary['expected_trading_day']} reason={summary['market_calendar_reason']} observed_activity={summary['observed_market_activity_today']}")
    print(f"quality: usable={summary['usable_symbols']}/{summary['total_symbols']} fresh={summary['fresh_symbols']}/{summary['total_symbols']} today={summary['symbols_with_today_data']}/{summary['total_symbols']} covers_1400={summary['symbols_covering_1400_bar']}/{summary['total_symbols']} ready={summary['ready_for_1400_analysis_symbols']}/{summary['total_symbols']}")
    for k,v in summary["symbols"].items():
        print(k,v["sources"],v["daily_count"],v["m15_count"],v["latest_m15_time"],v["latest_completed_m15_end_time"],v["usable_for_analysis"],v["fresh_for_analysis"],v["covers_1400_bar"],v["proxy_substitution"],v["errors"])


if __name__=="__main__":main()
