
import os
import json
import requests
from decimal import Decimal, getcontext, ROUND_HALF_UP
from web3 import Web3
from dotenv import load_dotenv
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
getcontext().prec = 28

load_dotenv()
SHADOW_SLUG = os.getenv('SHADOW_SLUG', 'shadow-2')
SIMPLE_PRICE_URL = 'https://api.coingecko.com/api/v3/simple/price'
SHADOW_RPC_URL = os.getenv('SHADOW_RPC_URL')
SHADOW_VOTER_ADDRESS = os.getenv('SHADOW_VOTER_ADDRESS')
SHADOW_NFT_OWNER_ADDRESS = os.getenv('SHADOW_NFT_OWNER_ADDRESS')
VOTER_ABI_PATH = os.getenv('VOTER_ABI_PATH', 'abi/shadow/Voter.json')

def load_json(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} not found")
    with open(path, 'r') as f:
        return json.load(f)

def fetch_price(slugs):
    params = {'vs_currencies': 'usd'}
    for slug in slugs:
        params['ids'] = slug
        resp = requests.get(SIMPLE_PRICE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        price = data.get(slug, {}).get('usd')
        if price is not None:
            return Decimal(str(price))
    raise ValueError(f"No valid price for slugs: {slugs}")

def fetch_onchain_voting_power(rpc_url, voter_contract, nft_owner_address, abi_path, period=None):
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        logger.error("Failed to connect to RPC node for voting power")
        return Decimal(0)
    abi = load_json(abi_path)
    contract = w3.eth.contract(address=w3.to_checksum_address(voter_contract), abi=abi)
    owner = w3.to_checksum_address(nft_owner_address)
    try:
        if period is None:
            period = contract.functions.getPeriod().call() + 1
            logger.info(f"On‐chain next voting period: {period}")
        raw = contract.functions.userVotingPowerPerPeriod(owner, period).call()
        normalized = Decimal(raw) / (Decimal(10) ** 18)
        logger.info(f"On‐chain voting power for period {period}: {normalized}")
        return normalized
    except Exception as e:
        logger.error(f"Error fetching on‑chain voting power: {e}")
        return Decimal(0)

def fetch_votes_for_period(rpc_url, voter_contract, nft_owner_address, abi_path, period):
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        logger.error("Failed to connect to RPC node")
        return None
    abi = load_json(abi_path)
    contract = w3.eth.contract(address=w3.to_checksum_address(voter_contract), abi=abi)
    owner_address = w3.to_checksum_address(nft_owner_address)
    try:
        num_pools = contract.functions.userVotedPoolsPerPeriodLength(owner_address, period).call()
        logger.info(f"Number of pools voted for in period {period}: {num_pools}")
        pools = []
        for i in range(num_pools):
            pool = contract.functions.userVotedPoolsPerPeriod(owner_address, period, i).call()
            pools.append(pool)
        
        votes = []
        for pool in pools:
            weight = contract.functions.userVotesForPoolPerPeriod(owner_address, period, pool).call()
            votes.append({'pool': pool, 'weight': weight})
        
        return {'period': period, 'votes': votes}
    except Exception as e:
        logger.error(f"Error fetching vote data for period {period}: {e}")
        return None

def compute_actual_return(historical_dashboard, user_votes):
    """Calculate the actual return from our votes using the historical dashboard data"""
    actual_return = Decimal(0)
    if not user_votes or 'votes' not in user_votes or not user_votes['votes']:
        logger.warning("No user votes to compute return")
        return actual_return
        
    logger.info(f"Computing actual return from {len(user_votes['votes'])} user votes")
    matched_pools = 0
    
    for vote in user_votes['votes']:
        pool = vote['pool'].lower()
        V_user = Decimal(vote['weight']) / Decimal(10**18)
        pool_found = False
        
        for p in historical_dashboard['pools']:
            if p['pool'].lower() == pool:
                pool_found = True
                matched_pools += 1
                W_final = Decimal(p['pool_votes_period'])
                R = Decimal(p.get('bribes_usd', 0))
                if W_final > 0 and R > 0:
                    pool_return = R * (V_user / W_final)
                    actual_return += pool_return
                    logger.info(f"Pool {p.get('symbol', pool)}: votes={V_user}, bribes=${R}, return=${pool_return}")
                break
                
        if not pool_found:
            logger.warning(f"Pool {pool} not found in dashboard")
    
    logger.info(f"Matched {matched_pools}/{len(user_votes['votes'])} pools, total expected return: ${actual_return}")
    return actual_return

def run_analyze(period=None, compare=False, is_historical=False, dashboard_path=None, optimizer_path=None):
    """
    Run analytics on voting data.
    
    Args:
        period: Period to analyze (default: next period for current, specified period for historical)
        compare: Whether to compare with optimal allocation
        is_historical: Whether this is a historical analysis
        dashboard_path: Explicit path to dashboard file (optional)
        optimizer_path: Explicit path to optimizer results file (optional)
    """
    if not (SHADOW_RPC_URL and SHADOW_VOTER_ADDRESS and SHADOW_NFT_OWNER_ADDRESS):
        logger.error("Missing RPC, contract, or owner configuration")
        return None
    
    
    w3 = Web3(Web3.HTTPProvider(SHADOW_RPC_URL))
    if not w3.is_connected():
        logger.error("Failed to connect to RPC node")
        return None
    
    with open(VOTER_ABI_PATH) as f:
        voter_abi = json.load(f)
    contract = w3.eth.contract(address=w3.to_checksum_address(SHADOW_VOTER_ADDRESS), abi=voter_abi)
    owner = w3.to_checksum_address(SHADOW_NFT_OWNER_ADDRESS)
    
    
    if period is None:
        if is_historical:
            period = int(input("Enter the historical period to analyze: "))
        else:
            
            period = contract.functions.getPeriod().call() + 1
            logger.info(f"Using next voting period: {period}")
    
    logger.info(f"Analyzing period {period}")
    
    
    if not optimizer_path:
        if is_historical:
            optimizer_path = f'optimized_votes/shadow/historical/{period}_historical_optimal_votes.json'
        else:
            optimizer_path = f'optimized_votes/shadow/{period}_optimized_votes_human.json'
            if not os.path.exists(optimizer_path):
                optimizer_path = 'optimized_votes/shadow/optimized_votes_human.json'
        
        if not os.path.exists(optimizer_path):
            logger.warning(f"Optimizer file not found at {optimizer_path}")
            optimizer_path = input(f"Enter path to optimizer results for period {period}: ")
    
    try:
        optimized_votes = load_json(optimizer_path)
        total_expected_usd = Decimal(str(optimized_votes.get('total_expected_usd', 0)))
        logger.info(f"Loaded optimization result from {optimizer_path}")
    except Exception as e:
        logger.error(f"Failed to load optimization result: {e}")
        total_expected_usd = Decimal(0)
    
    
    voting_power = fetch_onchain_voting_power(
        SHADOW_RPC_URL,
        SHADOW_VOTER_ADDRESS,
        SHADOW_NFT_OWNER_ADDRESS,
        VOTER_ABI_PATH,
        period
    )
    
    
    slugs = [s.strip() for s in SHADOW_SLUG.split(',')]
    try:
        price_usd = fetch_price(slugs)
        logger.info(f"Token price: ${price_usd}")
    except Exception as e:
        logger.error(f"Failed to fetch price: {e}")
        price_usd = Decimal(0)
    
    
    token_value = (price_usd * voting_power).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    # Calculate optimized votes APR
    optimized_votes_apr = (Decimal('0') if token_value == 0 else
                  (total_expected_usd * Decimal(52) / token_value * Decimal(100))
                  .quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    
    # Initialize with default report structure
    report = {
        'xshadow_holder': SHADOW_NFT_OWNER_ADDRESS,
        'our_voting_power': float(voting_power),
        'token_price_usd': float(price_usd),
        'token_value_usd': float(token_value),
        'total_expected_usd_this_epoch_optimizer': float(total_expected_usd),
        'optimized_votes_apr': float(optimized_votes_apr),
        'expected_apr_from_current_votes': 0.0,  # Default to 0 if no votes
    }
    
    
    
    # Fetch actual votes for the period
    actual_votes = fetch_votes_for_period(
        SHADOW_RPC_URL, SHADOW_VOTER_ADDRESS, SHADOW_NFT_OWNER_ADDRESS, VOTER_ABI_PATH, period
    )
    
    # Always try to find a dashboard file for current votes APR calculation
    if not dashboard_path:
        if is_historical:
            dashboard_path = f'input_data/shadow/historical/{period}_historical_votes_dashboard.json'
            if not os.path.exists(dashboard_path):
                dashboard_path = f'input_data/shadow/{period}_votes_dashboard.json'
        else:
            dashboard_path = f'input_data/shadow/{period}_votes_dashboard.json'
            if not os.path.exists(dashboard_path):
                dashboard_path = 'input_data/shadow/votes_dashboard.json'
        
        if not os.path.exists(dashboard_path):
            logger.warning(f"Dashboard file not found at {dashboard_path} for APR calculation")
    
    # Calculate APR from current votes if available
    current_votes_apr = Decimal('0')
    if actual_votes and 'votes' in actual_votes and len(actual_votes['votes']) > 0:
        actual_votes['period'] = period
        
        # If we have dashboard data, calculate the current votes APR
        if dashboard_path and os.path.exists(dashboard_path):
            try:
                dashboard = load_json(dashboard_path)
                actual_return = compute_actual_return(dashboard, actual_votes)
                
                if token_value > 0:
                    current_votes_apr = (actual_return * Decimal(52) / token_value * Decimal(100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                
                report['expected_apr_from_current_votes'] = float(current_votes_apr)
                logger.info(f"Current votes APR calculated: {current_votes_apr}%")
            except Exception as e:
                logger.error(f"Error calculating current votes APR: {e}")
    else:
        logger.info("No current votes found for APR calculation")
    
    report['current_votes'] = actual_votes
    
    
    if compare:
        # Dashboard path should already be set from earlier
        if not os.path.exists(dashboard_path):
            logger.warning(f"Dashboard file not found at {dashboard_path} for comparison")
            dashboard_path = input(f"Enter path to votes dashboard for period {period}: ")
        
        try:
            dashboard = load_json(dashboard_path)
            if dashboard['period'] != period:
                logger.warning(f"Period mismatch in dashboard: expected {period}, got {dashboard['period']}")
            
            
            if actual_votes and 'votes' in actual_votes:
                actual_return = compute_actual_return(dashboard, actual_votes)
                ideal_return = total_expected_usd
                difference = ideal_return - actual_return
                
                report['comparison'] = {
                    'period': period,
                    'last_period_expected': float(actual_return),
                    'last_period_ideal_return': float(ideal_return),
                    'difference': float(difference)
                }
                logger.info(f"Comparison for period {period} added to report")
                
                
                if actual_return > 0:
                    pct_improvement = (difference / actual_return * 100).quantize(Decimal('0.01'))
                    report['comparison']['improvement_percent'] = float(pct_improvement)
            else:
                logger.error("No votes found for comparison")
        except Exception as e:
            logger.error(f"Error in comparison: {e}")
    
    
    date_str = datetime.now().strftime('%Y%m%d')
    if is_historical:
        output_path = f'analytics/shadow/historical/{period}_vote_analytics_{date_str}.json'
    else:
        output_path = f'analytics/shadow/{period}_vote_analytics_{date_str}.json'
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2)
    logger.info(f"Analytics report written to {output_path}")
    
    
    if not is_historical:
        std_path = 'analytics/shadow/current_vote_analytics.json'
        os.makedirs(os.path.dirname(std_path), exist_ok=True)
        with open(std_path, 'w') as f:
            json.dump(report, f, indent=2)
    
    
    print("\n================ ANALYTICS SUMMARY ================")
    print(f"Period: {period}")
    print(f"Voting Power: {voting_power}")
    print(f"Token Price: ${price_usd}")
    print(f"Token Value: ${token_value}")
    print(f"Expected USD This Epoch (Optimized): ${total_expected_usd}")
    print(f"Current Votes Expected APR: {report['expected_apr_from_current_votes']}%")
    print(f"Optimized Votes Expected APR: {optimized_votes_apr}%")
    
    if compare and 'comparison' in report:
        comp = report['comparison']
        print("\n----------- Performance Comparison -----------")
        print(f"Actual Expected Return: ${comp['last_period_expected']}")
        print(f"Ideal Return: ${comp['last_period_ideal_return']}")
        print(f"Difference: ${comp['difference']}")
        if 'improvement_percent' in comp:
            print(f"Potential Improvement: {comp['improvement_percent']}%")
    
    print("==================================================\n")
    
    return report