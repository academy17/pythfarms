#!/usr/bin/env python3
# filepath: scripts/aero/aero_manager.py
import os
import argparse
import logging
from dotenv import load_dotenv
from lib import fetch_votes, optimizer, analytics

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
    else:
        parser.print_help()

if __name__ == "__main__":
    main()