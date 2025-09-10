#!/usr/bin/env python3
import os
import json
import logging
from decimal import Decimal, getcontext, ROUND_HALF_UP
from dotenv import load_dotenv
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

getcontext().prec = 50

# Constants
TOL = Decimal("1e-12")
MAX_ITERS = 100

load_dotenv()

def load_json(path):
    """Load a JSON file from the given path."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found.")
    with open(path) as f:
        return json.load(f)

def equal_marginal_lp(pools_data, investment_amount):
    """
    Equal marginal utility optimization algorithm for LP positions.
    
    Args:
        pools_data: List of tuples (pool_address, APR, TVL)
        investment_amount: Total investment amount in USD
        
    Returns:
        List of tuples (pool_address, allocation_amount)
    """
    # Filter out pools with zero or negative APR or TVL
    active = [(p, apr, tvl) for (p, apr, tvl) in pools_data if apr > 0 and tvl > 0]
    
    if not active:
        return [(p, Decimal(0)) for (p, _, _) in pools_data]

    def sum_delta(lam):
        total = Decimal(0)
        for _, apr, tvl in active:
            # APR diminishes as our investment increases TVL
            # The formula below models diminishing returns
            num = apr * tvl
            if num <= 0:
                continue
            d = (num / lam).sqrt() - tvl
            if d > 0:
                total += d
        return total

    # Binary search to find the optimal lambda
    lo, hi = Decimal("1e-30"), Decimal("1")
    for _ in range(200):
        if sum_delta(hi) < investment_amount:
            break
        hi *= 2
    else:
        raise RuntimeError("Could not bracket lambda for equal-marginal LP")

    for _ in range(MAX_ITERS):
        mid = (lo + hi) / 2
        s = sum_delta(mid)
        if abs(s - investment_amount) < TOL:
            lam = mid
            break
        if s > investment_amount:
            lo = mid
        else:
            hi = mid
    else:
        lam = lo

    # Calculate allocations
    out = []
    for p, apr, tvl in pools_data:
        if apr <= 0 or tvl <= 0:
            out.append((p, Decimal(0)))
        else:
            # Calculate optimal allocation based on equal marginal principle
            d = ((apr * tvl) / lam).sqrt() - tvl
            out.append((p, d if d > 0 else Decimal(0)))
    return out

def run_lp_optimization(dashboard, investment_amount, top_n_pools=None):
    """
    Run the LP optimization algorithm on a dashboard with given investment amount.
    
    Args:
        dashboard: The votes dashboard with pool data
        investment_amount: Decimal value of available investment in USD
        top_n_pools: If provided, only consider the top N pools by TVL
        
    Returns:
        Dictionary with optimization results
    """
    pools = dashboard.get("pools", [])
    
    # Filter out pools without TVL or APR data
    valid_pools = []
    for p in pools:
        if not isinstance(p, dict) or 'pool' not in p:
            continue
            
        if 'tvl' in p and 'lp_apr' in p and p['tvl'] > 0:
            valid_pools.append(p)
    
    # Filter for top N pools by TVL if specified
    if top_n_pools and isinstance(top_n_pools, int) and top_n_pools > 0:
        valid_pools.sort(key=lambda x: x.get('tvl', 0), reverse=True)
        valid_pools = valid_pools[:top_n_pools]
        logger.info(f"ℹ️ Filtered to top {top_n_pools} pools by TVL")
    
    logger.info(f"ℹ️ Found {len(valid_pools)} valid pools with TVL and APR data")
    logger.info(f"ℹ️ Allocating ${investment_amount} for LP positions")
    
    # Prepare data for optimization
    pools_data = []
    for p in valid_pools:
        addr = p["pool"].lower()
        
        # Calculate APR based on annualized 7-day fees instead of using lp_apr
        tvl = Decimal(str(p.get("tvl", 0)))
        last_7d_fees = Decimal(str(p.get("stats", {}).get("last_7d_fees", 0)))
        
        # Annualize: 52 weeks in a year
        annualized_fees = last_7d_fees * 52
        
        # Calculate APR: (annualized_fees / tvl) * 100
        calculated_apr = (annualized_fees / tvl * 100) if tvl > 0 else Decimal(0)
        
        pools_data.append((addr, calculated_apr, tvl))
    
    # Run optimization
    alloc = equal_marginal_lp(pools_data, investment_amount)
    total_alloc = sum(d for _, d in alloc)
    
    if total_alloc <= 0:
        logger.warning("⚠️ Total allocation is zero. Check pool data.")
        return {"total_investment": 0, "allocations": [], "period": dashboard.get("period")}
    
    # Build output
    allocations = []
    expected_return = Decimal(0)
    
    for addr, amount in alloc:
        if amount <= 0:
            continue
            
        p = next((x for x in valid_pools if x["pool"].lower() == addr), None)
        if not p:
            continue
            
        pct = (amount / total_alloc * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        # Calculate expected return
        original_tvl = Decimal(str(p.get("tvl", 0)))
        
        # Calculate rewards APR based on last 7 days fees
        last_7d_fees = Decimal(str(p.get("stats", {}).get("last_7d_fees", 0)))
        annualized_fees = last_7d_fees * 52
        annualized_rewards_apr = (annualized_fees / original_tvl * 100) if original_tvl > 0 else Decimal(0)
        
        # Adjust APR for the new TVL (simplified model)
        # In reality, APR might decrease with higher TVL due to fee dilution
        new_tvl = original_tvl + amount
        adjusted_rewards_apr = annualized_rewards_apr * (original_tvl / new_tvl) if new_tvl > 0 else Decimal(0)
        
        # Expected annual return in USD
        exp_annual_return = amount * adjusted_rewards_apr / 100
        
        # Calculate weekly return (annual / 52)
        exp_weekly_return = exp_annual_return / 52
        
        expected_return += exp_annual_return
        
        allocations.append({
            "symbol": p.get("symbol", ""),
            "pool": addr,
            "amount": float(amount),
            "pct": float(pct),
            "tvl": float(original_tvl),  # Include the original TVL
            "original_lp_apr": float(p.get("lp_apr", 0)),  # Keep the original lp_apr for reference
            "annualized_rewards_apr": float(annualized_rewards_apr),
            "adjusted_rewards_apr": float(adjusted_rewards_apr),
            "expected_annual_return": float(exp_annual_return),
            "expected_weekly_return": float(exp_weekly_return),
            "last_7d_fees": float(last_7d_fees)
        })
    
    # Sort by allocation percentage
    allocations.sort(key=lambda x: x['pct'], reverse=True)
    
    # Calculate weekly return
    weekly_return_total = expected_return / 52
    
    result = {
        "total_investment": float(total_alloc),
        "total_expected_annual_return": float(expected_return),
        "total_expected_weekly_return": float(weekly_return_total),
        "expected_portfolio_apr": float(expected_return / total_alloc * 100) if total_alloc > 0 else 0,
        "allocations": allocations,
        "period": dashboard.get("period")
    }
    
    return result

def save_lp_optimization(result):
    """
    Save LP optimization results to a file.

    Args:
        result: Optimization result dict
    """
    period = result.get("period")
    date_str = datetime.now().strftime('%Y%m%d')
    
    # Save to period-specific and standard locations
    filepath = f'optimized_lp/shadow/{period}_optimized_lp_{date_str}.json'
    std_filepath = 'optimized_lp/shadow/optimized_lp.json'
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    with open(filepath, 'w') as f:
        json.dump(result, f, indent=2)
        
    with open(std_filepath, 'w') as f:
        json.dump(result, f, indent=2)
    
    logger.info(f"✅ Saved LP optimization results to {filepath} and {std_filepath}")
    return filepath

def display_lp_optimization(result):
    """Display LP optimization results in a readable format."""
    if not result:
        return
    
    print("\n================ LP OPTIMIZATION RESULTS ================")
    print(f"Total Investment: ${result['total_investment']:.2f}")
    print(f"Expected Annual Return: ${result['total_expected_annual_return']:.2f}")
    print(f"Expected Weekly Return: ${result['total_expected_weekly_return']:.2f}")
    print(f"Expected Portfolio APR: {result['expected_portfolio_apr']:.2f}%")
    print("----------------------------------------------------------")
    print("Pool                  Amount ($)    %      TVL     7d Fees  Ann. APR  Adj. APR  Weekly Return  Annual Return")
    print("----------------------------------------------------------")
    
    for alloc in result["allocations"]:
        symbol = alloc.get("symbol", "").ljust(10)
        amount = f"${alloc.get('amount', 0):.2f}".rjust(10)
        pct = f"{alloc.get('pct', 0):.2f}%".rjust(6)
        tvl = f"${alloc.get('tvl', 0):.0f}".rjust(8)
        fees_7d = f"${alloc.get('last_7d_fees', 0):.2f}".rjust(8)
        ann_apr = f"{alloc.get('annualized_rewards_apr', 0):.2f}%".rjust(8)
        adj_apr = f"{alloc.get('adjusted_rewards_apr', 0):.2f}%".rjust(8)
        weekly_ret = f"${alloc.get('expected_weekly_return', 0):.2f}".rjust(10)
        annual_ret = f"${alloc.get('expected_annual_return', 0):.2f}".rjust(10)
        print(f"{symbol} ({alloc.get('pool')[:8]}...) {amount} {pct} {tvl} {fees_7d} {ann_apr} {adj_apr} {weekly_ret} {annual_ret}")
    
    print("==========================================================\n")

def run_lp_optimize(dashboard_path=None, investment_amount=None, save=True, display=True, top_n_pools=None):
    """
    Main entry point for running the LP optimizer.
    
    Args:
        dashboard_path: Path to the votes dashboard file
        investment_amount: Amount to invest in USD
        save: Whether to save results to file
        display: Whether to display results
        top_n_pools: If provided, only consider the top N pools by TVL
        
    Returns:
        Optimization result dict
    """
    # Prompt for dashboard path if not provided
    if not dashboard_path:
        dashboard_path = input("Enter path to dashboard file (e.g., input_data/shadow/votes_dashboard.json): ")
    
    # Load dashboard
    try:
        dashboard = load_json(dashboard_path)
        logger.info(f"Loaded dashboard for period {dashboard.get('period')}")
    except Exception as e:
        logger.error(f"❌ Failed to load dashboard: {e}")
        return None
    
    # Prompt for investment amount if not provided
    if not investment_amount:
        try:
            amount_str = input("Enter investment amount in USD: ")
            investment_amount = Decimal(amount_str)
        except ValueError:
            logger.error("❌ Invalid amount. Please enter a numeric value.")
            return None
    else:
        investment_amount = Decimal(str(investment_amount))
    
    # Run optimization
    result = run_lp_optimization(dashboard, investment_amount, top_n_pools)
    
    # Save results if requested
    if save:
        save_lp_optimization(result)
    
    # Display results if requested
    if display:
        display_lp_optimization(result)
    
    return result
