#!/usr/bin/env python3
# filepath: scripts/aero/aero_manager.py
import os
import argparse
import logging
from dotenv import load_dotenv
from lib import fetch_votes, optimizer, analytics, fetch_lp_data

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
    else:
        parser.print_help()

if __name__ == "__main__":
    main()