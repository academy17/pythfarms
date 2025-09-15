#!/usr/bin/env python3
# filepath: scripts/aero/aero_manager.py
import os
import argparse
import logging
from dotenv import load_dotenv
from lib import fetch_votes, optimizer, analytics, fetch_lp_data, fetch_lp_data_previous_epoch, lp_optimized

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Aerodrome Voting Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch vote data and create dashboard")
    fetch_parser.add_argument("--historical", action="store_true", help="Fetch historical data")
    
    # Optimize command
    optimize_parser = subparsers.add_parser("optimize", help="Optimize vote allocation")
    optimize_parser.add_argument("--display", action="store_true", help="Display results instead of saving")
    
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
    lp_optimized_parser = subparsers.add_parser("lp_optimized", help="Generate optimized LP dashboard with simulated vote allocation")
    lp_optimized_parser.add_argument("--votes", help="Path to votes dashboard JSON", default="input_data/aero/votes_dashboard.json")
    lp_optimized_parser.add_argument("--lp", help="Path to LP dashboard JSON", default="lp_dashboard/aero/lp_dashboard.json")
    lp_optimized_parser.add_argument("--output", help="Path to save optimized LP dashboard", default="lp_optimized/aero/optimized_lp.json")
    lp_optimized_parser.add_argument("--top", type=int, default=30, help="Number of top pools to display")
    lp_optimized_parser.add_argument("--no-display", action="store_true", help="Don't display the dashboard in terminal")
    
    args = parser.parse_args()
    
    if args.command == "fetch":
        logger.info("Fetching votes data")
        fetch_votes.run_fetch(is_historical=args.historical)
    elif args.command == "optimize":
        logger.info("Optimizing votes")
        save = not args.display
        optimizer.run_optimize(save=save)
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
    elif args.command == "lp_optimized":
        logger.info("Generating optimized LP dashboard")
        lp_optimized.run_lp_optimized(
            votes_path=args.votes,
            lp_path=args.lp,
            output_path=args.output,
            display=not args.no_display,
            top_n=args.top
        )
    else:
        parser.print_help()

if __name__ == "__main__":
    main()