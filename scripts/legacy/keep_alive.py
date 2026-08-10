import time
import requests
import datetime
import schedule

# 如果您的 Render 網址不一樣，請在這邊修改
TARGET_URL = "https://smart-financial-report-mvp.onrender.com"

def ping_website():
    """發送請求來維持網站喚醒狀態"""
    now = datetime.datetime.now()
    
    # 檢查是否在允許連線的時間內 (早上 6 點到晚上 11:59 點)
    if 6 <= now.hour <= 23:
        try:
            print(f"[{now.strftime('%H:%M:%S')}] 執行防睡眠請求...")
            response = requests.get(TARGET_URL, timeout=10)
            print(f"  狀態碼: {response.status_code}")
        except Exception as e:
            print(f"  連線失敗: {str(e)}")
    else:
        print(f"[{now.strftime('%H:%M:%S')}] 目前為深夜省電時段，讓伺服器休息 😴")

print("啟動防睡眠腳本...")
print("工作時段：每天 06:00 到 23:59")
print("發送頻率：每 14 分鐘一次")
print("-" * 30)

# 馬上執行第一次
ping_website()

# 設定每 14 分鐘執行一次
schedule.every(14).minutes.do(ping_website)

# 保持腳本運行
while True:
    try:
        schedule.run_pending()
        time.sleep(1)
    except KeyboardInterrupt:
        print("\n腳本已終止。")
        break
