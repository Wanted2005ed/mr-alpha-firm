import os, time, math, sqlite3, hashlib, hmac, secrets, smtplib, ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from threading import Lock, Thread
import numpy as np
import pandas as pd
try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()
DB=os.getenv('DB_PATH','bot.db')
SYMBOLS=[s.strip() for s in os.getenv('MT5_SYMBOLS','XAUUSD,EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,NZDUSD').split(',') if s.strip()]
MIN_SCORE=int(os.getenv('MIN_SIGNAL_SCORE','80'))
RISK_PER_TRADE=float(os.getenv('RISK_PER_TRADE_PCT','0.5'))
MAX_OPEN=int(os.getenv('MAX_OPEN_PAPER_TRADES','3'))
POLL=max(1.0,float(os.getenv('POLL_SECONDS','1')))
DATA_SOURCE=os.getenv('DATA_SOURCE','auto').lower()
if mt5 is None and DATA_SOURCE == 'auto': DATA_SOURCE='yfinance'
if DATA_SOURCE == 'yfinance': POLL=max(POLL,60.0)
BASE=os.getenv('PUBLIC_BASE_URL','http://127.0.0.1:8000')
EXECUTION_MODE='paper'
PAPER_START_BALANCE=float(os.getenv('PAPER_START_BALANCE','200000'))
lock=Lock(); engine_running=False; engine_thread=None
app=FastAPI(title='Mr Alpha Autonomous Market Engine',version='2.1')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])

class AuthIn(BaseModel): email: EmailStr; password: str

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,password_hash TEXT,verified INTEGER DEFAULT 0,verify_token TEXT,created_at TEXT); CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY,symbol TEXT,direction TEXT,entry REAL,sl REAL,tp REAL,score INTEGER,reasons TEXT,opened_at TEXT,closed_at TEXT,exit REAL,result_r REAL,status TEXT,estimated_window TEXT,risk_amount REAL); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,kind TEXT,message TEXT,created_at TEXT);''');
    try: c.execute('ALTER TABLE trades ADD COLUMN risk_amount REAL')
    except sqlite3.OperationalError: pass
    c.commit(); c.close()

def hashpw(p): return hashlib.sha256(p.encode()).hexdigest()
def send_mail(to,subject,body):
    host=os.getenv('SMTP_HOST'); port=int(os.getenv('SMTP_PORT','587')); user=os.getenv('SMTP_USER'); pw=os.getenv('SMTP_PASSWORD'); sender=os.getenv('SMTP_FROM',user or '')
    if not host or not user or not pw: return False
    m=EmailMessage(); m['From']=sender; m['To']=to; m['Subject']=subject; m.set_content(body)
    with smtplib.SMTP(host,port,timeout=10) as s: s.starttls(context=ssl.create_default_context()); s.login(user,pw); s.send_message(m)
    return True

def log(kind,msg):
    c=db(); c.execute('INSERT INTO events(kind,message,created_at) VALUES(?,?,?)',(kind,msg,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()

def connect():
    if DATA_SOURCE == 'yfinance': return True
    if mt5 is None: raise RuntimeError('MetaTrader5 is unavailable; set DATA_SOURCE=yfinance for cloud mode')
    login=os.getenv('MT5_LOGIN'); password=os.getenv('MT5_PASSWORD'); server=os.getenv('MT5_SERVER')
    ok=mt5.initialize(login=int(login),password=password,server=server) if login and password and server else mt5.initialize()
    if not ok: raise RuntimeError(str(mt5.last_error()))
    for s in SYMBOLS: mt5.symbol_select(s,True)
    return True

def yf_symbol(symbol): return {'XAUUSD':'GC=F','EURUSD':'EURUSD=X','GBPUSD':'GBPUSD=X','USDJPY':'JPY=X','AUDUSD':'AUDUSD=X','USDCHF':'CHF=X','USDCAD':'CAD=X','NZDUSD':'NZDUSD=X'}.get(symbol,symbol+'=X')
def market_frame(symbol):
    if DATA_SOURCE != 'yfinance':
        rates=mt5.copy_rates_from_pos(symbol,mt5.TIMEFRAME_M15,0,250); return pd.DataFrame(rates) if rates is not None else pd.DataFrame()
    df=yf.download(yf_symbol(symbol),period='5d',interval='15m',auto_adjust=False,progress=False,threads=False)
    if df is None or df.empty: return pd.DataFrame()
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.rename(columns=str.lower).reset_index()
    return df[['open','high','low','close','volume']].dropna().tail(250)

def latest_price(symbol):
    if DATA_SOURCE != 'yfinance':
        tick=mt5.symbol_info_tick(symbol); return None if tick is None else float((tick.ask+tick.bid)/2)
    df=market_frame(symbol); return None if df.empty else float(df.close.iloc[-1])

def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); down=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); rs=up/down.replace(0,np.nan); return 100-(100/(1+rs))

def analyze(symbol):
    df=market_frame(symbol)
    if len(df)<80: return None
    close=df.close; high=df.high; low=df.low; ema20=close.ewm(span=20,adjust=False).mean(); ema50=close.ewm(span=50,adjust=False).mean(); ema200=close.ewm(span=200,adjust=False).mean(); rv=rsi(close).iloc[-1]; m12=close.ewm(span=12,adjust=False).mean(); m26=close.ewm(span=26,adjust=False).mean(); macd=m12-m26; sig=macd.ewm(span=9,adjust=False).mean(); tr=pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1); atr=tr.rolling(14).mean().iloc[-1]
    x=df.iloc[-2]; prev=df.iloc[-3]; body=abs(x.close-x.open); rng=max(x.high-x.low,1e-9); upper=x.high-max(x.open,x.close); lower=min(x.open,x.close)-x.low
    bull_eng=x.close>x.open and prev.close<prev.open and x.close>=prev.open and x.open<=prev.close; bear_eng=x.close<x.open and prev.close>prev.open and x.open>=prev.close and x.close<=prev.open; bull_pin=lower>body*2 and upper<body; bear_pin=upper>body*2 and lower<body
    direction='BUY' if close.iloc[-1]>ema20.iloc[-1]>ema50.iloc[-1]>ema200.iloc[-1] else 'SELL' if close.iloc[-1]<ema20.iloc[-1]<ema50.iloc[-1]<ema200.iloc[-1] else 'WAIT'; score=40; reasons=[]
    if direction=='BUY': score+=20; reasons.append('EMA20/50/200 bullish alignment')
    elif direction=='SELL': score+=20; reasons.append('EMA20/50/200 bearish alignment')
    else: reasons.append('EMA trend not fully aligned')
    if direction=='BUY' and 52<=rv<=70: score+=10; reasons.append(f'RSI bullish momentum {rv:.1f}')
    elif direction=='SELL' and 30<=rv<=48: score+=10; reasons.append(f'RSI bearish momentum {rv:.1f}')
    if direction=='BUY' and macd.iloc[-1]>sig.iloc[-1]: score+=8; reasons.append('MACD bullish confirmation')
    elif direction=='SELL' and macd.iloc[-1]<sig.iloc[-1]: score+=8; reasons.append('MACD bearish confirmation')
    if direction=='BUY' and (bull_eng or bull_pin): score+=12; reasons.append('bullish candle pattern')
    elif direction=='SELL' and (bear_eng or bear_pin): score+=12; reasons.append('bearish candle pattern')
    recent_high=high.iloc[-21:-2].max(); recent_low=low.iloc[-21:-2].min()
    if direction=='BUY' and close.iloc[-1]>recent_high: score+=10; reasons.append('M15 structure breakout')
    elif direction=='SELL' and close.iloc[-1]<recent_low: score+=10; reasons.append('M15 structure breakdown')
    score=max(0,min(100,int(score))); price=latest_price(symbol)
    if price is None or not math.isfinite(atr) or atr<=0: return None
    sl=price-atr*1.2 if direction=='BUY' else price+atr*1.2; tp=price+atr*2.4 if direction=='BUY' else price-atr*2.4
    return {'symbol':symbol,'direction':direction if score>=MIN_SCORE else 'WAIT','price':price,'sl':sl,'tp':tp,'score':score,'reasons':reasons,'atr':float(atr),'estimated_window':'15-60 min','rsi':float(rv),'ema20':float(ema20.iloc[-1]),'ema50':float(ema50.iloc[-1]),'ema200':float(ema200.iloc[-1]),'timestamp':datetime.now(timezone.utc).isoformat()}

def account_snapshot():
    balance=float(PAPER_START_BALANCE)
    realized=0.0
    open_count=0
    try:
        c=db()
        closed=c.execute("SELECT result_r FROM trades WHERE status='CLOSED'").fetchall()
        open_rows=c.execute("SELECT symbol,direction,entry,sl FROM trades WHERE status='OPEN'").fetchall()
        c.close()
        default_risk=float(PAPER_START_BALANCE*RISK_PER_TRADE_PCT/100.0)
        realized=sum(float(r['result_r'] or 0.0)*default_risk for r in closed)
        open_count=len(open_rows)
    except Exception as e:
        log('ACCOUNT_ERROR',str(e))
    balance += realized
    return {'currency':'USD','starting_balance':float(PAPER_START_BALANCE),'balance':balance,'equity':balance,'realized_pnl':realized,'unrealized_pnl':0.0,'total_pnl':realized,'open_trades':open_count,'risk_per_trade_pct':RISK_PER_TRADE_PCT}

def open_trade(a):
    if a['direction']=='WAIT': return
    with lock:
        c=db(); n=c.execute("SELECT COUNT(*) n FROM trades WHERE status='OPEN'").fetchone()['n']; exists=c.execute("SELECT 1 FROM trades WHERE symbol=? AND status='OPEN'",(a['symbol'],)).fetchone();
        if n>=MAX_OPEN or exists: c.close(); return
        reasons='; '.join(a['reasons']); now=datetime.now(timezone.utc).isoformat(); risk_amount=PAPER_START_BALANCE*RISK_PER_TRADE_PCT/100.0; c.execute('INSERT INTO trades(symbol,direction,entry,sl,tp,score,reasons,opened_at,status,estimated_window,result_r,risk_amount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(a['symbol'],a['direction'],a['price'],a['sl'],a['tp'],a['score'],reasons,now,'OPEN',a['estimated_window'],0,risk_amount)); c.commit(); c.close()
    msg=f"MR ALPHA BOT - PAPER TRADE OPENED\\n{a['symbol']} {a['direction']}\\nEntry: {a['price']:.5f}\\nSL: {a['sl']:.5f}\\nTP: {a['tp']:.5f}\\nSignal strength: {a['score']}/100\\nEstimated window: {a['estimated_window']}\\nWhy: {reasons}\\nExecution mode: PAPER"
    log('TRADE_OPEN',msg); email=os.getenv('ALERT_EMAIL')
    if email:
        try: send_mail(email,f"Trade opened: {a['symbol']} {a['direction']}",msg)
        except Exception as e: log('EMAIL_ERROR',str(e))

def manage_trades():
    c=db(); rows=c.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall(); c.close()
    for t in rows:
        px=latest_price(t['symbol']);
        if px is None: continue
        reason=None
        if t['direction']=='BUY' and px>=t['tp']: reason='TAKE_PROFIT'
        elif t['direction']=='BUY' and px<=t['sl']: reason='STOP_LOSS'
        elif t['direction']=='SELL' and px<=t['tp']: reason='TAKE_PROFIT'
        elif t['direction']=='SELL' and px>=t['sl']: reason='STOP_LOSS'
        if reason:
            risk=abs(t['entry']-t['sl']); r=(px-t['entry'])/risk if t['direction']=='BUY' else (t['entry']-px)/risk; c=db(); c.execute('UPDATE trades SET status="CLOSED",closed_at=?,exit=?,result_r=? WHERE id=?',(datetime.now(timezone.utc).isoformat(),px,r,t['id'])); c.commit(); c.close(); msg=f"MR ALPHA BOT - PAPER TRADE CLOSED\\n{t['symbol']} {t['direction']}\\nEntry: {t['entry']:.5f}\\nExit: {px:.5f}\\nResult: {r:+.2f}R\\nReason: {reason}\\nOriginal strength: {t['score']}/100"; log('TRADE_CLOSE',msg); email=os.getenv('ALERT_EMAIL');
            if email:
                try: send_mail(email,f"Trade closed: {t['symbol']} {reason}",msg)
                except Exception as e: log('EMAIL_ERROR',str(e))

def engine():
    global engine_running
    try:
        connect(); log('ENGINE','Engine connected to MT5 market data')
        while engine_running:
            for s in SYMBOLS:
                if not engine_running: break
                try:
                    a=analyze(s)
                    if a: open_trade(a)
                except Exception as e: log('ENGINE_ERROR',f'{s}: {e}')
            try: manage_trades()
            except Exception as e: log('MANAGER_ERROR',str(e))
            time.sleep(POLL)
    except Exception as e: log('ENGINE_ERROR',str(e))
    finally:
        if mt5 is not None and DATA_SOURCE != 'yfinance': mt5.shutdown()
        engine_running=False; log('ENGINE','Engine stopped')

@app.on_event('startup')
def startup(): init_db()
@app.get('/api/health')
def health(): return {'ok':True,'engine_running':engine_running,'execution_mode':EXECUTION_MODE,'symbols':SYMBOLS,'poll_seconds':POLL}
@app.post('/api/auth/register')
def register(x:AuthIn):
    if len(x.password)<8: raise HTTPException(400,'Password must be at least 8 characters')
    t=secrets.token_urlsafe(24); c=db()
    try: c.execute('INSERT INTO users(email,password_hash,verify_token,created_at) VALUES(?,?,?,?)',(x.email,hashpw(x.password),t,datetime.now(timezone.utc).isoformat())); c.commit()
    except sqlite3.IntegrityError: raise HTTPException(409,'Account already exists')
    finally: c.close()
    link=f'{BASE}/api/auth/verify?token={t}&email={x.email}'; sent=False
    try: sent=send_mail(x.email,'Verify your Mr Alpha account',f'Verify your Mr Alpha account:\n\n{link}\n')
    except Exception: pass
    return {'ok':True,'email_sent':sent,'verification_link':link if not sent else None}

@app.get('/api/auth/verify')
def verify(email:str,token:str):
    c=db(); row=c.execute('SELECT id FROM users WHERE email=? AND verify_token=?',(email,token)).fetchone()
    if not row: raise HTTPException(400,'Invalid verification link')
    c.execute('UPDATE users SET verified=1 WHERE email=?',(email,)); c.commit(); c.close(); return {'ok':True,'message':'Email verified. You can log in.'}

@app.post('/api/auth/demo')
def demo_login():
    return {'ok':True,'session':secrets.token_urlsafe(24),'email':'demo@mr-alpha-firm.local','demo':True}

@app.post('/api/auth/login')
def login(x:AuthIn):
    c=db(); row=c.execute('SELECT * FROM users WHERE email=?',(x.email,)).fetchone(); c.close()
    if not row or row['password_hash']!=hashpw(x.password): raise HTTPException(401,'Invalid email or password')
    if not row['verified']: raise HTTPException(403,'Verify your email before logging in')
    return {'ok':True,'session':secrets.token_urlsafe(24),'email':x.email}

@app.post('/api/engine/start')
def start():
    global engine_running,engine_thread
    if engine_running: return {'ok':True,'already_running':True,'execution_mode':EXECUTION_MODE}
    engine_running=True; engine_thread=Thread(target=engine,daemon=True); engine_thread.start(); return {'ok':True,'started':True,'execution_mode':EXECUTION_MODE}
@app.post('/api/engine/stop')
def stop():
    global engine_running; engine_running=False; return {'ok':True,'stopping':True}
@app.get('/api/market')
def market():
    connect()
    try: return {'timestamp':datetime.now(timezone.utc).isoformat(),'data':[a for s in SYMBOLS if (a:=analyze(s))]}
    finally:
        if mt5 is not None and DATA_SOURCE != 'yfinance': mt5.shutdown()
@app.get('/api/account')
def account():
    return account_snapshot()

@app.get('/api/trades')
def trades():
    c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 100').fetchall()]; c.close(); return rows
@app.get('/api/events')
def events():
    c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100').fetchall()]; c.close(); return rows
