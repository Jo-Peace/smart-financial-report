import os
import glob
import time
from dotenv import load_dotenv
from modules.knowledge_manager import KnowledgeUpdater

def main():
    print("🚀 [Seed Knowledge Wiki] 開始歷史記憶灌輸程序...")
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ 找不到 GEMINI_API_KEY，請確認 .env 設定")
        return

    updater = KnowledgeUpdater(api_key)
    
    # 尋找所有歷史 reports
    base_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(base_dir, "reports", "archive", "01_Daily_Reports")
    
    # 找出所有 markdown 報表
    md_files = glob.glob(os.path.join(reports_dir, "daily_report_V21_*.md"))
    # 加入以防萬一的新報表
    md_files.extend(glob.glob(os.path.join(base_dir, "reports", "daily_report_V21_*.md")))
    
    # 確保按照檔名時間順序 (早期 -> 近期)
    md_files.sort() 
    
    # 為了獲得最精華且不過度耗費 Token，我們抓取最近 10 次的報告進行提煉 (約涵蓋近半個多月的市場波動)
    selected_files = md_files[-10:] if len(md_files) > 10 else md_files
    
    print(f"共找到 {len(md_files)} 份總計歷史報表。")
    print(f"將依序抽取最近 {len(selected_files)} 份關鍵報表進行知識庫濃縮注入...")
    
    for idx, filepath in enumerate(selected_files):
        filename = os.path.basename(filepath)
        print(f"\n=============================================")
        print(f"[{idx+1}/{len(selected_files)}] 正在處理與閱讀: {filename}")
        print(f"=============================================")
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # 觸發更新
            updater.update_knowledge_base(content)
            
            # 暫停 3 秒，避免觸發 API 頻率限制 (Rate Limit)
            time.sleep(3)
            
        except Exception as e:
            print(f"處理 {filename} 時發生錯誤: {e}")
            
    print("\n✅ 所有歷史精華已經成功複寫灌輸完畢！現在可開啟 Wiki 檔案查看成果！")

if __name__ == "__main__":
    main()
