
import os
import json
from dotenv import load_dotenv

load_dotenv()

INPUT_PATH  = "data/aero/sugar_pools.json"
OUTPUT_PATH = "data/aero/votable_pools.json"

if not os.path.exists(INPUT_PATH):
    print(f"❌  Cannot find {INPUT_PATH}. Run get_sugar_pools.py first.")
    exit(1)


with open(INPUT_PATH) as f:
    all_pools = json.load(f)



zero_addr = "0x0000000000000000000000000000000000000000"
votable = [
    p for p in all_pools
    if p.get("gauge", zero_addr).lower() != zero_addr
    and p.get("gauge_alive", False) is True
]

print(f"🔍  Of {len(all_pools)} total pools, {len(votable)} are votable.")


votable.sort(key=lambda x: int(x["liquidity"]), reverse=True)


os.makedirs("data", exist_ok=True)
with open(OUTPUT_PATH, "w") as f:
    json.dump(votable, f, indent=2)

print(f"✅  Saved {len(votable)} votable pools to {OUTPUT_PATH}")
print("\n🏆 Top 5 votable pools by liquidity:")
for p in votable[:5]:
    print(f" • {p['symbol']} @ {p['lp']} (gauge={p['gauge']}, liq={int(p['liquidity']):,})")
