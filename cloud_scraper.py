import requests
from bs4 import BeautifulSoup
import urllib.parse
import json
import time
import os
import random
from datetime import datetime, timezone, timedelta

# Configuration
TOP_URL = "https://www.suisin.city.nagoya.jp/system/institution/index.cgi?action=inst_list&class="
BASE_URL = "https://www.suisin.city.nagoya.jp/system/institution/"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DATA_FILE = "state.json"
HISTORY_FILE = "history.html"

# Set timezone to JST
JST = timezone(timedelta(hours=+9), 'JST')

def get_now_jst():
    return datetime.now(JST)

def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('utf-8', 'replace').decode('utf-8', 'ignore'))

def polite_sleep(min_sec=2, max_sec=4):
    """Sleeps for a random duration to be gentle on the server."""
    delay = random.uniform(min_sec, max_sec)
    time.sleep(delay)

def get_soup(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.8"
    }
    response = requests.get(url, headers=headers)
    response.encoding = response.apparent_encoding
    return BeautifulSoup(response.text, 'html.parser')

def load_state():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "known_slots": {}, # key: "CenterName_YYYY-MM-DD_morning/afternoon/evening"
        "history": [] # list of {"timestamp": "...", "message": "..."}
    }

def save_state(state):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_discord_notification(content):
    if not WEBHOOK_URL:
        safe_print("Webhook URL not configured.")
        return
    data = {
        "content": content,
        "username": "名古屋市体育館 空き通知BOT"
    }
    try:
        requests.post(WEBHOOK_URL, json=data)
        safe_print("Sent notification")
        time.sleep(1) # Rate limit protection for Discord API
    except Exception as e:
        safe_print("Failed to send webhook")

def update_html_history(history):
    html_content = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>体育館 土日空き状況履歴</title>
    <style>
        body { font-family: sans-serif; padding: 20px; background-color: #f4f4f9; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; }
        .log-entry { padding: 10px; border-bottom: 1px solid #eee; }
        .timestamp { color: #888; font-size: 0.9em; margin-bottom: 4px; display: block; }
        .message { font-size: 1.1em; color: #d9534f; font-weight: bold; }
        .empty-history { color: #666; font-style: italic; }
    </style>
</head>
<body>
    <div class="container">
        <h1>全生涯学習センター 体育館 土日新規空き履歴</h1>
        <div id="history-logs">
"""
    if not history:
        html_content += '            <p class="empty-history">現在、履歴はありません。</p>\n'
    else:
        # Show newest first
        for entry in reversed(history):
            html_content += f"""            <div class="log-entry">
                <span class="timestamp">{entry['timestamp']}</span>
                <span class="message">{entry['message']}</span>
            </div>\n"""
            
    html_content += """        </div>
    </div>
</body>
</html>"""
    
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        f.write(html_content)

def is_weekend(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() >= 5 # 5=Sat, 6=Sun
    except:
        return False

def translate_slot(slot_name):
    return {"morning": "午前", "afternoon": "午後", "evening": "夜間"}.get(slot_name, slot_name)

def process_slot(state, center_name, date_str, slot_name, status, now):
    # e.g., "北生涯学習センター_2026-03-01_morning"
    slot_id = f"{center_name}_{date_str}_{slot_name}"
    
    if status == "空き" and is_weekend(date_str):
        # NEW SLOT FOUND
        if slot_id not in state["known_slots"]:
            safe_print(f"NEW SLOT DISCOVERED: 【{center_name}】 {date_str} {translate_slot(slot_name)}")
            ts_str = now.strftime("%Y/%m/%d %H:%M:%S")
            msg = f"🎉 【新規空き発見！】 **{center_name}** の **{date_str}** の **{translate_slot(slot_name)}** に空きが出ました！"
            
            state["known_slots"][slot_id] = {
                "found_at": int(now.timestamp()),
                "burst_count": 0,
                "reminders_sent": {"12": False, "15": False, "19": False}
            }
            state["history"].append({
                "timestamp": ts_str,
                "message": msg
            })
            
    elif slot_id in state["known_slots"]:
        # The slot is booked or no longer available, remove it
        safe_print(f"Slot 【{center_name}】 {date_str} {translate_slot(slot_name)} is no longer available. Removing from known_slots.")
        del state["known_slots"][slot_id]

def crawl_centers():
    safe_print(f"Fetching center list: {TOP_URL}...")
    soup = get_soup(TOP_URL)
    centers = []
    
    for li in soup.find_all('li'):
        h4 = li.find('h4')
        if not h4:
            continue
            
        a_view = li.find('a', href=lambda href: href and 'action=inst_view' in href and 'inst_key=' in href)
        if a_view:
            text = h4.text.strip()
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(a_view['href']).query)
            inst_key = qs.get('inst_key', [''])[0]
            if text and inst_key:
                centers.append({"name": text, "inst_key": inst_key})
                
    unique_centers = []
    seen = set()
    for c in centers:
        if c["inst_key"] not in seen:
            seen.add(c["inst_key"])
            unique_centers.append(c)
            
    return unique_centers

def find_gym_room_key(center):
    inst_view_url = f"{BASE_URL}index.cgi?action=inst_view&inst_key={center['inst_key']}&class="
    csoup = get_soup(inst_view_url)
    
    for a in csoup.find_all('a', href=True):
        if 'action=inst_room_view' in a['href'] and 'key=' in a['href']:
            text = a.text.strip()
            if "体育" in text:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(a['href']).query)
                return qs.get('key', [''])[0], a['href']
    return None, None

def check_gym_months(center_name, gym_key, gym_url, state, now):
    gsoup = get_soup(gym_url)
    months = []
    
    # The default gym_url is the current month. Add it explicitly.
    current_month_str = now.strftime("%Y-%m")
    months.append((current_month_str, gym_url))

    for a in gsoup.find_all('a', href=True):
        if 'action=inst_room_view' in a['href'] and 'year=' in a['href'] and 'month=' in a['href']:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(a['href']).query)
            y = qs.get('year', [''])[0]
            m = qs.get('month', [''])[0]
            month_str = f"{y}-{m}"
            if month_str not in [x[0] for x in months]:
                months.append((month_str, urllib.parse.urljoin(BASE_URL, a['href'])))
                
    safe_print(f"  > Found {len(months)} available months for {center_name}.")
    
    for month_str, month_url in months:
        safe_print(f"    -> Checking {month_str}...")
        polite_sleep(1, 2)
        
        msoup = get_soup(month_url)
        day_links = []
        for a in msoup.find_all('a', href=True):
            if 'action=inst_day_view' in a['href']:
                full_url = urllib.parse.urljoin(BASE_URL, a['href'])
                if full_url not in day_links:
                    day_links.append(full_url)
                    
        for link in day_links:
            # Some centers do not include the room 'key=' in the day links on the calendar.
            # We must explicitly add it if missing, otherwise the day view won't show the gym room!
            if 'key=' not in link and gym_key:
                link += f"&key={gym_key}"
                
            parsed_url = urllib.parse.urlparse(link)
            qs = urllib.parse.parse_qs(parsed_url.query)
            date_str = f"{qs.get('year', [''])[0]}-{qs.get('month', [''])[0]}-{qs.get('day', [''])[0]}"
            
            if not is_weekend(date_str):
                continue
                
            polite_sleep(2, 4) # Sleep before checking the day details
            day_soup = get_soup(link)
            
            # Find the gym th row
            gym_th = None
            for th in day_soup.find_all('th'):
                if th.find('a') and 'key=' in th.find('a').get('href', ''):
                    text = th.find('a').text.strip()
                    if "体育" in text:
                        gym_th = th
                        break
            
            if gym_th:
                m_tr = gym_th.parent
                a_tr = m_tr.find_next_sibling('tr')
                e_tr = a_tr.find_next_sibling('tr')
                
                def extract_status(tr):
                    if not tr: return "不明"
                    for td in tr.find_all('td'):
                        strong = td.find('strong')
                        # Some centers say '予約状況', others '予約'. The presence of strong and img is what matters.
                        if strong:
                            img = td.find('img')
                            if img and img.has_attr('alt'):
                                return img['alt']
                    return "不明"
                    
                m_stat = extract_status(m_tr)
                a_stat = extract_status(a_tr)
                e_stat = extract_status(e_tr)
                
                process_slot(state, center_name, date_str, "morning", m_stat, now)
                process_slot(state, center_name, date_str, "afternoon", a_stat, now)
                process_slot(state, center_name, date_str, "evening", e_stat, now)


def main():
    safe_print(f"Starting execution at {get_now_jst().strftime('%Y/%m/%d %H:%M:%S')}")
    state = load_state()
    now = get_now_jst()
    
    # SCRAPING LOGIC
    centers = crawl_centers()
    safe_print(f"Found {len(centers)} lifelong learning centers.")
    
    for i, c in enumerate(centers):
        safe_print(f"[{i+1}/{len(centers)}] Processing {c['name']}...")
        polite_sleep(1, 3) 
        
        gym_key, gym_href = find_gym_room_key(c)
        if gym_key:
            safe_print(f"  > Gym identified (Key: {gym_key})")
            gym_url = urllib.parse.urljoin(BASE_URL, gym_href)
            check_gym_months(c['name'], gym_key, gym_url, state, now)
        else:
            safe_print(f"  > No Gym room found for {c['name']}. Skipping.")

    # PROCESS NOTIFICATIONS (bursts and reminders)
    current_hour_str = str(now.hour)
    
    for slot_id, slot_data in list(state["known_slots"].items()):
        
        # Parse slot_id: "北生涯学習センター_2026-03-01_morning"
        parts = slot_id.split("_")
        if len(parts) == 3:
            center_name, date_str, slot_name = parts
        else:
            continue
            
        ts_str = datetime.fromtimestamp(slot_data["found_at"], JST).strftime("%H:%M:%S")
        
        if slot_data["burst_count"] < 10:
            safe_print(f"Handling bursts for 【{center_name}】 {date_str} {translate_slot(slot_name)} (Sent: {slot_data['burst_count']}/10)")
            while slot_data["burst_count"] < 10:
                msg = f"🔴 【至急】 **{center_name}** の **{date_str}** **{translate_slot(slot_name)}** に予約可能な空きがあります！\n即確認してください！ (通知 {slot_data['burst_count']+1}/10)"
                send_discord_notification(msg)
                slot_data["burst_count"] += 1
                save_state(state) 
                if slot_data["burst_count"] < 10:
                    safe_print("Waiting 60 seconds before next burst...")
                    time.sleep(60)
        
        if current_hour_str in ["12", "15", "19"]:
            if not slot_data["reminders_sent"].get(current_hour_str, False):
                msg = f"⏰ 【 定期リマインド: {current_hour_str}時 】\nまだ **{center_name}** の **{date_str}** **{translate_slot(slot_name)}** に空きが残っています！"
                send_discord_notification(msg)
                slot_data["reminders_sent"][current_hour_str] = True
                save_state(state)

    # FINAL SAVE AND HTML GEN
    save_state(state)
    update_html_history(state["history"])
    safe_print("Execution finished successfully.")

if __name__ == "__main__":
    main()
