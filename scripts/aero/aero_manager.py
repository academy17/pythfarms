#!/usr/bin/env python3
# filepath: scripts/aero/aero_manager.py
import os
import argparse
import logging
from decimal import Decimal
from dotenv import load_dotenv
from lib import fetch_votes, optimizer, analytics, fetch_lp_data, fetch_lp_data_previous_epoch, lp_optimizer, fetch_volatility

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Aerodrome Voting Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch vote data and create dashboard")
    fetch_parser.add_argument("--historical", action="store_true", help="Fetch historical data")

    # Fetch Previous command
    fetch_previous_parser = subparsers.add_parser("fetch-previous", help="Fetch vote data from previous epoch")
    
    # Optimize command
    optimize_parser = subparsers.add_parser("optimize", help="Optimize vote allocation")
    optimize_parser.add_argument("--display", action="store_true", help="Display results instead of saving")
    optimize_parser.add_argument("--previous", action="store_true", help="Use previous epoch's votes dashboard")
    optimize_parser.add_argument("--votes", help="Path to votes dashboard JSON", default="input_data/aero/votes_dashboard.json")
    optimize_parser.add_argument("--with-volatility", action="store_true", help="Apply volatility penalty to volatile pools")
    optimize_parser.add_argument("--gamma", type=float, default=1.0, help="Volatility penalty coefficient (default 1.0, higher = stronger penalty)")

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze votes")
    analyze_parser.add_argument("--compare", action="store_true", help="Compare with optimal allocation")
    
    # LP Dashboard command
    lp_dashboard_parser = subparsers.add_parser("lp_dashboard", help="Generate LP dashboard with APR calculations")
    lp_dashboard_parser.add_argument("--sizes", nargs="+", type=int, help="Custom investment sizes to calculate APR for (e.g. --sizes 5000 25000 100000)")
    lp_dashboard_parser.add_argument("--top", type=int, default=30, help="Number of top pools to display")
    lp_dashboard_parser.add_argument("--no-save", action="store_true", help="Don't save the dashboard to file")
    lp_dashboard_parser.add_argument("--no-display", action="store_true", help="Don't display the dashboard in terminal")    
    # LP Dashboard Previous Epoch command
    lp_dashboard_previous_parser = subparsers.add_parser("lp_dashboard_previous", help="Generate LP dashboard for previous epoch with APR calculations")
    lp_dashboard_previous_parser.add_argument("--sizes", nargs="+", type=int, help="Custom investment sizes to calculate APR for (e.g. --sizes 5000 25000 100000)")
    lp_dashboard_previous_parser.add_argument("--top", type=int, default=30, help="Number of top pools to display")
    lp_dashboard_previous_parser.add_argument("--no-save", action="store_true", help="Don't save the dashboard to file")
    lp_dashboard_previous_parser.add_argument("--no-display", action="store_true", help="Don't display the dashboard in terminal")
    
    # LP Optimized command
    lp_optimizer_parser = subparsers.add_parser("lp_optimizer", help="Generate optimized LP dashboard with simulated vote allocation")
    lp_optimizer_parser.add_argument("--votes", help="Path to votes dashboard JSON", default="input_data/aero/votes_dashboard.json")
    lp_optimizer_parser.add_argument("--output", help="Path to save optimized LP dashboard", default="lp_optimized/aero/optimized_lp.json")
    lp_optimizer_parser.add_argument("--top", type=int, default=30, help="Number of top pools to display")
    lp_optimizer_parser.add_argument("--no-display", action="store_true", help="Don't display the dashboard in terminal")
    
    # Fetch Volatility command
    volatility_parser = subparsers.add_parser("fetch_volatility", help="Fetch volatility data for pools")
    volatility_parser.add_argument("--max", type=int, default=500, help="Maximum number of pools to process")
    volatility_parser.add_argument("--rate-limit", type=int, default=2, help="Seconds to wait between API calls to avoid rate limiting")
    volatility_parser.add_argument("--force", action="store_true", help="Force update for all pools")
    
    args = parser.parse_args()
    
    if args.command == "fetch":
        logger.info("Fetching votes data")
        fetch_votes.run_fetch(is_historical=args.historical)
    elif args.command == "fetch-previous":
        logger.info("Fetching votes data from previous epoch")
        from lib import fetch_votes_previous_epoch
        result = fetch_votes_previous_epoch.run_fetch_votes_previous_epoch()
        if result:
            logger.info("✅ Successfully fetched previous epoch votes data")
    elif args.command == "optimize":
        logger.info("Optimizing votes")
        save = not args.display
        votes_path = "previous_votes/aero/previous_votes_dashboard.json" if args.previous else args.votes
        
        logger.info(f"Using votes dashboard: {votes_path}")
        
        # Run the optimizer with volatility if requested
        optimizer.run_optimize(save=save, votes_path=votes_path,
                              with_volatility=args.with_volatility, gamma=args.gamma)
    elif args.command == "analyze":
        logger.info("Analyzing votes")
        analytics.run_analyze(compare=args.compare)
    elif args.command == "lp_dashboard":
        logger.info("Generating LP dashboard")
        fetch_lp_data.run_fetch_lp_data(
            investment_sizes=args.sizes,
            display=not args.no_display,
            save=not args.no_save,
            top_n=args.top
        )
    elif args.command == "lp_dashboard_previous":
        logger.info("Generating LP dashboard for previous epoch")
        dashboard = fetch_lp_data_previous_epoch.run_fetch_lp_data_previous_epoch()
        
        # If dashboard was generated successfully and display is requested
        if dashboard and not args.no_display:
            # Display top pools by APR
            pools = dashboard.get("pools", [])
            logger.info(f"\n===== Top {min(args.top, len(pools))} Pools by APR (Previous Epoch) =====")
            for i, pool in enumerate(pools[:args.top]):
                logger.info(f"{i+1}. {pool.get('symbol', 'Unknown')} - APR: {pool.get('apr', 0):.2f}% - TVL: ${pool.get('tvl_usd', 0):,.2f}")
    elif args.command == "lp_optimizer":
        logger.info("Generating optimized LP dashboard")
        lp_optimizer.run_lp_optimized(
            votes_path=args.votes,
            output_path=args.output,
            display=not args.no_display,
            top_n=args.top
        )
    elif args.command == "fetch_volatility":
        logger.info("Fetching volatility data for pools")
        result = fetch_volatility.run_fetch_volatility(
            max_pools=args.max,
            rate_limit_seconds=args.rate_limit,
            force_update=args.force
        )
        # Display a summary of the volatility data
        if result and "pools" in result:
            pools_data = result["pools"]
            # Sort pools by volatility percentage
            sorted_pools = sorted(
                [(addr, data) for addr, data in pools_data.items() if "price_range" in data],
                key=lambda x: x[1]["price_range"]["volatility_percentage"],
                reverse=True
            )
            
            logger.info(f"\n===== Top {min(10, len(sorted_pools))} Most Volatile Pools =====")
            for i, (addr, data) in enumerate(sorted_pools[:10]):
                symbol = data.get("symbol", "Unknown")
                vol_pct = data["price_range"]["volatility_percentage"]
                price = data.get("current_price", 0)
                logger.info(f"{i+1}. {symbol} - Volatility: {vol_pct:.4f}% - Price: ${price:.6f}")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()