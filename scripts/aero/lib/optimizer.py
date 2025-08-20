#!/usr/bin/env python3
# filepath: d:\Pyth\pythfarms\scripts\aero\lib\optimizer.py
import os
import json
import logging
from decimal import Decimal, getcontext, ROUND_HALF_UP

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Increase precision for allocation math
getcontext().prec = 50

# Constants
TOL = Decimal("1e-12")
MAX_ITERS = 100
TOP_N = 6  # For display
TOTAL_WEIGHT_TARGET = Decimal(100) * (Decimal(10) ** 18)  # sum weights to 100e18

# Paths
DASHBOARD_PATH = "input_data/aero/votes_dashboard.json"
HUMAN_OUT_PATH = "optimized_votes/aero/optimized_votes_human.json"
BOT_OUT_PATH = "optimized_votes/aero/optimized_votes_bot.txt"
CALLDATA_OUT_PATH = "optimized_votes/aero/optimized_votes_calldata.json"

def load_json(path):
    """Load a JSON file or exit if missing"""
    if not os.path.exists(path):
        logger.error(f"❌ {path} not found.")
        return None
    with open(path) as f:
        return json.load(f)

def save_json(data, path):
    """Save data to a JSON file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"✅ Saved data to {path}")

def save_text(text, path):
    """Save text to a file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    logger.info(f"✅ Saved text to {path}")

def equal_marginal(RW, P):
    """
    Equal-marginal solver: maximize sum R_i * Δ_i/(W_i+Δ_i) subject to sum Δ_i = P
    
    Args:
        RW: List of tuples (pool_addr, reward, weight)
        P: Total voting power to allocate
    
    Returns:
        List of tuples (pool_addr, allocation)
    """
    active = [(p, R, W) for (p, R, W) in RW if R > 0 and W >= 0]
    if not active:
        return [(p, Decimal(0)) for (p, _, _) in RW]
    
    def sum_delta(lam):
        s = Decimal(0)
        for _, R, W in active:
            num = R * W
            if num <= 0:
                continue
            d = (num / lam).sqrt() - W
            if d > 0:
                s += d
        return s
    
    # bracket λ so sum_delta(hi) < P
    lo, hi = Decimal("1e-30"), Decimal("1")
    for _ in range(200):
        if sum_delta(hi) < P:
            break
        hi *= 2
    else:
        raise RuntimeError("Could not bracket lambda for equal-marginal")
    
    # binary search for λ
    for _ in range(MAX_ITERS):
        mid = (lo + hi) / 2
        s = sum_delta(mid)
        if abs(s - P) < TOL:
            lo = mid
            break
        if s > P:
            lo = mid
        else:
            hi = mid
    lam = lo
    
    # compute Δ_i for each pool
    out = []
    for p, R, W in RW:
        if R <= 0 or W < 0:
            out.append((p, Decimal(0)))
        else:
            d = ((R * W) / lam).sqrt() - W
            out.append((p, d if d > 0 else Decimal(0)))
    return out

def run_optimization(dashboard):
    """
    Run the optimization algorithm
    
    Args:
        dashboard: The votes dashboard with pool data
    
    Returns:
        Tuple of (result_dict, bot_output_string)
    """
    pools = dashboard["pools"]
    P_our = Decimal(str(dashboard.get("our_voting_power", 0)))
    already_cast = sum(Decimal(str(p.get("our_votes", 0))) for p in pools)
    P_rem = max(P_our - already_cast, Decimal(0))
    
    logger.info(f"Our voting power: {P_our}")
    logger.info(f"Already cast: {already_cast}")
    logger.info(f"Remaining to allocate: {P_rem}")
    
    # Build baseline weights (on-chain)
    base = []
    for p in pools:
        addr = p["pool"].lower()
        R = Decimal(str(p.get("total_usd", 0)))
        W = Decimal(str(p.get("weight", 0)))
        base.append((addr, R, W))
    
    # Check if we have voting power to allocate
    if P_rem <= 0:
        logger.error("❌ No voting power available to allocate. Exiting optimization.")
        return {"total_expected_usd": 0, "allocations": []}, ""
    
    # Allocate our remaining votes across pools
    logger.info(f"Allocating {P_rem} votes based on equal-marginal algorithm...")
    alloc = equal_marginal(base, P_rem)
    total_alloc = sum(d for _, d in alloc)
    
    # Prepare outputs
    human = []
    bot_lines = []
    
    for addr, d in alloc:
        if d <= 0:
            continue
        p = next((x for x in pools if x["pool"].lower() == addr), None)
        if not p:
            continue
            
        sym = p.get("symbol", "")
        pct = (d / total_alloc * Decimal(100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        total_usd_dec = Decimal(str(p.get("total_usd", 0)))
        fraction = (d / (W + d)) if (W + d) > 0 else Decimal(0)
        exp_usd_dec = (total_usd_dec * fraction).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        exp_usd = float(exp_usd_dec)
        
        human.append({
            "symbol": sym,
            "pool": addr,
            "votes": float(d),
            "pct": int(pct),
            "exp_usd": exp_usd
        })
        
        # Scale to 100e18 total for bot output
        weight_i = (d / P_rem * TOTAL_WEIGHT_TARGET).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        bot_lines.append(f"{addr} {int(weight_i)}")
    
    # Compute total expected USD return
    total_exp_usd = sum(item['exp_usd'] for item in human)
    
    # Sort by percentage
    human.sort(key=lambda x: x['pct'], reverse=True)
    
    # Assemble output with total at top
    human_output = {
        "total_expected_usd": round(total_exp_usd, 2),
        "allocations": human
    }
    
    bot_output = "\n".join(bot_lines)
    
    return human_output, bot_output

def run_optimize(save=True):
    """
    Main entry point for optimizing votes
    
    Args:
        save: Whether to save results to file (True) or display them (False)
    """
    logger.info("Starting vote optimization")
    
    # Load dashboard
    dashboard = load_json(DASHBOARD_PATH)
    if not dashboard:
        return None
    
    # Run optimization
    result, bot_output = run_optimization(dashboard)
    
    # Save or display results
    if save:
        save_json(result, HUMAN_OUT_PATH)
        save_text(bot_output, BOT_OUT_PATH)
        logger.info(f"✅ Total expected USD return: ${result['total_expected_usd']:.2f}")
        logger.info(f"✅ Allocated votes across {len(result['allocations'])} pools")
    else:
        print("\n================ OPTIMIZATION RESULTS ================")
        print(f"Total Expected USD: ${result['total_expected_usd']:.2f}")
        print("------------------------------------------------------")
        print("Pool                                    Votes    Exp USD")
        print("------------------------------------------------------")
        for alloc in result["allocations"][:TOP_N]:
            symbol = alloc.get("symbol", "").ljust(10)
            votes = f"{alloc.get('votes', 0):.2f}".rjust(8)
            exp_usd = f"${alloc.get('exp_usd', 0):.2f}".rjust(8)
            print(f"{symbol} ({alloc.get('pool')[:10]}...) {votes} {exp_usd}")
        if len(result["allocations"]) > TOP_N:
            print(f"... and {len(result['allocations']) - TOP_N} more pools")
        print("======================================================\n")
    
    return result