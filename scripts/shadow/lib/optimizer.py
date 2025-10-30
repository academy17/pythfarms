#!/usr/bin/env python3
import os
import json
import logging
from decimal import Decimal, getcontext, ROUND_HALF_UP
from dotenv import load_dotenv
from web3 import Web3
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

getcontext().prec = 50

# Constants
TOL = Decimal("1e-12")
MAX_ITERS = 100
TOTAL_WEIGHT_TARGET = Decimal(100) * (Decimal(10) ** 18)  # scale to 100e18 for bot outputs

load_dotenv()
SHADOW_RPC_URL = os.getenv("SHADOW_RPC_URL")
SHADOW_VOTER_ADDRESS = os.getenv("SHADOW_VOTER_ADDRESS")  
VOTER_ABI_PATH = os.getenv('VOTER_ABI_PATH', 'abi/shadow/Voter.json')
SHADOW_NFT_OWNER_ADDRESS = os.getenv("SHADOW_NFT_OWNER_ADDRESS", "")
SHADOW_PROTOCOL_WALLET = os.getenv("SHADOW_PROTOCOL_WALLET", "")

def load_json(path):
    """Load a JSON file from the given path."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found.")
    with open(path) as f:
        return json.load(f)

def equal_marginal(RWV, P, with_volatility=False, gamma=Decimal(1), S_total=None):
    """
    Equal marginal utility optimization algorithm with optional volatility penalties.
    
    Args:
        RWV: List of tuples (pool_address, reward, external_votes, volatility)
        P: Total voting power to allocate
        with_volatility: Whether to include volatility penalties
        gamma: Volatility penalty coefficient
        S_total: Total system votes (for volatility penalty calculation)
        
    Returns:
        List of (pool_address, vote_allocation) tuples.
    """
    # Handle both old format (R, W) and new format (R, W, V)
    if RWV and len(RWV[0]) == 3:
        # Old format without volatility
        active = [(p, R, W, Decimal(0)) for (p, R, W) in RWV if R > 0 and W >= 0]
    else:
        # New format with volatility
        active = [(p, R, W, V) for (p, R, W, V) in RWV if R > 0 and W >= 0]
    
    if not active:
        return [(p, Decimal(0)) for (p, *_) in RWV]

    def voter_marginal(R, W, our_votes, volatility=Decimal(0), S_tot=None):
        """Calculate marginal utility with optional volatility penalty"""
        if R <= 0:
            return Decimal(0)
        
        EPSILON = Decimal("1e-12")
        external_votes = max(W, EPSILON)
        
        # Base marginal utility
        base = R * external_votes / (external_votes + our_votes)**2
        
        if not with_volatility or volatility <= 0 or not S_tot or S_tot <= 0:
            return base
            
        # Apply volatility penalty
        P_share = (external_votes + our_votes) / S_tot
        sigma = volatility / Decimal(100)  # Convert percentage to decimal
        
        risk_mult = Decimal(1) - (2 * gamma * (sigma**2) * P_share * R)
        risk_mult = max(Decimal(0), min(Decimal(1), risk_mult))
        
        return base * risk_mult

    def sum_delta(lam):
        total = Decimal(0)
        for _, R, W, V in active:
            if R <= 0:
                continue
            
            # Use binary search to find optimal delta for this pool
            lo_delta, hi_delta = Decimal(0), P * 10  # generous upper bound
            
            for _ in range(50):  # Binary search iterations
                mid_delta = (lo_delta + hi_delta) / 2
                marginal = voter_marginal(R, W, mid_delta, V, S_total)
                
                if marginal > lam:
                    lo_delta = mid_delta
                else:
                    hi_delta = mid_delta
                    
                if hi_delta - lo_delta < TOL:
                    break
            
            delta = lo_delta
            if delta > 0:
                total += delta
                
        return total

    # Find lambda using binary search
    lo, hi = Decimal("1e-30"), Decimal("1")
    for _ in range(200):
        if sum_delta(hi) < P:
            break
        hi *= 2
    else:
        raise RuntimeError("Could not bracket lambda for equal-marginal")

    for _ in range(MAX_ITERS):
        mid = (lo + hi) / 2
        s = sum_delta(mid)
        if abs(s - P) < TOL:
            lam = mid
            break
        if s > P:
            lo = mid
        else:
            hi = mid
    else:
        lam = lo

    # Calculate final allocations
    out = []
    for item in RWV:
        if len(item) == 3:
            p, R, W = item
            V = Decimal(0)
        else:
            p, R, W, V = item
            
        if R <= 0 or W < 0:
            out.append((p, Decimal(0)))
        else:
            # Find optimal delta for this pool given lambda
            lo_delta, hi_delta = Decimal(0), P * 10
            
            for _ in range(50):
                mid_delta = (lo_delta + hi_delta) / 2
                marginal = voter_marginal(R, W, mid_delta, V, S_total)
                
                if marginal > lam:
                    lo_delta = mid_delta
                else:
                    hi_delta = mid_delta
                    
                if hi_delta - lo_delta < TOL:
                    break
            
            delta = lo_delta if lo_delta > 0 else Decimal(0)
            out.append((p, delta))
            
    return out

def deduct_user_votes(dashboard, user_votes):
    """Deduct user's votes from the dashboard totals and pool votes."""
    adjusted_pools = []
    total_votes_deducted = Decimal(0)

    for pool in dashboard['pools']:
        adjusted_pool = pool.copy()
        for vote in user_votes['votes']:
            if vote['pool'].lower() == pool['pool'].lower():
                your_weight = Decimal(vote['weight']) / Decimal(10**18)
                adjusted_pool['pool_votes_period'] = float(Decimal(pool['pool_votes_period']) - your_weight)
                total_votes_deducted += your_weight
                break
        adjusted_pools.append(adjusted_pool)

    adjusted_total_votes = Decimal(dashboard['total_votes_period']) - total_votes_deducted
    return {
        'period': dashboard['period'],
        'total_votes_period': float(adjusted_total_votes),
        'pools': adjusted_pools
    }

def run_optimization(dashboard, voting_power, re_run=False, previous_votes=None, with_volatility=False, gamma=1.0):
    """
    Run the optimization algorithm on a dashboard with given voting power.
    
    Args:
        dashboard: The votes dashboard to optimize against
        voting_power: Decimal value of available voting power
        re_run: Whether this is a re-run (user already voted in this period)
        previous_votes: Dict mapping pool addresses to previous vote weights
        with_volatility: Whether to include volatility penalties in optimization
        gamma: Volatility penalty coefficient (default 1.0)
        
    Returns:
        Dictionary with optimization results
    """
    pools = dashboard.get("pools", [])
    pools = sorted(pools, key=lambda p: p.get("bribes_usd", 0), reverse=True)[:10]
    logger.info(f"ℹ️ Allocating {voting_power} votes based on bribes_usd.")
    
    # Load volatility data if requested
    volatility_data = {}
    if with_volatility:
        try:
            # Load volatility data
            raw_data = load_json('volatility_data/shadow/volatility_data.json')
            
            # The volatility data is nested under 'pools'
            pool_volatility = raw_data.get('pools', {})
            
            # Map volatility data directly using pool addresses
            volatility_data = {addr.lower(): data for addr, data in pool_volatility.items()}
            
            # Log which pools we found volatility data for
            found_pools = []
            missing_pools = []
            for p in pools:
                pool_addr = p["pool"].lower()
                if pool_addr in volatility_data:
                    found_pools.append(p.get("symbol", "Unknown"))
                else:
                    missing_pools.append(p.get("symbol", "Unknown"))
            
            logger.info(f"📊 Loaded volatility data for {len(found_pools)} pools")
            if found_pools:
                logger.info(f"✅ Found volatility for: {', '.join(found_pools)}")
            if missing_pools:
                logger.warning(f"⚠️ Missing volatility data for pools: {', '.join(missing_pools)}")
                
        except FileNotFoundError:
            logger.warning("⚠️ Volatility data not found, proceeding without volatility penalties")
            with_volatility = False
        except Exception as e:
            logger.error(f"❌ Error loading volatility data: {e}")
            with_volatility = False
    
    base = []
    locked = {}
    
    # Prepare optimization inputs
    for p in pools:
        addr = p["pool"].lower()
        R = Decimal(str(p.get("bribes_usd", 0)))
        W_total = Decimal(str(p.get("pool_votes_period", 0)))
        
        # Get volatility for this pool
        volatility = Decimal(0)
        if with_volatility and addr in volatility_data:
            vol_data = volatility_data[addr]
            if 'price_range' in vol_data and 'volatility_percentage' in vol_data['price_range']:
                volatility = Decimal(str(vol_data['price_range']['volatility_percentage']))
                if volatility > 0:
                    logger.info(f"📈 Pool {p.get('symbol', 'Unknown')}: {volatility}% volatility")
        
        if re_run and previous_votes and addr in previous_votes:
            W = W_total - previous_votes[addr]
            if W < 0:
                W = Decimal(0)
        else:
            W = W_total
            
        locked[addr] = W_total
        base.append((addr, R, W, volatility))
    
    # Calculate total system votes for volatility penalty calculation
    total_system_votes = sum(Decimal(str(p.get("pool_votes_period", 0))) for p in pools) + voting_power
    
    # Run optimization
    alloc = equal_marginal(base, voting_power, with_volatility, Decimal(str(gamma)), total_system_votes)
    total_alloc = sum(d for _, d in alloc)
    
    # Build outputs
    human, bot_lines = [], []
    for addr, d in alloc:
        if d <= 0:
            continue
        p = next(x for x in pools if x["pool"].lower() == addr)
        pct = (d / total_alloc * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        fraction = d / (locked[addr] + d) if (locked[addr] + d) > 0 else Decimal(0)
        exp_usd = float((Decimal(str(p.get("bribes_usd", 0))) * fraction).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        human.append({
            "symbol": p.get("symbol", ""), 
            "pool": addr, 
            "votes": float(d), 
            "pct": int(pct), 
            "exp_usd": exp_usd
        })
        weight_i = (d / voting_power * TOTAL_WEIGHT_TARGET).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        bot_lines.append(f"{addr} {int(weight_i)}")
    
    total_exp = sum(item['exp_usd'] for item in human)
    human.sort(key=lambda x: x['pct'], reverse=True)
    
    result = {
        "total_expected_usd": round(total_exp, 2), 
        "allocations": human, 
        "re_run": re_run,
        "period": dashboard.get("period")
    }
    
    bot_output = "\n".join(bot_lines)
    
    return result, bot_output

def save_optimization(result, bot_output, is_historical=False):
    """
    Save optimization results to files.

    Args:
        result: Optimization result dict
        bot_output: String with bot-formatted output
        is_historical: Whether this is for a historical period
    """
    period = result.get("period")
    date_str = datetime.now().strftime('%Y%m%d')

    if is_historical:
        human_path = f'optimized_votes/shadow/historical/{period}_historical_optimal_votes.json'
        bot_path = f'votes/shadow/historical/{period}_historical_optimal_votes_bot.txt'
    else:
        human_path = f'optimized_votes/shadow/{period}_optimized_votes_human.json'
        bot_path = f'optimized_votes/shadow/{period}_optimized_votes_bot.txt'

        # Also save to standard locations for compatibility
        std_human_path = 'optimized_votes/shadow/optimized_votes_human.json'
        std_bot_path = 'optimized_votes/shadow/optimized_votes_bot.txt'

        os.makedirs(os.path.dirname(std_human_path), exist_ok=True)
        with open(std_human_path, 'w') as f:
            json.dump(result, f, indent=2)

        with open(std_bot_path, 'w') as f:
            f.write(bot_output)

    os.makedirs(os.path.dirname(human_path), exist_ok=True)
    with open(human_path, 'w') as f:
        json.dump(result, f, indent=2)

    with open(bot_path, 'w') as f:
        f.write(bot_output)

    logger.info(f"✅ Saved optimization results to {human_path} and {bot_path}")
    return human_path, bot_path

def get_current_voting_power(owner):
    # Hardcoded VoteModule address and ABI path
    VOTE_MODULE_ADDRESS = "0xDCB5A24ec708cc13cee12bFE6799A78a79b666b4"
    VOTE_MODULE_ABI_PATH = "abi/shadow/VoteModule.json"
    w3 = Web3(Web3.HTTPProvider(SHADOW_RPC_URL))
    with open(VOTE_MODULE_ABI_PATH) as f:
        vote_module_abi = json.load(f)
    contract = w3.eth.contract(address=w3.to_checksum_address(VOTE_MODULE_ADDRESS), abi=vote_module_abi)
    raw_power = contract.functions.balanceOf(owner).call()
    power = Decimal(raw_power) / Decimal(10 ** 18)
    logger.info(f"Current voting power for {owner[:10]}...: {power}")
    return power

def get_user_votes(period=None, wallet_address=None):
    """
    Get a wallet's votes for a specific period.
    
    Args:
        period: Period to fetch votes for, or None to use last voted period
        wallet_address: Wallet address to fetch votes for, or None to use NFT owner address
    
    Returns:
        Dict with period and votes
    """
    w3 = Web3(Web3.HTTPProvider(SHADOW_RPC_URL))
    if not w3.is_connected():
        logger.error("❌ Failed to connect to RPC node")
        return None
    
    try:
        with open(VOTER_ABI_PATH) as f:
            voter_abi = json.load(f)
    except Exception as e:
        logger.error(f"❌ Failed to load ABI: {e}")
        return None
    
    contract = w3.eth.contract(address=w3.to_checksum_address(SHADOW_VOTER_ADDRESS), abi=voter_abi)
    
    if wallet_address:
        owner = w3.to_checksum_address(wallet_address)
    else:
        if not SHADOW_NFT_OWNER_ADDRESS:
            logger.error("❌ SHADOW_NFT_OWNER_ADDRESS not set in .env")
            return None
        owner = w3.to_checksum_address(SHADOW_NFT_OWNER_ADDRESS)
    
    # If period not specified, get last voted period
    if period is None:
        try:
            period = contract.functions.lastVoted(owner).call()
            logger.info(f"Last voted period for {owner[:10]}...: {period}")
        except Exception as e:
            logger.error(f"❌ Failed to get last voted period: {e}")
            return None
    
    try:
        num_pools = contract.functions.userVotedPoolsPerPeriodLength(owner, period).call()
        logger.info(f"Number of pools voted for by {owner[:10]}... in period {period}: {num_pools}")
        
        pools = []
        for i in range(num_pools):
            pool = contract.functions.userVotedPoolsPerPeriod(owner, period, i).call()
            pools.append(pool)
        
        votes = []
        for pool in pools:
            weight = contract.functions.userVotesForPoolPerPeriod(owner, period, pool).call()
            votes.append({'pool': pool, 'weight': weight})
        
        return {'period': period, 'votes': votes}
    except Exception as e:
        logger.error(f"❌ Failed to fetch votes for period {period}: {e}")
        return None

def display_optimization(result):
    """Display optimization results in a readable format."""
    if not result:
        return
    
    print("\n================ OPTIMIZATION RESULTS ================")
    print(f"Total Expected USD: ${result['total_expected_usd']:.2f}")
    print("------------------------------------------------------")
    print("Pool                                    Votes    Exp USD")
    print("------------------------------------------------------")
    
    for alloc in result["allocations"]:
        symbol = alloc.get("symbol", "").ljust(10)
        votes = f"{alloc.get('votes', 0):.2f}".rjust(8)
        exp_usd = f"${alloc.get('exp_usd', 0):.2f}".rjust(8)
        print(f"{symbol} ({alloc.get('pool')[:10]}...) {votes} {exp_usd}")
    
    print("======================================================\n")

def save_calldata(result, owner):
    """
    Save calldata for optimized votes (for bot use).
    """
    pools = [alloc["pool"] for alloc in result["allocations"]]
    weights = [
        int(Decimal(str(alloc["votes"])) * Decimal(10**18))
        for alloc in result["allocations"]
    ]
    calldata = {
        "voter": owner,
        "_pools": pools,
        "_weights": weights
    }
    path = f"optimized_votes/shadow/optimized_votes_calldata.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(calldata, f, indent=2)
    logger.info(f"✅ Saved calldata output to {path}")
    return path

def run_optimize(period=None, save=True, is_historical=False, recompute=False, with_volatility=False, gamma=1.0):
    """
    Main entry point for running the optimizer.
    
    Args:
        period: Period to optimize for, or None for current/next period
        save: Whether to save results to file
        is_historical: Whether this is a historical optimization
        recompute: Whether to prompt for manual dashboard file input
        with_volatility: Whether to apply volatility penalty to volatile pools
        gamma: Volatility penalty coefficient (default 1.0, higher = stronger penalty)
    """
    if not (SHADOW_RPC_URL and SHADOW_VOTER_ADDRESS):
        logger.error("❌ RPC_URL or CONTRACT_ADDRESS not set in env.")
        return None
    
    w3 = Web3(Web3.HTTPProvider(SHADOW_RPC_URL))
    if not w3.is_connected():
        logger.error("❌ Failed to connect to RPC node")
        return None
    
    try:
        with open(VOTER_ABI_PATH) as f:
            voter_abi = json.load(f)
    except Exception as e:
        logger.error(f"❌ Failed to load ABI: {e}")
        return None
    
    contract = w3.eth.contract(address=w3.to_checksum_address(SHADOW_VOTER_ADDRESS), abi=voter_abi)
    
    # Determine the period to optimize for
    if period is None:
        if is_historical:
            period = int(input("Enter the historical period to optimize for: "))
        else:
            period = contract.functions.getPeriod().call() + 1
    
    logger.info(f"Optimizing for period {period}")
    
    if is_historical:
        dashboard_path = input(f"Enter path to historical dashboard for period {period} (e.g., input_data/shadow/historical/{period}_votes_dashboard_ddmmyy.json): ")
    else:
        dashboard_path = f'input_data/shadow/{period}_votes_dashboard.json'
        if not os.path.exists(dashboard_path):
            dashboard_path = 'input_data/shadow/votes_dashboard.json'
    
    try:
        dashboard = load_json(dashboard_path)
        if dashboard.get('period') != period:
            logger.warning(f"⚠️ Period mismatch: dashboard period is {dashboard.get('period')}, requested period is {period}")
    except FileNotFoundError:
        logger.error(f"❌ Dashboard not found at {dashboard_path}")
        period_file = f'input_data/shadow/{period}_votes_dashboard.json'
        generic_file = 'input_data/shadow/votes_dashboard.json'
        logger.error(f"   Looked for: {dashboard_path}")
        logger.error(f"   Also checked: {period_file} exists: {os.path.exists(period_file)}")
        logger.error(f"   Also checked: {generic_file} exists: {os.path.exists(generic_file)}")
        logger.error(f"   Have you run 'python scripts/shadow/shadow_manager.py fetch' first?")
        return None
    
    if not SHADOW_NFT_OWNER_ADDRESS:
        logger.error("❌ SHADOW_NFT_OWNER_ADDRESS not set in env.")
        return None
    
    owner = w3.to_checksum_address(SHADOW_NFT_OWNER_ADDRESS)
    
    # Create a working copy of the dashboard that we can modify
    working_dashboard = {
        'period': dashboard.get('period'),
        'total_votes_period': dashboard.get('total_votes_period'),
        'pools': [p.copy() for p in dashboard.get('pools', [])]
    }
    
    # Check if we have a protocol wallet defined
    protocol_votes = None
    protocol_voting_power = Decimal(0)
    protocol_allocations = {}
    
    if SHADOW_PROTOCOL_WALLET:
        protocol_wallet = w3.to_checksum_address(SHADOW_PROTOCOL_WALLET)
        logger.info(f"Protocol wallet detected: {protocol_wallet[:10]}...")
        
        # Get protocol wallet's votes for the current period
        protocol_votes = get_user_votes(period, protocol_wallet)
        
        # If protocol wallet has votes, deduct them from pool weights
        if protocol_votes and protocol_votes.get('votes'):
            logger.info(f"Found {len(protocol_votes['votes'])} pools with protocol wallet votes")
            for vote in protocol_votes['votes']:
                pool_addr = vote['pool'].lower()
                vote_weight = Decimal(vote.get('weight', 0)) / (Decimal(10) ** 18)
                logger.info(f"Protocol wallet has {vote_weight} votes on pool {pool_addr[:10]}...")
                
                # Find the pool and deduct the votes
                for pool in working_dashboard['pools']:
                    if pool['pool'].lower() == pool_addr:
                        original_votes = Decimal(pool.get('pool_votes_period', 0))
                        pool['pool_votes_period'] = float(max(Decimal(0), original_votes - vote_weight))
                        logger.info(f"Deducted {vote_weight} protocol wallet votes from pool {pool.get('symbol', pool_addr[:10])} (original: {original_votes}, new: {pool['pool_votes_period']})")
                        break
        
        # Get protocol wallet's voting power
        try:
            if is_historical:
                raw_power = contract.functions.userVotingPowerPerPeriod(protocol_wallet, period).call()
                protocol_voting_power = Decimal(raw_power) / (Decimal(10) ** 18)
                logger.info(f"Protocol wallet historical voting power for period {period}: {protocol_voting_power}")
            else:
                protocol_voting_power = get_current_voting_power(protocol_wallet)
                logger.info(f"Protocol wallet current voting power: {protocol_voting_power}")
                
            # If protocol wallet has voting power, optimize its votes first
            if protocol_voting_power > 0:
                logger.info(f"Optimizing protocol wallet votes (without volatility) with {protocol_voting_power} voting power...")
                
                # Run optimization for protocol wallet WITHOUT volatility penalties
                protocol_result, _ = run_optimization(working_dashboard, protocol_voting_power, False, None, False, 1.0)
                
                if protocol_result and 'allocations' in protocol_result:
                    # Store protocol wallet allocations for reference
                    for alloc in protocol_result['allocations']:
                        pool_addr = alloc['pool'].lower()
                        votes = Decimal(alloc.get('votes', 0))
                        protocol_allocations[pool_addr] = votes
                        
                        # Add these votes back to the working dashboard
                        for pool in working_dashboard['pools']:
                            if pool['pool'].lower() == pool_addr:
                                pool['pool_votes_period'] = float(Decimal(pool.get('pool_votes_period', 0)) + votes)
                                logger.info(f"Added {votes} protocol wallet optimized votes to pool {pool.get('symbol', pool_addr[:10])}, new total: {pool['pool_votes_period']}")
                                break
                    
                    logger.info(f"Protocol wallet votes optimized across {len(protocol_allocations)} pools")
        except Exception as e:
            logger.error(f"Error optimizing protocol wallet votes: {e}")
    
    # Now get our voting power and optimize our votes
    if is_historical:
        raw_power = contract.functions.userVotingPowerPerPeriod(owner, period).call()
        voting_power = Decimal(raw_power) / (Decimal(10) ** 18)
        logger.info(f"ℹ️ Historical voting power for {owner[:10]}... at period {period}: {voting_power}")

        user_votes = get_user_votes(period)
        if not user_votes:
            logger.error("❌ Failed to get user votes for historical period")
            return None

        # Apply our deductions to the working dashboard that may already have protocol wallet votes
        for vote in user_votes.get('votes', []):
            pool_addr = vote['pool'].lower()
            vote_weight = Decimal(vote.get('weight', 0)) / (Decimal(10) ** 18)
            
            # Find the pool and deduct our votes
            for pool in working_dashboard['pools']:
                if pool['pool'].lower() == pool_addr:
                    original_votes = Decimal(pool.get('pool_votes_period', 0))
                    pool['pool_votes_period'] = float(max(Decimal(0), original_votes - vote_weight))
                    logger.info(f"Deducted {vote_weight} of our votes from pool {pool.get('symbol', pool_addr[:10])}")
                    break

        # Run optimization with our votes on the working dashboard
        result, bot_output = run_optimization(working_dashboard, voting_power, False, None, with_volatility, gamma)
    else:
        # Use VoteModule's balanceOf for current voting power
        voting_power = get_current_voting_power(owner)
        logger.info(f"ℹ️ Current voting power for {owner[:10]}...: {voting_power}")

        re_run = False
        previous_votes = {}

        current_votes = get_user_votes(period)
        if current_votes and current_votes.get('votes'):
            re_run = True
            for v in current_votes['votes']:
                pool = v['pool'].lower()
                weight = Decimal(v.get('weight', 0)) / (Decimal(10) ** 18)
                previous_votes[pool] = weight
                
                # Deduct our current votes from the working dashboard
                for p in working_dashboard['pools']:
                    if p['pool'].lower() == pool:
                        p['pool_votes_period'] = float(max(Decimal(0), Decimal(p.get('pool_votes_period', 0)) - weight))
                        logger.info(f"Deducted {weight} of our existing votes from pool {p.get('symbol', pool[:10])}")
                        break
                        
            logger.info(f"ℹ️ Re-run detected: found {len(previous_votes)} pools with our existing votes")

        # Run optimization with our votes on the working dashboard
        result, bot_output = run_optimization(working_dashboard, voting_power, re_run, previous_votes, with_volatility, gamma)

    # If protocol wallet allocations exist, add them to the result for reference
    if protocol_allocations:
        protocol_allocs_list = []
        for pool_addr, votes in protocol_allocations.items():
            pool = next((p for p in dashboard['pools'] if p['pool'].lower() == pool_addr), None)
            if pool:
                protocol_allocs_list.append({
                    "symbol": pool.get("symbol", ""),
                    "pool": pool_addr,
                    "votes": float(votes)
                })
        
        # Sort by votes (descending)
        protocol_allocs_list.sort(key=lambda x: x["votes"], reverse=True)
        
        # Add to result
        result["protocol_wallet"] = {
            "address": SHADOW_PROTOCOL_WALLET,
            "voting_power": float(protocol_voting_power),
            "allocations": protocol_allocs_list
        }

    if save:
        save_optimization(result, bot_output, is_historical)
        if not is_historical:
            save_calldata(result, owner)
    else:
        display_optimization(result)
        
        # Also display protocol wallet allocations if available
        if 'protocol_wallet' in result:
            print("\n=========== PROTOCOL WALLET ALLOCATIONS ===========")
            print(f"Protocol Wallet: {SHADOW_PROTOCOL_WALLET[:10]}...")
            print(f"Voting Power: {result['protocol_wallet']['voting_power']}")
            print("---------------------------------------------------")
            print("Pool                                    Votes")
            print("---------------------------------------------------")
            
            for alloc in result['protocol_wallet']['allocations'][:10]:
                symbol = alloc.get("symbol", "").ljust(10)
                votes = f"{alloc.get('votes', 0):.2f}".rjust(8)
                print(f"{symbol} ({alloc.get('pool')[:10]}...) {votes}")
            
            if len(result['protocol_wallet']['allocations']) > 10:
                print(f"... and {len(result['protocol_wallet']['allocations']) - 10} more pools")
            print("===================================================\n")

    return result