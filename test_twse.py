import datetime
import requests
import warnings

warnings.filterwarnings('ignore')

check_date = datetime.datetime.now()
date_str = check_date.strftime("%Y%m%d")
url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"
print(f"URL: {url}")
resp = None
try:
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
except requests.exceptions.SSLError:
    print("SSL Error caught, retrying without verify")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, verify=False)
except Exception as e:
    print(f"Other fetch error: {e}")

if resp:
    print(f"Status Code: {resp.status_code}")
    print(f"Response raw: {resp.text[:100]}")
    try:
        result = resp.json()
        print(f"Stat: {result.get('stat')}")
    except Exception as e:
        print(f"JSON target error: {e}")

