import sys
sys.path.append("/Users/shenghanchou/Desktop/stock/smart_financial_report")
from modules.data_fetcher import DataFetcher
import os
from dotenv import load_dotenv

load_dotenv("/Users/shenghanchou/Desktop/stock/smart_financial_report/.env")
fetcher = DataFetcher(os.getenv("TAVILY_API_KEY", "dummy"))
data = fetcher.get_institutional_data(top_n=3)

if data.get("top_buy") or data.get("top_sell"):
    print(f"Data Date: {data.get('data_date')}")
    print(f"Stats: Found top_buy and top_sell. Data is out and processed.")
else:
    print("No data found or incomplete for today.")
