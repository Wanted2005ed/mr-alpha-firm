import os
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app=FastAPI(title='XAUUSD Live Market Bridge')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
SYMBOL=os.getenv('MT5_SYMBOL','XAUUSD')

def connect():
    login=os.getenv('MT5_LOGIN'); password=os.getenv('MT5_PASSWORD'); server=os.getenv('MT5_SERVER')
    if login and password and server:
        ok=mt5.initialize(login=int(login),password=password,server=server)
    else:
        ok=mt5.initialize()
    if not ok: raise RuntimeError(str(mt5.last_error()))
    if not mt5.symbol_select(SYMBOL,True): raise RuntimeError(f'Cannot select {SYMBOL}')

def indicators(df):
    close=df.close
    ema20=close.ewm(span=20,adjust=False).mean().iloc[-1]
    ema50=close.ewm(span=50,adjust=False).mean().iloc[-1]
    delta=close.diff(); gain=delta.clip(lower=0).rolling(14).mean(); loss=(-delta.clip(upper=0)).rolling(14).mean()
    rs=gain/(loss.replace(0,np.nan)); rsi=(100-(100/(1+rs))).iloc[-1]
    tr=pd.concat([df.high-df.low,(df.high-df.close.shift()).abs(),(df.low-df.close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.rolling(14).mean().iloc[-1]
    return float(ema20),float(ema50),float(rsi),float(atr)

def snapshot():
    rates=mt5.copy_rates_from_pos(SYMBOL,mt5.TIMEFRAME_M1,0,150)
    tick=mt5.symbol_info_tick(SYMBOL)
    if rates is None or tick is None: raise RuntimeError(str(mt5.last_error()))
    df=pd.DataFrame(rates); ema20,ema50,rsi,atr=indicators(df); price=float((tick.bid+tick.ask)/2)
    score=0
    score += 1 if price>ema20 else -1
    score += 1 if ema20>ema50 else -1
    score += 1 if rsi>55 else (-1 if rsi<45 else 0)
    signal='BUY' if score>=2 else ('SELL' if score<=-2 else 'WAIT')
    confidence=min(95,50+abs(score)*15)
    return {'symbol':SYMBOL,'price':price,'bid':float(tick.bid),'ask':float(tick.ask),'spread':float(tick.ask-tick.bid),'ema_regime':'BULLISH' if ema20>ema50 else 'BEARISH','rsi':rsi,'atr':atr,'signal':signal,'confidence':confidence,'time':datetime.now(timezone.utc).isoformat()}

@app.get('/api/health')
def health(): return {'ok':True,'mode':'market-data-only'}

@app.get('/api/live')
def live():
    connect()
    try: return snapshot()
    finally: mt5.shutdown()
