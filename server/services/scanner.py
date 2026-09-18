# server/services/scanner.py
"""Nobitex scanner plus persistent Global+Local market snapshots."""
from __future__ import annotations
import json, logging, math, threading, time
from collections import defaultdict, deque
from typing import Any, Optional
import pandas as pd
from server.config import settings
from server.database import SessionLocal
from trading.nobitex_client import NobitexClient

logger=logging.getLogger(__name__)
_LOCK=threading.RLock()
_CACHE={"rows":[],"updated_at":0.0,"error":None,"source":None}
_HISTORY=defaultdict(lambda: deque(maxlen=30))

def get_cached():
    with _LOCK:
        rows=list(_CACHE["rows"]); updated=float(_CACHE["updated_at"]); error=_CACHE["error"]; source=_CACHE["source"]
    try:
        from server.models import ScanSnapshot
        db=SessionLocal()
        try:
            s=db.query(ScanSnapshot).order_by(ScanSnapshot.created_at.desc()).first()
            if s and (s.created_at.timestamp()>updated or not rows):
                rows=json.loads(s.payload); updated=s.created_at.timestamp(); error=None; source="nobitex:persisted"
        finally: db.close()
    except Exception: logger.exception("Persisted scanner cache read failed")
    return {"rows":rows,"updated_at":updated,"error":error,"source":source,"count":len(rows)}

def is_fresh(max_age_seconds=300):
    with _LOCK: return bool(_CACHE["rows"]) and time.time()-_CACHE["updated_at"]<max_age_seconds

def _json_safe(v):
    if v is None:return None
    if isinstance(v,float): return None if math.isnan(v) or math.isinf(v) else v
    if hasattr(v,"item"):
        try:return _json_safe(v.item())
        except Exception:pass
    if isinstance(v,(int,str,bool)):return v
    return v

def _records_from_df(df):
    if df is None or df.empty:return []
    df=df.where(pd.notnull(df),None)
    return [{k:_json_safe(v) for k,v in row.items()} for row in df.to_dict(orient="records")]

def _build_nobitex_client():
    return NobitexClient(quote_currency="IRT",testnet=False)

def _fetch_nobitex(limit):
    client=_build_nobitex_client()
    rows=client.get_all_market_stats("IRT")
    if not rows:return pd.DataFrame()
    rows=[r for r in rows if float(r.get("Price") or 0)>0]
    rows.sort(key=lambda r:float(r.get("Volume") or 0),reverse=True)
    return pd.DataFrame(rows[:max(1,int(limit))])

def _fetch_global_map(symbols):
    """Best-effort CMC enrichment. Failure never blocks the Nobitex scanner."""
    key=getattr(settings,"cryptosscanner_cmc_key","") or ""
    if not key:return {}
    try:
        from api.api_coinmarketcap import CoinMarketCapClient, extract_usd_quote, iter_cmc_coins
        payload=CoinMarketCapClient(api_key=key).get_quotes_batched(symbols=symbols,convert="USD")
        out={}
        for coin in iter_cmc_coins(payload):
            sym=str(coin.get("symbol") or "").upper().strip()
            usd=extract_usd_quote(coin)
            if not sym or not usd:continue
            out[sym]={
                "GlobalPriceUSD":float(usd.get("price") or 0),
                "Global1hPct":float(usd.get("percent_change_1h") or 0),
                "Global24hPct":float(usd.get("percent_change_24h") or 0),
                "GlobalVolumeUSD":float(usd.get("volume_24h") or 0),
                "GlobalVolumeChange24hPct":float(usd.get("volume_change_24h") or 0),
                "GlobalUpdated":usd.get("last_updated"),
                "GlobalDataSource":"CoinMarketCap",
            }
        return out
    except Exception as exc:
        logger.warning("Global CMC enrichment failed; local snapshot retained: %s",exc)
        return {}

def _history_features(symbol,price,volume):
    now=time.time()
    with _LOCK:
        h=_HISTORY[symbol]; h.append((now,price,volume)); samples=list(h)
    def at(seconds):
        target=now-seconds
        eligible=[x for x in samples if x[0]<=target]
        return eligible[-1][1] if eligible else None
    def pct(old,new): return (new-old)/old*100 if old and old>0 and new>0 else 0.0
    p5,p15,p30=at(300),at(900),at(1800)
    return {"5m Change (%)":pct(p5,price) if p5 else 0.0,"15m Change (%)":pct(p15,price) if p15 else 0.0,"30m Change (%)":pct(p30,price) if p30 else 0.0}

def _classify_market(row):
    ch5=float(row.get("5m Change (%)") or 0); ch15=float(row.get("15m Change (%)") or 0); ch30=float(row.get("30m Change (%)") or 0); ch24=float(row.get("24h Change (%)") or 0)
    price=float(row.get("Price") or 0); high=float(row.get("Day High") or 0); bid=float(row.get("Bid") or 0); ask=float(row.get("Ask") or 0)
    pump=max(0,ch5)*9+max(0,ch15)*5+max(0,ch30)*2.5+max(0,ch24)*.8
    trend=max(0,ch15)*5+max(0,ch30)*4+max(0,ch24)*1.5
    if ch15>0 and ch30>0 and ch24>0: trend+=15
    if price>0 and high>0 and (high-price)/price*100<=1: pump+=8; trend+=8
    spread=(ask-bid)/bid*100 if bid>0 and ask>=bid else 0
    if spread>2:pump-=15;trend-=10
    pump=max(0,min(100,pump));trend=max(0,min(100,trend))
    reasons=[]
    if ch5>=1:reasons.append(f"5m momentum +{ch5:.2f}%")
    if ch15>=2:reasons.append(f"15m momentum +{ch15:.2f}%")
    if ch30>=3:reasons.append(f"30m momentum +{ch30:.2f}%")
    if ch24>=5:reasons.append(f"24h strength +{ch24:.2f}%")
    if price>0 and high>0 and (high-price)/price*100<=1:reasons.append("near daily high")
    if spread>2:reasons.append("wide spread")
    if pump>=70 and ch5>=1:condition="STRONG_PUMP"
    elif pump>=50 and ch15>=1.5:condition="PUMP"
    elif trend>=65 and ch15>0 and ch30>0:condition="STRONG_UPTREND"
    elif trend>=45 and ch15>0:condition="UPTREND"
    elif ch24>0:condition="WEAK_UPTREND"
    else:condition="SIDEWAYS"
    return condition,round(pump,2),round(trend,2),reasons

def _enrich(df):
    if df is None or df.empty:return df
    global_map=_fetch_global_map([str(x).upper() for x in df["Symbol"].tolist()])
    enriched=[]
    for row in df.to_dict(orient="records"):
        symbol=str(row.get("Symbol") or "").upper(); price=float(row.get("Price") or 0); volume=float(row.get("Volume") or 0)
        row.update(_history_features(symbol,price,volume))
        condition,pump,trend,reasons=_classify_market(row)
        row.update({"Pump Score":pump,"Trend Score":trend,"Market Condition":condition,
                    "Entry Ready":bool((condition in {"STRONG_PUMP","PUMP","STRONG_UPTREND"} and pump>=50) or (condition=="STRONG_UPTREND" and trend>=65)),
                    "Signal":"Strong Buy" if condition in {"STRONG_PUMP","STRONG_UPTREND"} else "Buy Signal" if condition in {"PUMP","UPTREND"} else "Neutral",
                    "Score":round(max(pump,trend),2),
                    "Risk":"High" if condition in {"STRONG_PUMP","PUMP"} and float(row.get("5m Change (%)") or 0)>=5 else "Medium",
                    "Risk_Level":"High" if condition in {"STRONG_PUMP","PUMP"} and float(row.get("5m Change (%)") or 0)>=5 else "Medium",
                    "Reasons":"; ".join(reasons) if reasons else "No strong momentum confirmation"})
        if symbol in global_map: row.update(global_map[symbol])
        \n        if price > 0 and float(row.get("Bid") or 0) > 0:\n            row["SpreadPct"]=(float(row.get("Ask") or price)-float(row.get("Bid") or price))/float(row.get("Bid") or price)*100.0\n        else:\n            row["SpreadPct"]=0.0\n        row["ChasePct"]=max(0.0,(float(row.get("Ask") or price)-price)/price*100.0) if price>0 else 0.0\n        row["SnapshotTimestamp"]=time.time()
        enriched.append(row)
    return pd.DataFrame(enriched)

def run_scan(persist_snapshot=False):
    try:
        df=_enrich(_fetch_nobitex(settings.scan_limit))
        if df is None or df.empty:
            with _LOCK:_CACHE.update(error="no data returned by Nobitex",source="nobitex")
            return 0
        df=df.sort_values(by=["Entry Ready","Score","Pump Score","Trend Score"],ascending=[False]*4,na_position="last").reset_index(drop=True)
        records=_records_from_df(df)
        with _LOCK:_CACHE.update(rows=records,updated_at=time.time(),error=None,source="nobitex")
        if persist_snapshot:
            try:_persist_snapshot(records)
            except Exception as exc:logger.warning("Snapshot persist failed: %s",exc)
        return len(records)
    except Exception:
        logger.exception("Nobitex scan failed")
        with _LOCK:_CACHE.update(error="Nobitex scan failed",source="nobitex")
        return 0

def _persist_snapshot(records):
    if not settings.market_history_enabled:return
    from server.models import ScanSnapshot
    rows=records[:max(1,int(settings.market_history_max_rows_per_scan))]
    db=SessionLocal()
    try:
        db.add(ScanSnapshot(row_count=len(rows),payload=json.dumps(rows,default=str,ensure_ascii=False)))
        db.commit()
        cutoff=time.time()-max(1,int(settings.market_history_retention_days))*86400
        from datetime import datetime,timezone
        db.query(ScanSnapshot).filter(ScanSnapshot.created_at < datetime.fromtimestamp(cutoff,tz=timezone.utc)).delete(synchronize_session=False)
        db.commit()
    finally:db.close()

def get_symbol_price(symbol):
    if not symbol:return None
    sym=symbol.upper().strip()
    with _LOCK:
        for row in _CACHE["rows"]:
            if str(row.get("Symbol") or "").upper()==sym:
                p=row.get("Price")
                if isinstance(p,(int,float)) and p>0:return float(p)
    return None

__all__=["run_scan","get_cached","is_fresh","get_symbol_price"]
