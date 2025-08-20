

import os
import json
import requests
import logging
from decimal import Decimal, getcontext, ROUND_HALF_UP
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
getcontext().prec = 28


AERO_SLUG = os.getenv('AERO_SLUG', 'aerodrome-finance')
SIMPLE_PRICE_URL = 'https://api.coingecko.com/api/v3/simple/price'


DASHBOARD_PATH = "input_data/aero/votes_dashboard.json"
HUMAN_ALLOC_PATH = "optimized_votes/aero/optimized_votes_human.json"
TOKEN_ID_MAP_PATH = "data/aero/token_to_id.json"
OUTPUT_PATH = "analytics/aero/analytics_report.json"

def load_json(path):
    """Load a JSON file"""
    if not os.path.exists(path):
        logger.error(f"❌ {path} not found")
        return None
    with open(path, 'r') as f:
        return json.load(f)

def save_json(data, path):
    """Save data to a JSON file"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    logger.info(f"✅ Saved data to {path}")

def fetch_price(slug):
    """Fetch current USD price from CoinGecko"""
    params = {"ids": slug, "vs_currencies": "usd"}
    try:
        resp = requests.get(SIMPLE_PRICE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        price = data.get(slug, {}).get("usd")
        if price is None:
            raise ValueError(f"No price for {slug}")
        return Decimal(str(price))
    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        return Decimal(0)

def compute_actual_return(dashboard, current_votes=None):
    """
    Calculate the actual return from our votes using the dashboard data
    
    Args:
        dashboard: The votes dashboard with pool data
        current_votes: Optional override for current vote allocation
    
    Returns:
        Decimal of expected USD return
    """
    total_return = Decimal(0)
    pools = dashboard.get("pools", [])
    
    if not current_votes:
        
        for pool in pools:
            pool_addr = pool["pool"].lower()
            our_votes = Decimal(str(pool.get("our_votes", 0)))
            total_weight = Decimal(str(pool.get("weight", 0)))
            total_usd = Decimal(str(pool.get("total_usd", 0)))
            
            if our_votes > 0 and total_weight > 0:
                fraction = our_votes / total_weight
                pool_return = total_usd * fraction
                total_return += pool_return
    else:
        
        for vote in current_votes:
            pool_addr = vote["pool"].lower()
            our_votes = Decimal(str(vote.get("votes", 0)))
            
            
            for pool in pools:
                if pool["pool"].lower() == pool_addr:
                    total_weight = Decimal(str(pool.get("weight", 0)))
                    total_usd = Decimal(str(pool.get("total_usd", 0)))
                    
                    if our_votes > 0 and total_weight > 0:
                        fraction = our_votes / total_weight
                        pool_return = total_usd * fraction
                        total_return += pool_return
                    break
    
    return total_return

def run_analyze(compare=False, dashboard_path=None, optimizer_path=None):
    """
    Run analytics on voting data
    
    Args:
        compare: Whether to compare with optimal allocation
        dashboard_path: Path to dashboard file (optional)
        optimizer_path: Path to optimizer results file (optional)
    """
    logger.info("Starting vote analytics")
    
    
    dash_path = dashboard_path or DASHBOARD_PATH
    dash = load_json(dash_path)
    if not dash:
        return None
    
    
    opt_path = optimizer_path or HUMAN_ALLOC_PATH
    alloc = load_json(opt_path)
    if not alloc:
        return None
    
    
    our_power = Decimal(str(dash.get("our_voting_power", 0)))
    total_expected = Decimal(str(alloc.get("total_expected_usd", 0)))
    
    
    aero_price = fetch_price(AERO_SLUG)
    
    
    nft_value = (aero_price * our_power).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    apr = Decimal(0)
    if nft_value > 0:
        apr = (total_expected * Decimal(52) / nft_value * Decimal(100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    
    report = {
        "our_voting_power": float(our_power),
        "aero_price_usd": float(aero_price),
        "nft_value_usd": float(nft_value),
        "total_expected_usd_per_epoch": float(total_expected),
        "forecasted_apr_percent": float(apr),
        "current_votes": {
            "allocations": alloc.get("allocations", [])
        }
    }
    
    
    if compare:
        
        actual_return = compute_actual_return(dash)
        
        
        optimal_return = total_expected
        
        
        difference = optimal_return - actual_return
        
        
        report["comparison"] = {
            "actual_expected_return": float(actual_return),
            "optimal_expected_return": float(optimal_return),
            "difference": float(difference)
        }
        
        
        if actual_return > 0:
            pct_improvement = (difference / actual_return * Decimal(100)).quantize(Decimal('0.01'))
            report["comparison"]["improvement_percent"] = float(pct_improvement)
    
    
    date_str = datetime.now().strftime('%Y%m%d')
    output_path = f"analytics/aero/analytics_report_{date_str}.json"
    save_json(report, output_path)
    
    
    save_json(report, OUTPUT_PATH)
    
    
    print("\n================ ANALYTICS SUMMARY ================")
    print(f"Voting Power: {our_power}")
    print(f"Token Price: ${aero_price}")
    print(f"NFT Value: ${nft_value}")
    print(f"Expected USD per Epoch: ${total_expected}")
    print(f"Forecasted APR: {apr}%")
    
    if compare and "comparison" in report:
        comp = report["comparison"]
        print("\n----------- Performance Comparison -----------")
        print(f"Actual Expected Return: ${comp['actual_expected_return']}")
        print(f"Optimal Return: ${comp['optimal_expected_return']}")
        print(f"Difference: ${comp['difference']}")
        if 'improvement_percent' in comp:
            print(f"Potential Improvement: {comp['improvement_percent']}%")
    
    print("==================================================\n")
    
    return report