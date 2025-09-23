#!/usr/bin/env python3
# filepath: d:\Pyth\pythfarms\scripts\aero\lib\lp_optimized.py

import os
import json
import logging
import datetime
import argparse
from decimal import Decimal, getcontext
from web3 import Web3
from dotenv import load_dotenv

from .optimizer import equal_marginal

# Set precision for decimal calculations
getcontext().prec = 28

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Constants
RPC_URL = os.getenv("RPC_URL")
VE_ADDRESS = os.getenv("VE_ADDRESS", "0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4")
VOTER_ADDRESS = os.getenv("VOTER_ADDRESS")
VOTES_DASHBOARD_DEFAULT = "input_data/aero/votes_dashboard.json"
LP_DASHBOARD_DEFAULT = "lp_dashboard/aero/lp_dashboard.json"
OUTPUT_PATH = "lp_optimized/aero/optimized_lp.json"

# ABI
VE_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]

def get_web3():
    """Initialize and return a Web3 instance"""
    if not RPC_URL:
        logger.error("❌ RPC_URL not set in environment")
        return None
    
    return Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))

def save_json(data, path):
    """Save data to a JSON file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"✅ Saved data to {path}")

def load_json(path):
    """Load a JSON file"""
    if not os.path.exists(path):
        logger.error(f"❌ {path} not found.")
        return None
    with open(path) as f:
        return json.load(f)

def get_total_vote_power():
    """
    Get total vote power from the veAERO contract's totalSupply
    """
    w3 = get_web3()
    if not w3:
        return Decimal("0")
    
    # Get total from VE contract
    try:
        ve_contract = w3.eth.contract(address=w3.to_checksum_address(VE_ADDRESS), abi=VE_ABI)
        total_supply = ve_contract.functions.totalSupply().call()
        total_supply_decimal = Decimal(total_supply) / Decimal(10**18)
        logger.info(f"📊 Total vote power from VE contract: {total_supply_decimal:,.2f}")
        
        return total_supply_decimal
    except Exception as e:
        logger.error(f"❌ Failed to get total vote power: {e}")
        return Decimal("0")

def calculate_optimal_votes(vote_data, lp_data):
    """
    Calculate optimal vote allocation based on bribes
    
    Args:
        vote_data: The votes dashboard data
        lp_data: The LP dashboard data
        
    Returns:
        Dictionary mapping pool addresses to optimal vote allocations
    """
    # Get total potential voting power from VE contract
    total_vote_power = get_total_vote_power()
    if total_vote_power == 0:
        logger.error("❌ Failed to get total vote power")
        return {}
    
    # Get current on-chain votes from pool_summed_weights (direct sum of on-chain weights)
    # This is already calculated in fetch_votes.py and excludes relay votes
    current_on_chain_votes = Decimal(str(vote_data.get("pool_summed_weights", 0)))
    
    # If pool_summed_weights doesn't exist (backward compatibility), calculate it from pools
    if current_on_chain_votes == 0:
        pool_weights_sum = sum(Decimal(str(pool.get("weight", 0))) for pool in vote_data.get("pools", []))
        current_on_chain_votes = pool_weights_sum
        logger.info(f"📊 Using calculated sum of pool weights: {pool_weights_sum:,.2f}")
    else:
        pool_weights_sum = current_on_chain_votes
        logger.info(f"📊 Using pool_summed_weights from dashboard: {current_on_chain_votes:,.2f}")
    
    logger.info(f"📊 Current on-chain votes: {current_on_chain_votes:,.2f}")
    logger.info(f"📊 Total vote power from VE contract: {total_vote_power:,.2f}")
    
    # Calculate votes to be optimized - only consider on-chain votes
    votes_to_optimize = total_vote_power - current_on_chain_votes
    
    # Handle case where current votes exceed total supply
    if votes_to_optimize <= 0:
        logger.warning("⚠️ Current on-chain votes exceed or equal total vote power from VE contract")
        logger.warning("⚠️ No remaining votes to optimize, using current vote distribution")
        votes_to_optimize = Decimal("0")
    
    logger.info(f"📊 Votes to be optimized: {votes_to_optimize:,.2f}")
    
    # Create a pool mapping for quick lookup
    pool_bribes = {}
    pool_weights = {}
    
    # Get pools with bribes
    for pool in vote_data.get("pools", []):
        pool_addr = pool.get("pool", "").lower()
        if pool_addr:
            # Use total_bribes_fees_usd (bribes + fees) as the reward (updated field name)
            reward = Decimal(str(pool.get("total_bribes_fees_usd", 0)))
            # Current on-chain weight only (ignore relay votes)
            weight = Decimal(str(pool.get("weight", 0)))
            
            pool_bribes[pool_addr] = reward
            pool_weights[pool_addr] = weight
    
    # If there are no votes to optimize, return the current weights
    if votes_to_optimize <= 0:
        logger.info("Using current vote distribution since there are no votes to optimize")
        return {addr: weight for addr, weight in pool_weights.items() if weight > 0}
    
    # Prepare input for equal marginal optimizer
    rw_pairs = [(addr, reward, pool_weights.get(addr, Decimal("0"))) 
                for addr, reward in pool_bribes.items() if reward > 0]
    
    # Run equal marginal optimization
    logger.info(f"🧮 Running equal marginal optimization for {len(rw_pairs)} pools...")
    logger.info(f"🔒 Preserving all current votes and optimizing only the remaining {votes_to_optimize:,.2f} votes")
    optimal_allocations = equal_marginal(rw_pairs, votes_to_optimize)
    
    # Create a map of optimal allocations
    optimal_votes = {}
    
    # First, preserve all current votes (even for pools not selected for optimization)
    for addr, weight in pool_weights.items():
        if weight > 0:
            optimal_votes[addr.lower()] = weight
    
    # Then add the new allocations on top
    for addr, allocation in optimal_allocations:
        if allocation > 0:
            addr_lower = addr.lower()
            current_weight = optimal_votes.get(addr_lower, Decimal("0"))
            total_weight = current_weight + allocation
            optimal_votes[addr_lower] = total_weight
    
    logger.info(f"✅ Optimized votes for {len(optimal_votes)} pools")
    return optimal_votes

def create_optimized_lp_dashboard(votes_path, lp_path, output_path):
    """
    Create an optimized LP dashboard based on optimal vote allocation
    
    Args:
        votes_path: Path to the votes dashboard JSON
        lp_path: Path to the LP dashboard JSON
        output_path: Path to save the optimized LP dashboard
        
    Returns:
        The optimized LP dashboard data
    """
    # Load votes dashboard
    logger.info(f"📂 Loading votes dashboard from {votes_path}...")
    votes_data = load_json(votes_path)
    if not votes_data:
        return None
    
    # Load LP dashboard
    logger.info(f"📂 Loading LP dashboard from {lp_path}...")
    lp_data = load_json(lp_path)
    if not lp_data:
        return None
    
    # Calculate optimal vote allocation
    optimal_votes = calculate_optimal_votes(votes_data, lp_data)
    
    # Get total potential voting power from VE contract
    total_vote_power = get_total_vote_power()
    
    # Get current on-chain votes from pool_summed_weights in votes dashboard
    current_on_chain_votes = Decimal(str(votes_data.get("pool_summed_weights", 0)))
    
    # If pool_summed_weights doesn't exist (backward compatibility), calculate it from pools
    if current_on_chain_votes == 0:
        pool_weights_sum = sum(Decimal(str(pool.get("weight", 0))) for pool in votes_data.get("pools", []))
        current_on_chain_votes = pool_weights_sum
        logger.info(f"📊 Using calculated sum of pool weights: {pool_weights_sum:,.2f}")
    else:
        pool_weights_sum = current_on_chain_votes
        logger.info(f"📊 Using pool_summed_weights from dashboard: {current_on_chain_votes:,.2f}")
    
    # Get weekly emissions data from original LP dashboard
    weekly_emissions = lp_data.get("weekly_emissions", 0)
    weekly_emissions_usd = lp_data.get("weekly_emissions_usd", 0)
    
    # If weekly emissions data isn't in the LP dashboard, calculate it from period data
    if weekly_emissions == 0 or weekly_emissions_usd == 0:
        # Import necessary functions
        from .fetch_lp_data import get_weekly_emissions, get_aero_price
        
        # Get weekly emissions and price
        weekly_emissions = float(get_weekly_emissions())
        aero_price = float(get_aero_price())
        weekly_emissions_usd = weekly_emissions * aero_price
        
        logger.info(f"ℹ️ Weekly emissions: {weekly_emissions} AERO (${weekly_emissions_usd})")
    
    # Create a deep copy of the LP dashboard
    optimized_dashboard = {
        "timestamp": int(datetime.datetime.now().timestamp()),
        "date": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "epoch": lp_data.get("epoch", 0),
        "is_optimized": True,
        "weekly_emissions": weekly_emissions,
        "weekly_emissions_usd": weekly_emissions_usd,
        "total_vote_power": float(total_vote_power),
        "current_on_chain_votes": float(current_on_chain_votes),
        "pool_weights_sum": float(pool_weights_sum),
        "votes_to_optimize": float(total_vote_power - current_on_chain_votes),
        "implied_weights_optimized": {addr: float(weight) for addr, weight in optimal_votes.items()},
        "pools": []
    }
    
        # Copy the pools and update APRs based on optimized votes
    for pool in lp_data.get("pools", []):
        new_pool = pool.copy()
        pool_addr = pool.get("lp", "").lower()  # Using lp instead of pool field
        
        # Get optimized votes for this pool
        optimized_vote = optimal_votes.get(pool_addr, Decimal("0"))
        
        # Calculate new APR based on optimized votes
        tvl_usd = Decimal(str(pool.get("tvl_usd", 0)))
        if tvl_usd > 0 and total_vote_power > 0 and weekly_emissions_usd > 0:
            # Calculate pool's share of emissions based on optimized votes only
            # (ignoring relay votes in both the numerator and denominator)
            vote_share = optimized_vote / total_vote_power
            pool_emissions = vote_share * Decimal(str(weekly_emissions))
            pool_emissions_usd = vote_share * Decimal(str(weekly_emissions_usd))
            
            # Calculate APR (annualized - 52 weeks)
            apr = (pool_emissions_usd * 52) / tvl_usd * 100
            
            # Update pool data with weight information
            # Using total_pool_weight instead of weight field
            original_weight = float(pool.get("total_pool_weight", 0))
            optimized_weight = float(optimized_vote)
            
            # Ensure we never use a negative difference due to data inconsistencies
            weight_diff = max(0, optimized_weight - original_weight)
            
            new_pool["implied_weight_optimized"] = optimized_weight  # Renamed from optimized_weight
            new_pool["original_weight"] = original_weight
            new_pool["weight_diff"] = weight_diff
            new_pool["weight_pct"] = float(vote_share * 100)
            new_pool["apr"] = float(apr)
            new_pool["rewards"] = float(pool_emissions_usd)
            
            # Add investment-specific APRs if they exist in the original
            if "investment_apr" in pool or "apr_by_investment" in pool:
                new_pool["apr_by_investment"] = {}
                investment_sizes = [1000, 10000, 50000]  # Default sizes
                
                # Check if we have custom investment sizes from the original
                if "apr_by_investment" in pool:
                    investment_sizes = [int(size) for size in pool.get("apr_by_investment", {}).keys() if size.isdigit()]
                
                for size in investment_sizes:
                    # Calculate new investment-specific APR
                    invest_apr = calculate_investment_apr(
                        float(pool_emissions_usd), size, pool
                    )
                    new_pool["apr_by_investment"][str(size)] = invest_apr
        
        optimized_dashboard["pools"].append(new_pool)
    
    # Sort pools by TVL (descending) instead of APR
    optimized_dashboard["pools"].sort(key=lambda x: x.get("tvl_usd", 0), reverse=True)
    
    # Save optimized dashboard
    logger.info(f"💾 Saving optimized LP dashboard to {output_path}...")
    save_json(optimized_dashboard, output_path)
    
    return optimized_dashboard

def calculate_investment_apr(rewards_usd, investment_amount, pool):
    """Calculate APR for a specific investment amount"""
    try:
        # Extract pool data
        tvl_usd = pool.get("tvl_usd", 0)
        token0_price = pool.get("token0_price", 0)
        token1_price = pool.get("token1_price", 0)
        reserve0_human = pool.get("reserve0_human", 0)
        reserve1_human = pool.get("reserve1_human", 0)
        
        if tvl_usd <= 0 or token0_price <= 0 or token1_price <= 0:
            return 0
        
        # Calculate investment percentage of TVL
        investment_pct = investment_amount / tvl_usd
        
        # Calculate investment's share of rewards
        investment_rewards = rewards_usd * investment_pct
        
        # Annualized APR
        apr = (investment_rewards * 52) / investment_amount * 100
        return apr
    except Exception as e:
        logger.error(f"Error calculating investment APR: {e}")
        return 0

def display_optimized_dashboard(dashboard, top_n=30):
    """Display the optimized LP dashboard in the terminal"""
    if not dashboard:
        logger.error("❌ No dashboard data to display")
        return
    
    pools = dashboard.get("pools", [])
    if not pools:
        logger.error("❌ No pools in dashboard")
        return
    
    weekly_emissions = dashboard.get("weekly_emissions", 0)
    weekly_emissions_usd = dashboard.get("weekly_emissions_usd", 0)
    total_vote_power = dashboard.get("total_vote_power", 0)
    current_on_chain_votes = dashboard.get("current_on_chain_votes", 0)
    pool_weights_sum = dashboard.get("pool_weights_sum", 0)
    votes_to_optimize = dashboard.get("votes_to_optimize", 0)
    
    logger.info(f"\n===== OPTIMIZED LP DASHBOARD =====")
    logger.info(f"Weekly emissions: {weekly_emissions} AERO (${weekly_emissions_usd:,.2f})")
    logger.info(f"Total vote power (VE contract): {total_vote_power:,.2f}")
    logger.info(f"Current on-chain votes (dashboard): {current_on_chain_votes:,.2f}")
    logger.info(f"Sum of pool weights (dashboard): {pool_weights_sum:,.2f}")
    logger.info(f"Votes available to optimize: {votes_to_optimize:,.2f}")
    logger.info(f"Top {min(top_n, len(pools))} pools by APR (optimized allocation):")
    logger.info(f"----------------------------------------------------------------------------------")
    
    # Format headers
    headers = f"{'#':3} {'Pool':20} {'TVL':>12} {'APR':>8} {'Optimized':>10} {'Original':>10} {'Added':>10} {'%':>6}"
    logger.info(headers)
    logger.info(f"----------------------------------------------------------------------------------")
    
    for i, pool in enumerate(pools[:top_n]):
        symbol = pool.get('symbol', 'Unknown')[:18]
        apr = pool.get('apr', 0)
        tvl = pool.get('tvl_usd', 0)
        optimized_weight = pool.get('implied_weight_optimized', 0)
        original_weight = pool.get('original_weight', 0)
        weight_diff = pool.get('weight_diff', 0)
        weight_pct = pool.get('weight_pct', 0)
        
        # Format TVL for display
        if tvl >= 1_000_000:
            tvl_display = f"${tvl/1_000_000:.2f}M"
        else:
            tvl_display = f"${tvl/1_000:.2f}K"
            
        # Format weights with K/M suffix for readability
        if optimized_weight >= 1_000_000:
            optimized_display = f"{optimized_weight/1_000_000:.2f}M"
        else:
            optimized_display = f"{optimized_weight/1_000:.2f}K"
            
        if original_weight >= 1_000_000:
            original_display = f"{original_weight/1_000_000:.2f}M"
        else:
            original_display = f"{original_weight/1_000:.2f}K"
            
        if abs(weight_diff) >= 1_000_000:
            diff_display = f"{weight_diff/1_000_000:+.2f}M"
        else:
            diff_display = f"{weight_diff/1_000:+.2f}K"
            
        row = f"{i+1:3} {symbol:20} {tvl_display:>12} {apr:>7.2f}% {optimized_display:>10} {original_display:>10} {diff_display:>10} {weight_pct:>5.2f}%"
        logger.info(row)

def run_lp_optimized(votes_path=None, lp_path=None, output_path=None, display=False, top_n=30):
    """Main function to run the LP optimized dashboard generation"""
    # Use default paths if not provided
    votes_path = votes_path or VOTES_DASHBOARD_DEFAULT
    lp_path = lp_path or LP_DASHBOARD_DEFAULT
    output_path = output_path or OUTPUT_PATH
    
    logger.info("🚀 Starting LP optimized dashboard generation...")
    
    # Create optimized LP dashboard
    dashboard = create_optimized_lp_dashboard(votes_path, lp_path, output_path)
    
    # Display functionality is now disabled by default
    
    return dashboard

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate optimized LP dashboard")
    parser.add_argument("--votes", help="Path to votes dashboard JSON", default=VOTES_DASHBOARD_DEFAULT)
    parser.add_argument("--lp", help="Path to LP dashboard JSON", default=LP_DASHBOARD_DEFAULT)
    parser.add_argument("--output", help="Path to save optimized LP dashboard", default=OUTPUT_PATH)
    parser.add_argument("--top", type=int, default=30, help="Number of top pools to display")
    parser.add_argument("--display", action="store_true", help="Display the dashboard in terminal")
    
    args = parser.parse_args()
    
    run_lp_optimized(
        votes_path=args.votes,
        lp_path=args.lp,
        output_path=args.output,
        display=args.display,
        top_n=args.top
    )
