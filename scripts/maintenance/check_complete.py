import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

from modules.data_fetcher import DataFetcher
from modules.fetch_result import unwrap_fetch_result

load_dotenv(os.path.join(BASE_DIR, ".env"))
fetcher = DataFetcher(os.getenv("TAVILY_API_KEY", "dummy"))
data = unwrap_fetch_result(
    fetcher.get_institutional_data(top_n=3),
    default={"top_buy": [], "top_sell": [], "data_date": None},
)

if data.get("top_buy") or data.get("top_sell"):
    print(f"Data Date: {data.get('data_date')}")
    print(f"Stats: Found top_buy and top_sell. Data is out and processed.")
else:
    print("No data found or incomplete for today.")
