import hashlib
import json
import os
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from typing import Literal

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from redis import Redis
from rq import Queue

from settings import Settings, load_settings
from core.utils.timezone import tz_for_market
from core.data.provider import DataProviderConfig, MySQLProvider, LiquidityGate
from core.strategy.selector_csp_spv import csp_candidates, spv_candidates, scv_candidates, lcv_candidates
from core.strategy.selectors.condor import ic_candidates
from core.logging_config import configure_logging, get_logger
from datetime import date as _date


_settings = load_settings()
configure_logging(_settings.log_level, _settings.log_file, _settings.log_console)
logger = get_logger(__name__)

app = FastAPI(title="Options Backtest API", version="v1")

# CORS: allow local dev frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StrategyMeta(BaseModel):
    id: str
    mode: str = Field(pattern=r"^(fixed_contract|symbol_selector)$")
    market: str = Field(pattern=r"^(US|HK)$")
    symbols: Optional[List[str]] = None
    timeframe: str = Field(default="1d")
    trading_calendar: Optional[str] = None


class RangeStr(BaseModel):
    value: str

    @model_validator(mode="before")
    @classmethod
    def parse_input(cls, data):
        if isinstance(data, str):
            return {"value": data}
        return data

    @field_validator("value")
    @classmethod
    def validate_range(cls, v: str) -> str:
        parts = v.split("-")
        if len(parts) != 2:
            raise ValueError("range must be like 'a-b'")
        try:
            float(parts[0]); float(parts[1])
        except Exception as e:
            raise ValueError("range endpoints must be numeric") from e
        return v

    def as_tuple(self) -> Tuple[float, float]:
        a, b = self.value.split("-")
        return float(a), float(b)


class WidthCfg(BaseModel):
    min: int
    max: int


class SelectorCfg(BaseModel):
    target_dte: RangeStr = Field(default=RangeStr(value="30-60"))
    strike_rule: str = Field(default="by_delta")
    short_delta: Optional[RangeStr] = Field(default=RangeStr(value="0.18-0.25"))
    short_call_delta: Optional[RangeStr] = None
    wing_delta: Optional[RangeStr] = Field(default=None)
    width: Optional[WidthCfg] = None
    iv_rank_min: Optional[int] = None
    min_credit_of_width: Optional[float] = None
    max_debit_of_width: Optional[float] = None
    candidate_top_k: int = 3
    tie_breakers: List[str] = Field(default_factory=lambda: ["spread","oi","dte","round_strike"])
    kinds: List[Literal["CSP", "SPV", "SCV", "LCV", "IC"]] = Field(default_factory=lambda: ["CSP", "SPV"])


class LiquidityGate(BaseModel):
    min_oi: int = 500
    min_volume: int = 100
    max_spread_pct: float = 0.08


class Entry(BaseModel):
    liquidity: LiquidityGate = Field(default_factory=LiquidityGate)


class ExitCredit(BaseModel):
    tp_of_max: List[float] = Field(default_factory=lambda: [0.25, 0.5])
    sl_x_credit: List[float] = Field(default_factory=lambda: [1.5, 2.0])


class ExitPolicy(BaseModel):
    hard_exit_dte_lte: int = 7
    tp_sl: Dict[str, Any] = Field(default_factory=lambda: {"credit": ExitCredit().model_dump()})
    liquidity_fallback_exit: bool = True


class Tolerances(BaseModel):
    dte_snap_days: int = 2
    delta_snap: float = 0.01


class Relaxation(BaseModel):
    dte_steps: List[int] = Field(default_factory=lambda: [5, 10])
    delta_expand_steps: List[float] = Field(default_factory=lambda: [0.02, 0.04])
    width_expand_steps: List[int] = Field(default_factory=lambda: [1, 2])
    min_credit_of_width_floor: float = 0.28
    max_relax_attempts: int = 3


class DeltaTarget(BaseModel):
    put: Optional[RangeStr] = None
    call: Optional[RangeStr] = None


class RollPolicyModel(BaseModel):
    enabled: bool = True
    manage_at_dte_lte: Optional[int] = 21
    roll_to_dte: Optional[RangeStr] = None
    threatened_side_only: bool = True
    keep_width: bool = True
    delta_retarget: Optional[DeltaTarget] = None


class WingPolicyModel(BaseModel):
    enabled: bool = True
    same_expiry: bool = True
    wing_delta: Optional[RangeStr] = None
    max_cost_pct_of_credit: Optional[float] = None
    min_credit_of_width_after_wing: Optional[float] = None


class FillGuardModel(BaseModel):
    max_spread_pct: Optional[float] = None
    min_oi: Optional[int] = None
    min_volume: Optional[int] = None


class ExecutionModel(BaseModel):
    combo_order: bool = True
    price_preference: Literal["mid", "bid", "ask"] = "mid"
    price_offset_bps: int = 5
    fill_guard: Optional[FillGuardModel] = None
    reject_if_guard_fails: bool = True


class FeesModel(BaseModel):
    commission_per_contract: float = 0.65
    exchange_fees_per_contract: float = 0.15
    exercise_assignment_fee: float = 5.0


class SlippageModel(BaseModel):
    model: Literal["mid_bps", "fraction_spread", "fixed"] = "mid_bps"
    mid_bps: int = 5
    fraction_spread: float = 0.25
    fixed_abs: float = 0.01


class DebugModel(BaseModel):
    log_market_data: bool = False
    log_market_data_limit: int = 10
    dump_market_data_csv: bool = False
    market_data_csv_path: Optional[str] = None


class MonitorModel(BaseModel):
    manage_triggers: List[Literal["time", "pnl", "delta", "price_touch", "event", "liquidity"]] = Field(
        default_factory=lambda: ["time", "pnl", "delta", "price_touch", "event", "liquidity"]
    )
    metrics: List[Literal["pnl", "annualized_return", "theta", "margin_usage"]] = Field(
        default_factory=lambda: ["pnl", "annualized_return", "theta", "margin_usage"]
    )


class ConfigModel(BaseModel):
    strategy: StrategyMeta
    selector: Optional[SelectorCfg] = Field(default_factory=SelectorCfg)
    roll_policy: Optional[RollPolicyModel] = None
    exit_policy: ExitPolicy
    wing_policy: Optional[WingPolicyModel] = None
    execution: Optional[ExecutionModel] = None
    fees: Optional[FeesModel] = None
    slippage: Optional[SlippageModel] = None
    entry: Entry = Field(default_factory=Entry)
    monitor: Optional[MonitorModel] = None
    tolerances: Tolerances = Field(default_factory=Tolerances)
    relaxation: Relaxation = Field(default_factory=Relaxation)
    debug: Optional[DebugModel] = None
    profiles: Optional[Dict[str, Any]] = None
    user_overrides: Optional[Dict[str, Any]] = None
    backtest: Optional[Dict[str, Any]] = None  # start/end dates optional


def _fingerprint(obj: Dict[str, Any]) -> str:
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_config(cfg: ConfigModel) -> Dict[str, Any]:
    data = cfg.model_dump()
    # expand ranges
    if cfg.selector:
        td = cfg.selector.target_dte.as_tuple()
        data.setdefault("selector", {})
        data["selector"]["target_dte_tuple"] = td
        if cfg.selector.short_delta:
            data["selector"]["short_delta_tuple"] = cfg.selector.short_delta.as_tuple()
        if cfg.selector.short_call_delta:
            data["selector"]["short_call_delta_tuple"] = cfg.selector.short_call_delta.as_tuple()
        if cfg.selector.wing_delta:
            data["selector"]["wing_delta_tuple"] = cfg.selector.wing_delta.as_tuple()
    if cfg.roll_policy:
        data.setdefault("roll_policy", {})
        if cfg.roll_policy.roll_to_dte:
            data["roll_policy"]["roll_to_dte_tuple"] = cfg.roll_policy.roll_to_dte.as_tuple()
        if cfg.roll_policy.delta_retarget:
            target: Dict[str, Tuple[float, float]] = {}
            if cfg.roll_policy.delta_retarget.put:
                target["put"] = cfg.roll_policy.delta_retarget.put.as_tuple()
            if cfg.roll_policy.delta_retarget.call:
                target["call"] = cfg.roll_policy.delta_retarget.call.as_tuple()
            data["roll_policy"]["delta_retarget_tuple"] = target
    if cfg.wing_policy and cfg.wing_policy.wing_delta:
        data.setdefault("wing_policy", {})
        data["wing_policy"]["wing_delta_tuple"] = cfg.wing_policy.wing_delta.as_tuple()
    # market tz
    data.setdefault("strategy", {})
    data["strategy"]["tz"] = tz_for_market(cfg.strategy.market)
    return data


def _redis_queue(settings: Settings) -> Queue:
    redis = Redis.from_url(settings.redis_url)
    return Queue("backtest", connection=redis)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/strategies/validate")
def validate_strategy(body: Dict[str, Any]):
    try:
        cfg = ConfigModel.model_validate(body)
    except ValidationError as e:
        logger.warning("Config validation failed", exc_info=False)
        raise HTTPException(status_code=400, detail={"errors": json.loads(e.json())})
    normalized = normalize_config(cfg)
    fp = _fingerprint(normalized)
    logger.debug("Config validated", extra={"fingerprint": fp})
    return {"normalized_config": normalized, "warnings": [], "errors": [], "fingerprint": fp}


@app.post("/backtests", status_code=201)
def create_backtest(body: Dict[str, Any], Idempotency_Key: Optional[str] = Header(default=None)):
    settings = load_settings()
    try:
        cfg = ConfigModel.model_validate(body)
    except ValidationError as e:
        logger.warning("Backtest submission validation failed", exc_info=False)
        raise HTTPException(status_code=400, detail={"errors": json.loads(e.json())})
    normalized = normalize_config(cfg)
    run_id = str(uuid.uuid4()) if not Idempotency_Key else str(uuid.uuid5(uuid.NAMESPACE_DNS, Idempotency_Key + _fingerprint(normalized)))

    # Create initial status file
    run_dir = os.path.join(settings.artifacts_root, run_id)
    os.makedirs(run_dir, exist_ok=True)
    status = {"run_id": run_id, "status": "PENDING", "message": "queued"}
    with open(os.path.join(run_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f)

    # enqueue
    q = _redis_queue(settings)
    q.enqueue("worker.runner.run_backtest", run_id, normalized, job_timeout=60*60*6)  # 6h timeout
    logger.info("Backtest queued", extra={"run_id": run_id})

    return {"run_id": run_id, "status": "PENDING"}


@app.get("/backtests/{run_id}")
def get_backtest(run_id: str):
    settings = load_settings()
    run_dir = os.path.join(settings.artifacts_root, run_id)
    status_file = os.path.join(run_dir, "status.json")
    if not os.path.exists(status_file):
        raise HTTPException(status_code=404, detail="run_id not found")
    with open(status_file, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/backtests/{run_id}/artifacts")
def list_artifacts(run_id: str):
    settings = load_settings()
    run_dir = os.path.join(settings.artifacts_root, run_id)
    if not os.path.exists(run_dir):
        raise HTTPException(status_code=404, detail="run_id not found")
    files = []
    for fn in os.listdir(run_dir):
        if os.path.isfile(os.path.join(run_dir, fn)):
            files.append(fn)
    return {"run_id": run_id, "artifacts": sorted(files)}


@app.get("/backtests/{run_id}/artifacts/{filename}")
def get_artifact_file(run_id: str, filename: str):
    settings = load_settings()
    run_dir = os.path.join(settings.artifacts_root, run_id)
    if not os.path.exists(run_dir):
        raise HTTPException(status_code=404, detail="run_id not found")
    target = os.path.abspath(os.path.join(run_dir, filename))
    if not target.startswith(os.path.abspath(run_dir)):
        raise HTTPException(status_code=400, detail="invalid path")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(target)


@app.post("/backtests/{run_id}/cancel")
def cancel_backtest(run_id: str):
    settings = load_settings()
    run_dir = os.path.join(settings.artifacts_root, run_id)
    status_file = os.path.join(run_dir, "status.json")
    if not os.path.exists(status_file):
        raise HTTPException(status_code=404, detail="run_id not found")
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            status = json.load(f)
        status["status"] = "CANCELED"
        status["message"] = "cancel requested"
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status, f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"run_id": run_id, "status": "CANCELED"}


@app.get("/reference/defaults")
def get_defaults():
    # Minimal defaults; align with docs where possible
    return {
        "liquidity": {"min_oi": 500, "min_volume": 100, "max_spread_pct": 0.08},
        "selector": {
            "target_dte": "30-60",
            "short_delta": "0.18-0.25",
            "min_credit_of_width": 0.33,
            "max_debit_of_width": 0.55,
            "kinds": ["CSP", "SPV"],
        },
        "roll_policy": {"manage_at_dte_lte": 21, "roll_to_dte": "30-60"},
        "exit_policy": {"hard_exit_dte_lte": 7, "tp_of_max": [0.25, 0.5], "sl_x_credit": [1.5, 2.0]},
    }


@app.post("/reference/resolve-profile")
def resolve_profile(body: Dict[str, Any]):
    preset = body.get("preset", "balanced")
    overrides = body.get("user_overrides", {})
    base = {
        "strategy": {"id": "csp_spv_us", "mode": "symbol_selector", "market": "US", "symbols": ["AAPL"], "timeframe": "1d"},
        "selector": {
            "target_dte": "30-60",
            "short_delta": "0.18-0.25",
            "width": {"min": 2, "max": 8},
            "min_credit_of_width": 0.33,
            "max_debit_of_width": 0.55,
            "kinds": ["CSP", "SPV"],
        },
        "exit_policy": {"hard_exit_dte_lte": 7, "tp_sl": {"credit": {"tp_of_max": [0.25, 0.5], "sl_x_credit": [1.5, 2.0]}}},
        "entry": {"liquidity": {"min_oi": 500, "min_volume": 100, "max_spread_pct": 0.08}},
    }
    # Very simple preset tweak
    if preset == "conservative":
        base["selector"]["short_delta"] = "0.16-0.22"
    elif preset == "aggressive":
        base["selector"]["short_delta"] = "0.22-0.30"
    elif preset == "call_verticals":
        base["selector"].update({"short_call_delta": "0.22-0.32", "kinds": ["SCV", "LCV"], "min_credit_of_width": 0.30})
    elif preset == "iron_condor":
        base["selector"].update({"short_delta": "0.18-0.28", "kinds": ["IC"], "min_credit_of_width": 0.30})
    # dot-notation overrides
    for k, v in overrides.items():
        cur = base
        parts = k.split(".")
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = v

    try:
        cfg = ConfigModel.model_validate(base)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail={"errors": json.loads(e.json())})
    normalized = normalize_config(cfg)
    fp = _fingerprint(normalized)
    return {"normalized_config": normalized, "warnings": [], "fingerprint": fp}


@app.get("/diagnostics/option-chain")
def diagnostics_option_chain(
    market: str = Query(description="US 或 HK"),
    symbol: str = Query(description="期权symbol，如 AAPL 或 TCH.HK"),
    session_date: str = Query(description="会话日，YYYY-MM-DD，本地市场时区"),
    dte_min: int = 30,
    dte_max: int = 60,
    delta_lo: float = 0.18,
    delta_hi: float = 0.25,
    min_oi: int = 500,
    min_volume: int = 100,
    max_spread_pct: float = 0.08,
    top_k: int = 3,
    kinds: Optional[List[str]] = Query(default=None, description="策略列表，如 CSP,SPV,SCV,LCV,IC，默认 CSP+SPV"),
):
    """诊断：
    - 检查四路数据库连接状态
    - 拉取指定会话日 + DTE 窗口的期权链（原始与筛选后）并统计
    - 计算 CSP/SPV 的 Top-K 候选，输出摘要
    """
    try:
        sdate = _date.fromisoformat(session_date)
    except Exception:
        raise HTTPException(status_code=400, detail="session_date 需要 YYYY-MM-DD 格式")

    settings = load_settings()
    provider = MySQLProvider(DataProviderConfig(
        mysql_stock_dsn=settings.mysql_stock_dsn,
        mysql_option_qs_us_dsn=settings.mysql_option_qs_us_dsn,
        mysql_option_qs_hk_dsn=settings.mysql_option_qs_hk_dsn,
        mysql_option_history_dsn=settings.mysql_option_history_dsn,
    ))
    conn = provider.connectivity()

    # 原始链（不加 Δ/流动性筛）
    raw = provider.load_option_chain_daily(
        opt_symbol=symbol,
        session_local_date=sdate,
        market=market,
        dte_window=(dte_min, dte_max),
        liquidity=None,
        delta_range=None,
    )
    raw_stats = {
        "total": len(raw),
        "puts": sum(1 for r in raw if r.right == "put"),
        "calls": sum(1 for r in raw if r.right == "call"),
        "distinct_expiries": sorted(list({str(r.expiry_local_date) for r in raw})),
        "dte_range": [min((r.dte for r in raw), default=None), max((r.dte for r in raw), default=None)],
        "missing_iv": sum(1 for r in raw if r.iv is None),
        "missing_delta": sum(1 for r in raw if r.delta is None),
    }

    # 筛选后（Δ/流动性）
    gate = LiquidityGate(min_oi=min_oi, min_volume=min_volume, max_spread_pct=max_spread_pct)
    filt = provider.load_option_chain_daily(
        opt_symbol=symbol,
        session_local_date=sdate,
        market=market,
        dte_window=(dte_min, dte_max),
        liquidity=gate,
        delta_range=(delta_lo, delta_hi),
    )
    filt_stats = {
        "total": len(filt),
        "puts": sum(1 for r in filt if r.right == "put"),
        "calls": sum(1 for r in filt if r.right == "call"),
        "distinct_expiries": sorted(list({str(r.expiry_local_date) for r in filt})),
        "dte_range": [min((r.dte for r in filt), default=None), max((r.dte for r in filt), default=None)],
    }

    # 候选（Top-K）
    selector_kinds = kinds or ["CSP", "SPV"]
    selector_kinds = [k.upper() for k in selector_kinds]
    width_min, width_max = 2, 8
    csp_top = spv_top = scv_top = lcv_top = ic_top = []
    if "CSP" in selector_kinds:
        csp_top = [c.__dict__ for c in csp_candidates(provider, symbol, sdate, market, (dte_min, dte_max), (delta_lo, delta_hi), gate, top_k=top_k)]
    if "SPV" in selector_kinds:
        spv_top = [c.__dict__ for c in spv_candidates(provider, symbol, sdate, market, (dte_min, dte_max), (delta_lo, delta_hi), (width_min, width_max), 0.33, gate, top_k=top_k)]
    if "SCV" in selector_kinds:
        scv_top = [c.__dict__ for c in scv_candidates(provider, symbol, sdate, market, (dte_min, dte_max), (delta_lo, delta_hi), (width_min, width_max), 0.30, gate, top_k=top_k)]
    if "LCV" in selector_kinds:
        lcv_top = [c.__dict__ for c in lcv_candidates(provider, symbol, sdate, market, (dte_min, dte_max), (delta_lo, delta_hi), (width_min, width_max), 0.55, gate, top_k=top_k)]
    if "IC" in selector_kinds:
        ic_top = [c.__dict__ for c in ic_candidates(provider, symbol, sdate, market, (dte_min, dte_max), (delta_lo, delta_hi), (width_min, width_max), 0.30, gate, top_k=top_k)]

    return {
        "connectivity": conn,
        "params": {
            "market": market,
            "symbol": symbol,
            "session_date": session_date,
            "dte_window": [dte_min, dte_max],
            "delta_range": [delta_lo, delta_hi],
            "liquidity": gate.__dict__,
            "kinds": selector_kinds,
        },
        "chain_raw": raw_stats,
        "chain_filtered": filt_stats,
        "csp_top": csp_top,
        "spv_top": spv_top,
        "scv_top": scv_top,
        "lcv_top": lcv_top,
        "ic_top": ic_top,
    }
