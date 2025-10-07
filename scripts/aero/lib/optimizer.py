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

# Track which pools we've logged marginal calculations for
logged_pools = set()

# Default paths
DEFAULT_DASHBOARD_PATH = "input_data/aero/votes_dashboard.json"
# We don't need LP dashboard anymore as it's integrated into votes dashboard
# DEFAULT_LP_DASHBOARD_PATH = "lp_dashboard/aero/lp_dashboard.json" 
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

def equal_marginal_combined(pool_data, P, S_total, total_emissions=None, gamma=1):
    """
    Enhanced equal-marginal solver: maximizes total value (voting rewards + LP rewards)
    with volatility adjustment
    
    Args:
        pool_data: List of tuples (pool_addr, reward, weight, lp_fraction, weekly_rewards_usd, volatility)
        P: Total voting power to allocate
        S_total: Total system votes (sum of all pool weights + P)
        total_emissions: Total weekly emissions across all pools (for dynamic emission calculation)
        gamma: Volatility penalty coefficient (default 1)
    
    Returns:
        List of tuples (pool_addr, allocation)
    """
    # Use nonlocal to ensure total_emissions is accessible in nested functions
    # Default to 0 if None is passed
    total_emissions = Decimal(str(total_emissions)) if total_emissions else Decimal(0)
    gamma = Decimal(str(gamma))
    
    # Filter for potentially active pools (either has rewards or has our LP)
    active = [(p, R, W, lp, wk_r, vol) for (p, R, W, lp, wk_r, vol) in pool_data if (R > 0 or lp > 0) and W >= 0]
    if not active:
        return [(p, Decimal(0)) for (p, _, _, _, _, _) in pool_data]

    # Small floor for division safety
    EPSILON = Decimal("1e-12")
    
    def voter_marginal(R, W, delta, volatility=Decimal(0)):
        """Voter-side marginal utility per vote with volatility adjustment"""
        if R <= 0:
            return Decimal(0)
            
        # Apply volatility penalty if gamma > 0 and volatility > 0
        volatility_penalty = max(Decimal(0), Decimal(1) - (volatility / Decimal(100) * gamma))
        adjusted_R = R * volatility_penalty
        
        # Handle zero or very low weight pools properly
        if W <= EPSILON:
            # For pools with zero weight, marginal utility decreases as we add votes
            # First vote gets full reward, subsequent votes get diminishing returns
            if delta <= EPSILON:
                return adjusted_R  # First vote gets full reward
            else:
                # Marginal utility decreases as 1/(delta+1) for zero-weight pools
                return adjusted_R / (delta + Decimal(1))
        
        # Normal case: pools with existing weight
        denom = (W + delta)**2
        return adjusted_R * W / (denom if denom > EPSILON else EPSILON)
    
    def lp_marginal(lp_frac, weekly_rewards, W, delta, S_tot):
        """LP-side marginal utility per vote"""
        nonlocal total_emissions
        
        if lp_frac <= 0:
            return Decimal(0)
        
        v_i = W + delta
        
        # Special case for empty pools (W=0) - consider marginal of first vote
        if v_i <= EPSILON:
            # For display purposes, use a small value to show potential
            test_delta = Decimal("1.0")  # Consider marginal of adding 1 vote
            v_i = test_delta
            
        S = S_tot  # Total system votes
        
        # Ensure we don't have division by zero
        if S <= EPSILON:
            return Decimal(0)
        
        # Calculate LP marginal utility based on how our votes direct emissions
        # Each vote increases the pool's share of total emissions
        # The marginal utility is our LP fraction times the change in emission share
        # Use weekly_rewards parameter from the pool if available
        
        # Use total_emissions if available, otherwise fall back to weekly_rewards
        emissions_to_use = total_emissions
        
        # The marginal utility is the derivative of (lp_frac * emissions * v_i / S)
        # with respect to adding votes, where v_i is our pool's votes and S is total votes
        return lp_frac * emissions_to_use * (S - v_i) / (S * S)
    
    # Store the top marginal utility values to compare
    top_marginals = []
    def record_top_marginal(addr, R, W, lp_frac, weekly_rewards, vm, lm, total):
        """Record top marginal utility for comparison"""
        nonlocal top_marginals
        
        # Check if this pool is already in the list to avoid duplicates
        existing_pool_index = next((i for i, p in enumerate(top_marginals) if p[0] == addr), None)
        
        if existing_pool_index is not None:
            # Update existing entry if it's already in the list
            top_marginals[existing_pool_index] = (addr, R, W, lp_frac, weekly_rewards, vm, lm, total)
        else:
            # Add new entry if pool not already in the list
            top_marginals.append((addr, R, W, lp_frac, weekly_rewards, vm, lm, total))
            
        top_marginals.sort(key=lambda x: x[7], reverse=True)  # Sort by total marginal
        if len(top_marginals) > 10:
            top_marginals = top_marginals[:10]  # Keep only top 10
    
    def total_marginal(R, W, lp_frac, weekly_rewards, delta, S_tot, addr="unknown", volatility=Decimal(0)):
        """Combined marginal utility per vote"""
        global logged_pools
        
        vm = voter_marginal(R, W, delta, volatility)
        lm = lp_marginal(lp_frac, weekly_rewards, W, delta, S_tot)
        total = vm + lm
        
        # Record for comparison only once at the initial calculation
        if delta == 0:
            record_top_marginal(addr, R, W, lp_frac, weekly_rewards, vm, lm, total)
        
        # Add debug logging for pools where we have LP positions or significant volatility, but only once per pool
        if (lp_frac > 0 or volatility > 5) and delta == 0 and addr not in logged_pools:
            penalty = ""
            if gamma > 0 and volatility > 0:
                volatility_penalty = max(Decimal(0), Decimal(1) - (volatility / Decimal(100) * gamma))
                penalty = f", penalty={volatility_penalty:.4f}"
                
            # logger.info(f"Marginal calc: R=${R:.2f}, W={W:.2f}, lp_frac={lp_frac:.6f}, volatility={volatility:.2f}%{penalty}, delta={delta:.2f}")
            # logger.info(f"  → VM={vm:.6f}, LM={lm:.6f}, Total={vm+lm:.6f}")
            logged_pools.add(addr)
            
        return total
    
    def find_delta_for_pool(R, W, lp_frac, weekly_rewards, lam, S_tot, addr="unknown", volatility=Decimal(0)):
        """Find delta for a single pool using bisection"""
        if R <= 0 and lp_frac <= 0:
            return Decimal(0)
        
        # For pure voter reward pools with no LP position, we can use the closed form
        if lp_frac <= 0 and gamma == 0:
            # Only use closed form if no volatility adjustment
            d = ((R * W) / lam).sqrt() - W
            return d if d > 0 else Decimal(0)
        
        # For pools with LP positions or with volatility adjustment, we need to solve with bisection
        # Start with a reasonable range: 0 to P (all votes)
        lo, hi = Decimal(0), P
        
        # Edge case: check if we should allocate no votes
        if total_marginal(R, W, lp_frac, weekly_rewards, lo, S_tot, addr, volatility) <= lam:
            return Decimal(0)
        
        # Bisection search for delta where total_marginal = lambda
        for _ in range(40):  # Usually converges in ~30 iterations for good precision
            mid = (lo + hi) / 2
            marg = total_marginal(R, W, lp_frac, weekly_rewards, mid, S_tot, addr, volatility)
            
            if abs(marg - lam) < TOL:
                return mid
            
            if marg > lam:
                lo = mid
            else:
                hi = mid
        
        return lo  # Return our best approximation
    
    def sum_delta(lam):
        """Sum of allocations across all pools for a given lambda"""
        s = Decimal(0)
        for addr, R, W, lp_frac, weekly_rewards, volatility in active:
            d = find_delta_for_pool(R, W, lp_frac, weekly_rewards, lam, S_total, addr, volatility)
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
    for p, R, W, lp_frac, weekly_rewards, volatility in pool_data:
        if (R <= 0 and lp_frac <= 0) or W < 0:
            out.append((p, Decimal(0)))
        else:
            d = find_delta_for_pool(R, W, lp_frac, weekly_rewards, lam, S_total, p, volatility)
            out.append((p, d if d > 0 else Decimal(0)))
    
    # Display top marginal utilities for comparison
    logger.info("\nTop 10 pools by initial marginal utility:")
    if gamma > 0:
        logger.info("-----------------------------------------------------------------------------------------------")
        logger.info("Pool                R        Weight    LP Frac   LP Rewards   Vol %     Voter MU    LP MU    Total MU")
        logger.info("-----------------------------------------------------------------------------------------------")
    else:
        logger.info("----------------------------------------------------------------")
        logger.info("Pool                R        Weight    LP Frac   LP Rewards   Voter MU    LP MU    Total MU")
        logger.info("----------------------------------------------------------------")
    
    for addr, R, W, lp_frac, weekly_rewards, vm, lm, total in top_marginals:
        pool_name = addr[:10]  # We'll improve this in the full output
        volatility = next((v for p, _, _, _, _, v in active if p == addr), Decimal(0))
        
        if gamma > 0:
            logger.info(f"{pool_name:<10} ${R:<9.2f} {W:<10.2f} {lp_frac:<9.6f} ${weekly_rewards:<10.2f} {volatility:<8.2f} {vm:<10.6f} {lm:<8.6f} {total:<10.6f}")
        else:
            logger.info(f"{pool_name:<10} ${R:<9.2f} {W:<10.2f} {lp_frac:<9.6f} ${weekly_rewards:<10.2f} {vm:<10.6f} {lm:<8.6f} {total:<10.6f}")
    
    return out

def run_optimization(dashboard, volatility_data=None, gamma=0):
    """
    Run the optimization algorithm
    
    Args:
        dashboard: The votes dashboard with pool data (now includes LP data)
        volatility_data: Volatility data for pools
        gamma: Volatility penalty coefficient (0 = no penalty, default)
    
    Returns:
        Tuple of (result_dict, bot_output_string)
    """
    global logged_pools
    logged_pools.clear()  # Reset logged pools for new optimization run
    
    # Convert gamma to Decimal
    gamma = Decimal(str(gamma))
    
    # Check if Ouranous Foundation relay exists
    ouranous_relay = None
    if "relays" in dashboard:
        for relay in dashboard["relays"]:
            if relay.get("name") == "Ouranous Foundation":
                ouranous_relay = relay
                logger.info(f"Found Ouranous Foundation relay with {relay.get('voting_amount', '?')} voting power")
                break
    
    # Get weekly emissions directly from votes dashboard
    # If the emissions data is present, use it; otherwise fall back to estimating
    if "total_weekly_emissions_usd" in dashboard:
        total_emissions_usd = Decimal(str(dashboard.get("total_weekly_emissions_usd", 0)))
        logger.info(f"Using actual weekly emissions from dashboard: ${total_emissions_usd:.2f}")
    else:
        # Fall back to estimating from fees if emissions data isn't available
        total_fees = sum(Decimal(str(p.get("fees_usd", 0))) for p in dashboard["pools"])
        total_emissions_usd = total_fees * 7 * 100  # Weekly estimate based on daily fees, scaled up for LP impact
        
        # Set a minimum value for better optimization
        if total_emissions_usd < 1000000:
            total_emissions_usd = Decimal(1000000)  # Reasonable default if calculated value is too low
        
        logger.info(f"Estimated weekly emissions (fallback): ${total_emissions_usd:.2f}")
        
    # Load volatility data if not provided
    if volatility_data is None and gamma > 0:
        volatility_path = "volatility_data/aero/volatility_data.json"
        try:
            with open(volatility_path, 'r') as f:
                volatility_data = json.load(f)
            logger.info(f"Loaded volatility data with {len(volatility_data.get('pools', {}))} pools")
        except Exception as e:
            logger.warning(f"Could not load volatility data: {e}")
            volatility_data = {"pools": {}}
            
    # If gamma is enabled, log the volatility penalty setting
    if gamma > 0:
        logger.info(f"Using volatility penalty with gamma={gamma}")
    else:
        logger.info("Volatility penalty is disabled (gamma=0)")
    
    # First, get the pools so we can check for address matches
    pools = dashboard["pools"]
    
    # Create a mapping of volatility data by pool address
    volatility_map = {}
    if gamma > 0 and volatility_data and "pools" in volatility_data:
        pool_count = 0
        volatility_count = 0
        
        for addr, pool_data in volatility_data.get("pools", {}).items():
            pool_count += 1
            if "price_range" in pool_data and "volatility_percentage" in pool_data["price_range"]:
                vol_pct = pool_data["price_range"]["volatility_percentage"]
                # Make sure to normalize addresses to lowercase
                normalized_addr = addr.lower()
                volatility_map[normalized_addr] = Decimal(str(vol_pct))
                volatility_count += 1
                
                # Log pools with high volatility
                # if vol_pct > 10:
                    # symbol = pool_data.get("symbol", addr[:10])
                    # logger.info(f"High volatility detected: {symbol} - {vol_pct:.2f}%")
        
        logger.info(f"Loaded volatility data for {volatility_count} out of {pool_count} pools")
        
        # Debug to check if addresses match between volatility data and votes data
        if volatility_count > 0:
            votes_addresses = set(p["pool"].lower() for p in pools)
            volatility_addresses = set(volatility_map.keys())
            common_addresses = votes_addresses.intersection(volatility_addresses)
            logger.info(f"Found {len(common_addresses)} pools with both votes and volatility data")
            
            # Print sample addresses from each set for debugging
            if len(common_addresses) == 0:
                logger.warning("⚠️ No address matches between volatility data and votes dashboard!")
                logger.info(f"Sample vote addresses: {list(votes_addresses)[:3]}")
                logger.info(f"Sample volatility addresses: {list(volatility_addresses)[:3]}")
        
    # First remove our votes and Ouranous Foundation's votes from the pools
    # optim Ouranous Foundation's votes first, then our votes
    
    for p in pools:
        pool_addr = p["pool"].lower()
        our_votes = Decimal(str(p.get("our_votes", 0)))
        current_weight = Decimal(str(p.get("on_chain_weight", p.get("weight", 0))))
        
        # Remove Ouranous Foundation's current votes if they exist
        ouranous_votes = Decimal(0)
        if ouranous_relay:
            for vote in ouranous_relay.get("votes", []):
                if vote.get("pool", "").lower() == pool_addr:
                    ouranous_votes = Decimal(str(vote.get("weight", 0)))
                    break
        
        # Subtract just*** Ouranous Foundation's votes from the pool's weight before we optim for them
        adjusted_weight = max(current_weight - ouranous_votes, Decimal(0))
        
        # if our_votes > 0 or ouranous_votes > 0:
            # logger.info(f"Adjusting pool {p.get('symbol', '')} ({p['pool'][:10]}...):")
            # logger.info(f"  Original weight: {current_weight}")
            # if ouranous_votes > 0:
                 # logger.info(f"  Removing Ouranous votes: -{ouranous_votes}")
            # if our_votes > 0:
                # logger.info(f"  Removing our votes: -{our_votes}")
            # logger.info(f"  Adjusted weight: {adjusted_weight}")
            
        # Add adjusted weight directly to the pool data
        p["adjusted_weight"] = adjusted_weight
    
    # Now continue with the original logic
    P_our = Decimal(str(dashboard.get("our_voting_power", 0)))
    
    logger.info(f"Our total voting power: {P_our}")
    
    # Create a mapping of LP data by pool address from the integrated LP data in votes dashboard
    lp_data = {}
    logger.info("Extracting LP data from votes dashboard...")
    
    for pool in dashboard["pools"]:
        addr = pool.get("pool", "").lower()
        if not addr:
            continue
            
        # Check if this pool has our LP or is a protocol pool
        has_our_lp = pool.get("has_our_lp", False)
        is_protocol_pool = pool.get("is_protocol_pool", False)
        
        if has_our_lp or is_protocol_pool:
            # Get our LP data
            our_lp_data = pool.get("our_lp_data", {})
            tvl_usd = Decimal(str(our_lp_data.get("total_pool_tvl", 0)))
            our_lp_value = Decimal(str(our_lp_data.get("total_value_usd", 0)))
            
            # Calculate our LP fraction
            lp_fraction = Decimal(0)
            
            if tvl_usd > 0:
                # For protocol pools, we care about both our LP rewards and other LPs' rewards
                if is_protocol_pool:
                    # First, calculate our direct ownership percentage
                    if has_our_lp:
                        our_ownership = our_lp_value / tvl_usd
                    else:
                        our_ownership = Decimal(0)
                    
                    # Value other LPs' portion at 20% (0.2x)
                    other_lp_value_weight = Decimal("0.2")
                    other_lp_portion = Decimal(1) - our_ownership
                    
                    # Effective LP fraction = our_portion + (other_portion * 0.2)
                    lp_fraction = our_ownership + (other_lp_portion * other_lp_value_weight)
                    
                    logger.info(f"Protocol pool {pool.get('symbol', '')}: Our ownership={float(our_ownership*100):.2f}%, " +
                                f"Valuing other LP at 20% -> Effective LP fraction={float(lp_fraction*100):.2f}%")
                
                # For non-protocol pools with our LP, just use our ownership percentage
                elif has_our_lp:
                    lp_fraction = our_lp_value / tvl_usd
                    # Or use the pre-calculated ownership percentage
                    ownership_pct = Decimal(str(our_lp_data.get("ownership_percentage", 0)))
                    if ownership_pct > 0:
                        lp_fraction = ownership_pct / 100
            
            # Calculate weekly rewards based on daily fees
            # Multiply by 7 to convert daily fees to weekly
            weekly_rewards_usd = Decimal(str(pool.get("fees_usd", 0))) * 7  # Weekly = 7 * daily fees
            
            lp_data[addr] = {
                "lp_fraction": lp_fraction,
                "weekly_rewards_usd": weekly_rewards_usd
            }
    
    # if Foundation exists, optimize their votes first
    ouranous_allocations = {}
    if ouranous_relay:
        # Get Ouranous Foundation's voting power
        P_ouranous = Decimal(str(ouranous_relay.get("voting_amount_raw", 0)))
        
        if P_ouranous > 0:
            logger.info(f"Optimizing Ouranous Foundation votes first with {P_ouranous} voting power...")
            
            # Calculate initial system votes without our votes or Ouranous votes
            S_initial = sum(Decimal(str(p.get("adjusted_weight", 0))) for p in pools)
            logger.info(f"Initial system votes (without our votes or Ouranous votes): {S_initial}")
            
            # Build combined data for each pool for Ouranous optimization (use the same logic as for our optimization)
            ouranous_pool_data = []
            for p in pools:
                addr = p["pool"].lower()
                R = Decimal(str(p.get("total_usd", 0)))
                W = Decimal(str(p.get("adjusted_weight", 0)))
                
                # Get LP data (Ouranous doesn't have LP positions, but keep structure the same)
                lp_fraction = Decimal(0)
                weekly_rewards_usd = Decimal(0)
                
                # Get volatility data if available
                volatility = Decimal(0)
                if gamma > 0 and addr in volatility_map:
                    volatility = volatility_map[addr]
                
                ouranous_pool_data.append((addr, R, W, lp_fraction, weekly_rewards_usd, volatility))
            
            # Optimize Ouranous Foundation's votes (use the same algorithm as for our votes)
            logger.info(f"Allocating {P_ouranous} votes for Ouranous Foundation using equal-marginal algorithm...")
            ouranous_result = equal_marginal_combined(ouranous_pool_data, P_ouranous, S_initial + P_ouranous, total_emissions_usd, gamma)
            
            # Convert result to a dictionary for easier lookup
            for addr, alloc in ouranous_result:
                if alloc > 0:
                    ouranous_allocations[addr] = alloc
                    
                    # Find the pool data to log the allocation
                    pool = next((p for p in pools if p["pool"].lower() == addr), None)
                    if pool:
                        logger.info(f"Ouranous Foundation allocated {alloc} votes to {pool.get('symbol', addr[:10])}")
                        
                    # Add the allocation to the pool's adjusted weight for our optimization
                    for p in pools:
                        if p["pool"].lower() == addr:
                            p["adjusted_weight"] += alloc
                            break
            
            logger.info(f"Ouranous Foundation optimized votes applied to {len(ouranous_allocations)} pools")
    
    logger.info("Removing our votes from pool weights for our optimization...")
    for p in pools:
        our_votes = Decimal(str(p.get("our_votes", 0)))
        if our_votes > 0:
            p["adjusted_weight"] = max(p["adjusted_weight"] - our_votes, Decimal(0))
            logger.info(f"Removed {our_votes} of our votes from {p.get('symbol', '')} (new adjusted_weight: {p['adjusted_weight']})")

    # Calculate total system votes (now including optimized Ouranous votes but not our votes)
    S_total = sum(Decimal(str(p.get("adjusted_weight", p.get("on_chain_weight", p.get("weight", 0))))) for p in pools)
    S_total += P_our  # Add our votes to get total system votes
    logger.info(f"Total system votes (with optimized Ouranous votes): {S_total}")
    
    # Build combined data for each pool (voter rewards + LP data + volatility)
    combined_data = []
    for p in pools:
        addr = p["pool"].lower()
        R = Decimal(str(p.get("total_usd", 0)))
        # Use adjusted_weight which has our votes removed
        W = Decimal(str(p.get("adjusted_weight", p.get("on_chain_weight", p.get("weight", 0)))))
        
        # Get LP data if available
        lp_fraction = Decimal(0)
        weekly_rewards_usd = Decimal(0)
        if addr in lp_data:
            lp_fraction = lp_data[addr]["lp_fraction"]
            weekly_rewards_usd = lp_data[addr]["weekly_rewards_usd"]
        
        # Get volatility data if available
        volatility = Decimal(0)
        if gamma > 0 and addr in volatility_map:
            volatility = volatility_map[addr]
            
            # If we have volatility data and are applying a penalty, log for significant pools
            if volatility > 0 and (R > 50 or lp_fraction > 0):
                sym = p.get("symbol", addr[:10])
                # logger.info(f"Pool {sym}: Applying volatility penalty of {volatility:.2f}% with gamma={gamma}")
        
        combined_data.append((addr, R, W, lp_fraction, weekly_rewards_usd, volatility))
    
    # Check if we have voting power to allocate
    if P_our <= 0:
        logger.error("❌ No voting power available to allocate. Exiting optimization.")
        return {"total_expected_usd": 0, "allocations": []}, ""
    
    # We're already using total_emissions_usd from earlier in the function,
    # so we don't need to recalculate it here.
    
    logger.info(f"Using weekly emissions for LP reward calculation: ${total_emissions_usd:.2f}")
    
    # Allocate our total votes across pools
    logger.info(f"Allocating {P_our} votes based on equal-marginal algorithm...")
    alloc = equal_marginal_combined(combined_data, P_our, S_total, total_emissions_usd, gamma)
    total_alloc = sum(d for _, d in alloc)
    
    # Prepare outputs with both vote rewards and LP rewards
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
        current_pool_weight = Decimal(str(p.get("adjusted_weight", p.get("on_chain_weight", p.get("weight", 0)))))  # Current pool weight (already adjusted)
        new_total_pool_weight = current_pool_weight + d  # Add our new votes to get new total
        fraction = d / new_total_pool_weight if new_total_pool_weight > 0 else Decimal(0)  # Our share of the pool
        
        # Calculate voter-side reward (with volatility adjustment if applicable)
        volatility_penalty = Decimal(1)
        volatility = Decimal(0)
        
        # Apply volatility penalty to voter rewards if enabled
        if gamma > 0 and addr in volatility_map:
            volatility = volatility_map[addr]
            volatility_penalty = max(Decimal(0), Decimal(1) - (volatility / Decimal(100) * gamma))
            logger.debug(f"Pool {sym}: Volatility {volatility:.2f}%, penalty {volatility_penalty:.4f}")
        
        # Apply penalty to voter rewards
        vote_reward_usd = (total_usd_dec * fraction * volatility_penalty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        # Calculate LP-side reward
        lp_reward_usd = Decimal(0)
        has_lp_data = False
        lp_fraction = Decimal(0)
        
        # Get LP data if available for this pool
        if addr in lp_data:
            has_lp_data = True
            lp_fraction = lp_data[addr]["lp_fraction"]
            is_protocol_pool = next((p.get("is_protocol_pool", False) for p in pools if p["pool"].lower() == addr), False)
            
            # Calculate our share of rewards from LP position (including protocol pool logic)
            if lp_fraction > 0 and total_emissions_usd > 0:
                # Calculate this pool's share of emissions based on our vote allocation
                pool_votes = current_pool_weight + d
                
                # Use S_total but scale if it's an e18 value (common in ve3,3 systems)
                emission_baseline = S_total
                # Check if S_total is very large (likely e18)
                if emission_baseline > Decimal(1e15):  # If greater than 1e15, assume it's scaled by 1e18
                    emission_baseline = emission_baseline / Decimal(1e18)
                
                pool_emission_share = pool_votes / emission_baseline if emission_baseline > 0 else Decimal(0)
                pool_dynamic_rewards = total_emissions_usd * pool_emission_share
                
                # Our LP position's share of those rewards
                lp_reward_usd = (lp_fraction * pool_dynamic_rewards).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                
                # Add debugging information
                if is_protocol_pool:
                    logger.info(f"Protocol pool LP Reward for {sym}: effective_fraction={lp_fraction:.6f}, pool_votes={pool_votes:.2f}, emission_share={pool_emission_share:.6f}, rewards=${lp_reward_usd:.2f}")
                else:
                    logger.info(f"LP Reward for {sym}: fraction={lp_fraction:.6f}, pool_votes={pool_votes:.2f}, emission_share={pool_emission_share:.6f}, rewards=${lp_reward_usd:.2f}")
        
        # Total expected reward from both sources
        total_exp_usd = vote_reward_usd + lp_reward_usd
        
        # Get volatility info if available (we already have volatility from above, just include it in output)
        # Include volatility penalty in the output if applicable
        volatility_penalty_output = float(volatility_penalty) if gamma > 0 and volatility > 0 else None
        
        # Check if this is a protocol pool
        is_protocol_pool = next((p.get("is_protocol_pool", False) for p in pools if p["pool"].lower() == addr), False)
        
        # Prepare the output entry
        output_entry = {
            "symbol": sym,
            "pool": addr,
            "votes": float(d),
            "pct": int(pct),
            "voter_reward_usd": float(vote_reward_usd),
            "lp_reward_usd": float(lp_reward_usd),
            "total_reward_usd": float(vote_reward_usd + lp_reward_usd),
            "has_lp_position": has_lp_data and lp_fraction > 0,
            "is_protocol_pool": is_protocol_pool,
            "lp_fraction": float(lp_fraction) if has_lp_data else 0.0,
            "volatility": float(volatility) if volatility > 0 else None,
            "volatility_penalty": volatility_penalty_output
        }
        
        # Add protocol pool specific information if applicable
        if is_protocol_pool:
            # Calculate raw ownership vs effective ownership (with 20% boost)
            raw_lp_ownership = float(lp_fraction) if has_lp_data else 0.0
            # Get pool TVL to calculate other LP portion
            p = next((x for x in pools if x["pool"].lower() == addr), None)
            if p and has_lp_data:
                tvl_usd = Decimal(str(p.get("tvl_usd", 0)))
                if tvl_usd > 0:
                    our_lp_value = raw_lp_ownership * tvl_usd
                    other_lp_value = tvl_usd - our_lp_value
                    other_lp_portion = (other_lp_value / tvl_usd) if tvl_usd > 0 else Decimal(0)
                    other_lp_value_weight = Decimal("0.2")  # 20% valuation for other LPs
                    effective_fraction = raw_lp_ownership + (other_lp_portion * other_lp_value_weight)
                    
                    output_entry["protocol_pool_info"] = {
                        "raw_ownership": raw_lp_ownership,
                        "effective_fraction": float(effective_fraction),
                        "other_lp_portion": float(other_lp_portion),
                        "other_lp_value_weight": float(other_lp_value_weight)
                    }
            
        human.append(output_entry)
        
        # Scale to 100e18 total for bot output
        weight_i = (d / P_our * TOTAL_WEIGHT_TARGET).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        bot_lines.append(f"{addr} {int(weight_i)}")
    
    # Compute total expected USD return (voter + LP rewards)
    total_voter_reward = sum(item['voter_reward_usd'] for item in human)
    total_lp_reward = sum(item['lp_reward_usd'] for item in human)
    total_exp_usd = total_voter_reward + total_lp_reward
    
    # Log breakdown of rewards
    logger.info(f"Total expected voter rewards: ${total_voter_reward:.2f}")
    logger.info(f"Total expected LP rewards: ${total_lp_reward:.2f}")
    logger.info(f"Combined total expected rewards: ${total_exp_usd:.2f}")
    
    # Sort by total reward
    human.sort(key=lambda x: x['total_reward_usd'], reverse=True)
    
    # Assemble output with detailed totals
    human_output = {
        "total_voter_reward_usd": round(total_voter_reward, 2),
        "total_lp_reward_usd": round(total_lp_reward, 2),
        "total_expected_usd": round(total_exp_usd, 2),
        "allocations": human
    }

    
    
    # Add Ouranous Foundation optimization data if available
    if ouranous_relay and ouranous_allocations:
        # Convert allocations to a format similar to our result structure
        ouranous_alloc_list = []
        for addr, votes in ouranous_allocations.items():
            pool = next((p for p in pools if p["pool"].lower() == addr), None)
            if pool:
                ouranous_alloc_list.append({
                    "symbol": pool.get("symbol", ""),
                    "pool": addr,
                    "votes": float(votes),
                    "pct": float((votes / Decimal(str(ouranous_relay["voting_amount_raw"])) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                })
        
        # Sort by votes (descending)
        ouranous_alloc_list.sort(key=lambda x: x["votes"], reverse=True)
        
        # Add to result
        human_output["ouranous_foundation"] = {
            "voting_power": float(ouranous_relay["voting_amount_raw"]),
            "allocations": ouranous_alloc_list
        }

        
    
    bot_output = "\n".join(bot_lines)
    
    return human_output, bot_output

def run_optimize(save=True, votes_path=None, with_volatility=False, gamma=1):
    """
    Main entry point for optimizing votes
    
    Args:
        save: Whether to save results to file (True) or display them (False)
        votes_path: Path to the votes dashboard JSON file. If None, uses default path.
        with_volatility: Whether to apply volatility penalty (default False)
        gamma: Volatility penalty coefficient if with_volatility is True (default 1)
    """
    logger.info("Starting vote optimization")
    
    # Load votes dashboard
    dashboard_path = votes_path if votes_path else DEFAULT_DASHBOARD_PATH
    dashboard = load_json(dashboard_path)
    if not dashboard:
        return None
    
    # LP data is now integrated directly in the votes dashboard
    logger.info(f"Using integrated LP data from votes dashboard")
    # Check if LP data is present in the dashboard
    lp_positions = dashboard.get("lp_positions", [])
    if lp_positions:
        logger.info(f"✅ Found {len(lp_positions)} LP positions in votes dashboard")
    else:
        logger.info("⚠️ No LP positions found in votes dashboard, optimizing based on voter rewards only")
    
    # Load volatility data if needed
    volatility_data = None
    if with_volatility:
        volatility_path = "volatility_data/aero/volatility_data.json"
        if os.path.exists(volatility_path):
            logger.info(f"Loading volatility data from {volatility_path}")
            volatility_data = load_json(volatility_path)
            if volatility_data:
                logger.info(f"✅ Loaded volatility data with {len(volatility_data.get('pools', {}))} pools")
            else:
                logger.warning("❌ Failed to load volatility data")
                with_volatility = False  # Disable if load failed
        else:
            logger.warning("⚠️ No volatility data found, disabling volatility adjustment")
            with_volatility = False
    
    # Run optimization with parameters based on flags
    gamma_value = gamma if with_volatility else 0
    result, bot_output = run_optimization(dashboard, volatility_data, gamma_value)
    
    # If displaying results, show volatility impact on rewards if enabled
    if not save and with_volatility:
        # Run the optimization again without volatility for comparison
        logger.info("\nRunning comparison without volatility to show impact...")
        result_no_vol, _ = run_optimization(dashboard, volatility_data, 0)
        
        # Calculate the difference in expected rewards
        total_with_vol = result['total_expected_usd']
        total_without_vol = result_no_vol['total_expected_usd']
        diff = total_with_vol - total_without_vol
        
        print("\n=== Volatility Impact Analysis ===")
        print(f"Expected return with volatility: ${total_with_vol:.2f}")
        print(f"Expected return without volatility: ${total_without_vol:.2f}")
        print(f"Difference: ${diff:.2f} ({(diff/total_without_vol*100):.2f}%)")
        
        # Compare vote allocation differences for high-volatility pools
        print("\nImpact on high volatility pools (>5%):")
        print("------------------------------------------")
        print("Pool         Vol%  With Vol  Without Vol   Diff    %Change")
        print("------------------------------------------")
        
        # Create maps for easier lookup
        with_vol_map = {a["pool"]: a for a in result["allocations"]}
        without_vol_map = {a["pool"]: a for a in result_no_vol["allocations"]}
        
        # Find pools with significant volatility
        high_vol_pools = [(addr, a) for addr, a in with_vol_map.items() if a.get("volatility", 0) and a.get("volatility", 0) > 5]
        high_vol_pools.sort(key=lambda x: x[1].get("volatility", 0), reverse=True)
        
        # Show differences for top high volatility pools
        for addr, alloc in high_vol_pools[:10]:
            symbol = alloc.get("symbol", "").ljust(10)
            vol = alloc.get("volatility", 0)
            votes_with = alloc.get("votes", 0)
            votes_without = without_vol_map.get(addr, {}).get("votes", 0) if addr in without_vol_map else 0
            diff = votes_with - votes_without
            pct_change = (diff / votes_without * 100) if votes_without > 0 else 0
            print(f"{symbol} {vol:5.1f}%  {votes_with:8.0f}  {votes_without:10.0f}  {diff:+6.0f}  {pct_change:+6.1f}%")
            
        print("\nImpact on pools with largest vote changes:")
        print("------------------------------------------")
        print("Pool         Vol%  With Vol  Without Vol   Diff    %Change")
        print("------------------------------------------")
        
        # Find pools with the largest absolute vote changes
        vote_changes = []
        for addr, alloc in with_vol_map.items():
            if addr in without_vol_map:
                votes_with = alloc.get("votes", 0)
                votes_without = without_vol_map[addr].get("votes", 0)
                diff = votes_with - votes_without
                vol = alloc.get("volatility", 0) or 0
                
                # Only include pools with actual vote changes
                if abs(diff) > 0:
                    vote_changes.append((addr, alloc, diff, vol))
        
        # Sort by absolute difference in votes (largest changes first)
        vote_changes.sort(key=lambda x: abs(x[2]), reverse=True)
        
        for addr, alloc, diff, vol in vote_changes[:10]:
            symbol = alloc.get("symbol", "").ljust(10)
            votes_with = alloc.get("votes", 0)
            votes_without = without_vol_map[addr].get("votes", 0)
            pct_change = (diff / votes_without * 100) if votes_without > 0 else 0
            print(f"{symbol} {vol:5.1f}%  {votes_with:8.0f}  {votes_without:10.0f}  {diff:+6.0f}  {pct_change:+6.1f}%")
    
    
    # Save or display results
    if save:
        save_json(result, HUMAN_OUT_PATH)
        save_text(bot_output, BOT_OUT_PATH)
        logger.info(f"✅ Total expected combined return: ${result['total_expected_usd']:.2f}")
        logger.info(f"   - Voter rewards: ${result['total_voter_reward_usd']:.2f}")
        logger.info(f"   - LP rewards: ${result['total_lp_reward_usd']:.2f}")
        logger.info(f"✅ Allocated votes across {len(result['allocations'])} pools")
    else:
        print("\n================ OPTIMIZATION RESULTS ================")
        print(f"Total Expected USD: ${result['total_expected_usd']:.2f}")
        print(f" - Voter Rewards: ${result['total_voter_reward_usd']:.2f}")
        print(f" - LP Rewards: ${result['total_lp_reward_usd']:.2f}")
        
        # Change header based on whether volatility is enabled
        if with_volatility:
            print("----------------------------------------------------------------")
            print("Pool           Votes     Voter     LP    Total  Vol%   LP Pos")
            print("----------------------------------------------------------------")
        else:
            print("------------------------------------------------------")
            print("Pool           Votes     Voter     LP    Total  LP Pos")
            print("------------------------------------------------------")
        for alloc in result["allocations"][:TOP_N]:
            symbol = alloc.get("symbol", "").ljust(10)
            votes = f"{alloc.get('votes', 0):.0f}".rjust(6)
            voter_reward = f"${alloc.get('voter_reward_usd', 0):.2f}".rjust(8)
            lp_reward = f"${alloc.get('lp_reward_usd', 0):.2f}".rjust(6)
            total_reward = f"${alloc.get('total_reward_usd', 0):.2f}".rjust(8)
            lp_mark = "✓" if alloc.get("has_lp_position", False) else " "
            
            if with_volatility:
                volatility = alloc.get("volatility", None)
                vol_str = f"{volatility:.2f}".rjust(5) if volatility is not None else "  - ".rjust(5)
                print(f"{symbol} {votes} {voter_reward} {lp_reward} {total_reward} {vol_str}  {lp_mark}")
            else:
                print(f"{symbol} {votes} {voter_reward} {lp_reward} {total_reward}  {lp_mark}")
        if len(result["allocations"]) > TOP_N:
            print(f"... and {len(result['allocations']) - TOP_N} more pools")
        print("======================================================\n")
    
    return result