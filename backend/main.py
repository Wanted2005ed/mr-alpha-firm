import os, time, math, sqlite3, hashlib, hmac, secrets, smtplib, ssl
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from threading import Lock
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()
DB=os.getenv('DB_PATH','bot.db')
SYMBOLS=[s.strip() for s in os.getenv('MT5_SYMBOLS','XAUUSD,EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD,NZDUSD').split(',') if s.strip()]
TIMEFRAMES={'M1':mt5.TIMEFRAME_M1,'M5':mt5.TIMEFRAME_M5,'M15':mt5.TIMEFRAME_M15,'H1':mt5.TIMEFRAME_H1}
MIN_SCORE=int(os.getenv('MIN_SIGNAL_SCORE','80'))
RISK_PER_TRADE=float(os.getenv('RISK_PER_TRADE_PCT','0.5'))
MAX_OPEN=int(os.getenv('MAX_OPEN_PAPER_TRADES','3'))
POLL=float(os.getenv('POLL_SECONDS','1'))
BASE=os.getenv('PUBLIC_BASE_URL','http://127.0.0.1:8000')
EXECUTION_MODE='paper'
lock=Lock(); engine_running=False

app=FastAPI(title='Mr Alpha Autonomous Market Engine',version='2.0')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])

class AuthIn(BaseModel): email: EmailStr; password: str
class TradeSettings(BaseModel): symbols:list[str]|None=None; min_score:int|None=None

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,password_hash TEXT,verified INTEGER DEFAULT 0,verify_token TEXT,created_at TEXT); CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY,symbol TEXT,direction TEXT,entry REAL,sl REAL,tp REAL,score INTEGER,reasons TEXT,opened_at TEXT,closed_at TEXT,exit REAL,result_r REAL,status TEXT,estimated_window TEXT); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,kind TEXT,message TEXT,created_at TEXT);'''); c.commit(); c.close()

def hashpw(p): return hashlib.sha256(p.encode()).hexdigest()
def token(email): return hmac.new(os.getenv('AUTH_SECRET','change-me').encode(),email.encode(),hashlib.sha256).hexdigest()

def send_mail(to,subject,body):
    host=os.getenv('SMTP_HOST'); port=int(os.getenv('SMTP_PORT','587')); user=os.getenv('SMTP_USER'); pw=os.getenv('SMTP_PASSWORD'); sender=os.getenv('SMTP_FROM',user or '')
    if not host or not user or not pw: return False
    m=EmailMessage(); m['From']=sender; m['To']=to; m['Subject']=subject; m.set_content(body)
    with smtplib.SMTP(host,port,timeout=10) as s: s.starttls(context=ssl.create_default_context()); s.login(user,pw); s.send_message(m)
    return True

def log(kind,msg):
    c=db(); c.execute('INSERT INTO events(kind,message,created_at) VALUES(?,?,?)',(kind,msg,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()

def connect():
    login=os.getenv('MT5_LOGIN'); password=os.getenv('MT5_PASSWORD'); server=os.getenv('MT5_SERVER')
    ok=mt5.initialize(login=int(login),password=password,server=server) if login and password and server else mt5.initialize()
    if not ok: raise RuntimeError(str(mt5.last_error()))
    for s in SYMBOLS: mt5.symbol_select(s,True)

def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); down=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); rs=up/down.replace(0,np.nan); return 100-(100/(1+rs))

def macd(s):
    return s.ewm(span=12,adjust=False).mean()-s.ewm(span=26,adjust=False).mean(), s.ewm(span=9,adjust=False).mean()

def analyze(symbol):
    rates=mt5.copy_rates_from_pos(symbol,mt5.TIMEFRAME_M15,0,250)
    if rates is None or len(rates)<80: return None
    df=pd.DataFrame(rates); close=df.close; high=df.high; low=df.low; op=df.open
    ema20=close.ewm(span=20,adjust=False).mean(); ema50=close.ewm(span=50,adjust=False).mean(); ema200=close.ewm(span=200,adjust=False).mean(); rr=rsi(close); mline,msignal=macd(close)
    tr=pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1); atr=tr.rolling(14).mean()
    x=df.iloc[-2]; prev=df.iloc[-3]; body=abs(x.close-x.open); rng=max(x.high-x.low,1e-9); upper=x.high-max(x.open,x.close); lower=min(x.open,x.close)-x.low
    bull_eng=x.close>x.open and prev.close<prev.open and x.close>=prev.open and x.open<=prev.close
    bear_eng=x.close<x.open and prev.close>prev.open and x.open>=prev.close and x.close<=prev.open
    bull_pin=lower>body*2 and upper<body and x.close>x.open
    bear_pin=upper>body*2 and lower<body and x.close<x.open
    score=50; reasons=[]; direction='WAIT'
    if close.iloc[-1]>ema20.iloc[-1]>ema50.iloc[-1]>ema200.iloc[-1]: score+=15; reasons.append('EMA20/50/200 bullish alignment'); direction='BUY'
    elif close.iloc[-1]<ema20.iloc[-1]<ema50.iloc[-1]<ema200.iloc[-1]: score+=15; reasons.append('EMA20/50/200 bearish alignment'); direction='SELL'
    else: score-=5
    rv=rr.iloc[-1]
    if direction=='BUY' and 52<=rv<=70: score+=10; reasons.append(f'RSI bullish momentum {rv:.1f}')
    elif direction=='SELL' and 30<=rv<=48: score+=10; reasons.append(f'RSI bearish momentum {rv:.1f}')
    else: score-=3
    if direction=='BUY' and mline.iloc[-1]>msignal.iloc[-1]: score+=8; reasons.append('MACD bullish confirmation')
    if direction=='SELL' and mline.iloc[-1]<msignal.iloc[-1]: score+=8; reasons.append('MACD bearish confirmation')
    if direction=='BUY' and (bull_eng or bull_pin): score+=12; reasons.append('bullish candle pattern')
    if direction=='SELL' and (bear_eng or bear_pin): score+=12; reasons.append('bearish candle pattern')
    recent_high=high.iloc[-21:-2].max(); recent_low=low.iloc[-21:-2].min()
    if direction=='BUY' and close.iloc[-1]>recent_high: score+=8; reasons.append('M15 structure breakout')
    if direction=='SELL' and close.iloc[-1]<recent_low: score+=8; reasons.append('M15 structure breakdown')
    score=max(0,min(100,int(score)))
    tick=mt5.symbol_info_tick(symbol)
    if not tick: return None
    price=float(tick.ask if direction=='BUY' else tick.bid)
    a=float(atr.iloc[-1]);
    if not math.isfinite(a) or a<=0: return None
    sl=price-a*1.2 if direction=='BUY' else price+a*1.2; tp=price+a*2.4 if direction=='BUY' else price-a*2.4
    window=f'{max(10,int(a/price*100000*3))}-{max(20,int(a/price*100000*8))} min'
    return {'symbol':symbol,'direction':direction if score>=MIN_SCORE else 'WAIT','price':price,'sl':sl,'tp':tp,'score':score,'reasons':reasons,'atr':a,'estimated_window':window,'rsi':float(rv),'ema20':float(ema20.iloc[-1]),'ema50':float(ema50.iloc[-1]),'ema200':float(ema200.iloc[-1]),'timestamp':datetime.now(timezone.utc).isoformat()}

def open_trade(a):
    with lock:
        c=db(); n=c.execute("SELECT COUNT(*) n FROM trades WHERE status='OPEN'").fetchone()['n']
        if n>=MAX_OPEN or a['direction']=='WAIT': c.close(); return
        exists=c.execute("SELECT 1 FROM trades WHERE symbol=? AND status='OPEN'",(a['symbol'],)).fetchone();
        if exists: c.close(); return
        now=datetime.now(timezone.utc).isoformat(); reasons='; '.join(a['reasons'])
        c.execute('INSERT INTO trades(symbol,direction,entry,sl,tp,score,reasons,opened_at,status,estimated_window,result_r) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(a['symbol'],a['direction'],a['price'],a['sl'],a['tp'],a['score'],reasons,now,'OPEN',a['estimated_window'],0)); c.commit(); c.close()
    msg=f"MR ALPHA BOT - PAPER TRADE OPENED\n{a['symbol']} {a['direction']}\nEntry: {a['price']:.5f}\nSL: {a['sl']:.5f}\nTP: {a['tp']:.5f}\nSignal strength: {a['score']}/100\nEstimated window: {a['estimated_window']}\nWhy: {reasons}\nExecution mode: PAPER"
    log('TRADE_OPEN',msg); email=os.getenv('ALERT_EMAIL');
    if email:
        try: send_mail(email,f"Trade opened: {a['symbol']} {a['direction']} ({a['score']}/100)",msg)
        except Exception as e: log('EMAIL_ERROR',str(e))

def manage_trades():
    c=db(); rows=c.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall(); c.close()
    for t in rows:
        tick=mt5.symbol_info_tick(t['symbol']);
        if not tick: continue
        px=float(tick.bid if t['direction']=='BUY' else tick.ask); exit_reason=None
        if t['direction']=='BUY' and px>=t['tp']: exit_reason='TAKE_PROFIT'
        elif t['direction']=='BUY' and px<=t['sl']: exit_reason='STOP_LOSS'
        elif t['direction']=='SELL' and px<=t['tp']: exit_reason='TAKE_PROFIT'
        elif t['direction']=='SELL' and px>=t['sl']: exit_reason='STOP_LOSS'
        if exit_reason:
            risk=abs(t['entry']-t['sl']); r=(px-t['entry'])/risk if t['direction']=='BUY' else (t['entry']-px)/risk
            c=db(); c.execute('UPDATE trades SET status="CLOSED",closed_at=?,exit=?,result_r=? WHERE id=?',(datetime.now(timezone.utc).isoformat(),px,r,t['id'])); c.commit(); c.close()
            msg=f"MR ALPHA BOT - PAPER TRADE CLOSED\n{t['symbol']} {t['direction']}\nEntry: {t['entry']:.5f}\nExit: {px:.5f}\nResult: {r:+.2f}R\nReason: {exit_reason}\nOriginal strength: {t['score']}/100"
            log('TRADE_CLOSE',msg); email=os.getenv('ALERT_EMAIL');
            if email:
                try: send_mail(email,f"Trade closed: {t['symbol']} {exit_reason}",msg)
                except Exception as e: log('EMAIL_ERROR',str(e))

def engine():
    global engine_running
    engine_running=True
    try:
        connect()
        while engine_running:
            for s in SYMBOLS:
                try:
                    a=analyze(s)
                    if a and a['direction']!='WAIT': open_trade(a)
                except Exception as e: log('ENGINE_ERROR',f'{s}: {e}')
            try: manage_trades()
            except Exception as e: log('MANAGER_ERROR',str(e))
            time.sleep(POLL)
    finally: mt5.shutdown(); engine_running=False

@app.on_event('startup')
def startup(): init_db()

@app.get('/api/health')
def health(): return {'ok':True,'engine_running':engine_running,'execution_mode':EXECUTION_MODE,'symbols':SYMBOLS}

@app.post('/api/auth/register')
def register(x:AuthIn):
    if len(x.password)<8: raise HTTPException(400,'Password must be at least 8 characters')
    t=secrets.token_urlsafe(24); c=db()
    try: c.execute('INSERT INTO users(email,password_hash,verify_token,created_at) VALUES(?,?,?,?)',(x.email,hashpw(x.password),t,datetime.now(timezone.utc).isoformat())); c.commit()
    except sqlite3.IntegrityError: raise HTTPException(409,'Account already exists')
    finally: c.close()
    link=f'{BASE}/api/auth/verify?token={t}&email={x.email}'
    body=f'Verify your Mr Alpha account:\n\n{link}\n\nThis link verifies your email for the demo trading dashboard.'
    sent=False
    try: sent=send_mail(x.email,'Verify your Mr Alpha account',body)
    except Exception: pass
    return {'ok':True,'email_sent':sent,'verification_link':link if not sent else None}

@app.get('/api/auth/verify')
def verify(email:str,token:str):
    c=db(); row=c.execute('SELECT * FROM users WHERE email=? AND verify_token=?',(email,token)).fetchone()
    if not row: raise HTTPException(400,'Invalid verification link')
    c.execute('UPDATE users SET verified=1 WHERE email=?',(email,)); c.commit(); c.close(); return {'ok':True,'message':'Email verified. You can log in.'}

@app.post('/api/auth/login')
def login(x:AuthIn):
    c=db(); row=c.execute('SELECT * FROM users WHERE email=?',(x.email,)).fetchone(); c.close()
    if not row or row['password_hash']!=hashpw(x.password): raise HTTPException(401,'Invalid email or password')
    if not row['verified']: raise HTTPException(403,'Verify your email before logging in')
    return {'ok':True,'session':secrets.token_urlsafe(24),'email':x.email}

@app.post('/api/engine/start')
def start(background_tasks:BackgroundTasks):
    global engine_running
    if engine_running: return {'ok':True,'already_running':True,'execution_mode':EXECUTION_MODE}
    background_tasks.add_task(engine); return {'ok':True,'started':True,'execution_mode':EXECUTION_MODE}

@app.post('/api/engine/stop')
def stop():
    global engine_running; engine_running=False; return {'ok':True,'stopped':True}

@app.get('/api/market')
def market():
    connect()
    try:
        out=[]
        for s in SYMBOLS:
            a=analyze(s)
            if a: out.append(a)
        return {'timestamp':datetime.now(timezone.utc).isoformat(),'data':out}
    finally: mt5.shutdown()

@app.get('/api/trades')
def trades():
    c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM trades ORDER BY id DESC LIMIT 100').fetchall()]; c.close(); return rows

@app.get('/api/events')
def events():
    c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100').fetchall()]; c.close(); return rows
