from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="XAUUSD Sniper Risk Engine")
START_BALANCE=10000.0
MAX_DD_PCT=0.02
TARGET_PCT=0.10
MAX_SCALPS=3
state={"starting_balance":START_BALANCE,"balance":START_BALANCE,"equity":START_BALANCE,"peak_equity":START_BALANCE,"scalps":0,"dd_events":0,"status":"enabled"}

class EquityUpdate(BaseModel): equity: float
class ClosedTrade(BaseModel): pnl: float; duration_seconds: float; minimum_floating_pnl: float

def snapshot():
    dd=max(0,(state["peak_equity"]-state["equity"])/state["starting_balance"])
    profit=(state["equity"]-state["starting_balance"])/state["starting_balance"]
    if dd>=MAX_DD_PCT: state["status"]="locked_dd"
    elif profit>=TARGET_PCT: state["status"]="locked_target"
    elif state["scalps"]>=MAX_SCALPS: state["status"]="locked_scalps"
    else: state["status"]="enabled"
    return {**state,"account_dd_pct":dd,"profit_pct":profit,"updated_at":datetime.utcnow().isoformat()}

@app.get("/api/risk")
def risk(): return snapshot()

@app.post("/api/equity")
def equity(x: EquityUpdate):
    state["equity"]=x.equity; state["peak_equity"]=max(state["peak_equity"],x.equity); return snapshot()

@app.post("/api/trade/close")
def close_trade(t: ClosedTrade):
    is_scalp=t.duration_seconds<120
    is_dd_event=t.minimum_floating_pnl<0
    state["balance"]+=t.pnl; state["equity"]+=t.pnl
    if is_scalp: state["scalps"]+=1
    if is_dd_event: state["dd_events"]+=1
    out=snapshot(); out.update({"is_scalp":is_scalp,"is_dd_event":is_dd_event}); return out
