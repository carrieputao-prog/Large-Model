import os
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx
import sys
from datetime import datetime, date

# ── 环境变量 ──────────────────────────────────────────
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
DINGTALK_WEBHOOK = os.environ["DINGTALK_WEBHOOK"]
DINGTALK_SECRET  = os.environ["DINGTALK_SECRET"]

# ── 时间段映射（北京时间小时 → 推送风格） ──────────────
SLOT_CONFIG = {
    3:  {"label": "晨读", "emoji": "🌅", "slot_no": 0, "style": "偏重概念理解，适合早晨清醒头脑，语言简洁有力"},
    18: {"label": "晚间", "emoji": "🌙", "slot_no": 1, "style": "语言轻松有趣，适合傍晚回味，结尾留一个思考题"},
}

# ── 文件路径 ──────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TOPICS_FILE   = os.path.join(BASE_DIR, "topics.json")
PROGRESS_FILE = os.path.join(BASE_DIR, "progress.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_slot():
    """根据当前UTC小时推断北京时间时段"""
    # 命令行手动指定优先：python knowledge_push.py 3
    if len(sys.argv) > 1:
        try:
            slot = int(sys.argv[1])
            if slot in SLOT_CONFIG:
                return slot
            raise ValueError(f"仅支持时段：{sorted(SLOT_CONFIG)}")
        except ValueError:
            print(f"⚠️ 无效时段参数：{sys.argv[1]}，将按当前时间自动判断")
    utc_hour = datetime.utcnow().hour
    beijing_hour = (utc_hour + 8) % 24
    slots = sorted(SLOT_CONFIG)
    for s in slots:
        if abs(beijing_hour - s) <= 1:
            return s
    return 3


def get_active_topics(topics_data):
    """只返回当前保留分类下的 active 词条。"""
    allowed_modules = {"基础认知层", "工程与应用层", "前沿与趋势层"}
    return [
        t for t in topics_data["topics"]
        if t.get("status") == "active" and t.get("module") in allowed_modules
    ]


def get_topic_for_slot(topics_data, progress_data, slot):
    """
    一天2次各推不同词条。
    base_index = current_index * 2（每天消耗2个）
    slot_no 0/1 对应当天第1/2个词
    """
    active    = get_active_topics(topics_data)
    total     = len(active)
    slot_no   = SLOT_CONFIG[slot]["slot_no"]
    base_idx  = progress_data["current_index"] * len(SLOT_CONFIG)
    idx       = (base_idx + slot_no) % total
    learned   = base_idx + slot_no  # 累计已学词数
    return active[idx], idx, total, learned


def advance_progress(progress_data, topics_data, slot):
    """最后一个时段（18点）推送完后，day_index+1"""
    if slot != 18:
        return progress_data
    active    = get_active_topics(topics_data)
    total     = len(active)
    today_str = date.today().isoformat()
    base_idx  = progress_data["current_index"] * len(SLOT_CONFIG)
    # 记录今天学的2个词
    terms_today = [active[(base_idx + i) % total]["term"] for i in range(len(SLOT_CONFIG))]
    progress_data["daily_log"].append({
        "date": today_str,
        "day_index": progress_data["current_index"],
        "terms": terms_today
    })
    progress_data["current_index"] += 1
    # 每 ceil(total/2) 天跑完一轮
    days_per_round = (total + len(SLOT_CONFIG) - 1) // len(SLOT_CONFIG)
    if progress_data["current_index"] >= days_per_round:
        progress_data["current_index"] = 0
        progress_data["round"] += 1
    progress_data["last_updated"] = today_str
    return progress_data


def generate_knowledge(term, slot, progress_data, topics_data, learned, total):
    """调用 Gemini API 生成知识卡片"""
    slot_cfg  = SLOT_CONFIG[slot]
    round_num = progress_data["round"]
    remaining = total - ((progress_data["current_index"] * 4) + slot_cfg["slot_no"])

    round_note = ""
    if round_num > 1:
        round_note = f"这是第{round_num}轮学习，请换一个全新的角度和例子来讲解，避免与上一轮重复。"

    prompt = f"""你是一位大模型领域的资深讲师，现在需要给一位从事ToB业务的AI产品销售/BD讲解一个知识点。

今日词条：**{term}**
推送时段：{slot_cfg['label']}（{slot_cfg['style']}）
{round_note}

请严格按以下格式输出，不要添加任何多余内容：

{slot_cfg['emoji']} 葡萄大模型日课 · {slot_cfg['label']}

📌 **{term}**

📖 **定义**
用1-2句话给出准确定义。

💡 **通俗解释**
用一个生活化的比喻解释，让完全不懂技术的人也能秒懂（"就像..."句式）。

🏢 **ToB业务举例**
结合文旅、地图、政府数字化或企业服务场景，给一个具体的应用例子，说明这个概念在实际业务中怎么用、能解决什么问题。

🔀 **容易混淆的概念**
列出1-3个相近或容易混淆的词，每个用一句话说明区别。

⚡ **核心差别**
一句话总结最本质的区分点。

---
📊 **学习进度**
第 {round_num} 轮 · 今日第 {slot_cfg['slot_no']+1}/2 条 · 累计已学 {learned} 词 · 还剩 {remaining} 词
进度：{"█" * (learned * 10 // total)}{"░" * (10 - learned * 10 // total)} {learned * 100 // total}%"""

    response = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        params={"key": GEMINI_API_KEY},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def get_dingtalk_sign():
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{DINGTALK_SECRET}"
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_to_dingtalk(content):
    timestamp, sign = get_dingtalk_sign()
    url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "🍇 葡萄大模型日课 · AI知识推送",
            "text": content
        }
    }
    resp = httpx.post(url, json=payload, timeout=30)
    result = resp.json()
    if result.get("errcode") == 0:
        print("✅ 推送成功！")
    else:
        print(f"❌ 推送失败：{result}")
        raise RuntimeError(f"钉钉推送失败: {result}")


def save_to_grape_data(content: str, slot: int, date_str: str):
    """将当次知识卡片追加写入 grape-data/daily-knowledge/YYYY-MM-DD.md"""
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not github_token:
        print("⚠️ 未设置 GITHUB_TOKEN，跳过保存到 grape-data")
        return

    github_repo = "carrieputao-prog/grape-data"
    file_path   = f"daily-knowledge/{date_str}.md"
    url         = f"https://api.github.com/repos/{github_repo}/contents/{file_path}"
    headers     = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    sha      = None
    existing = ""
    check    = httpx.get(url, headers=headers, timeout=30)
    if check.status_code == 200:
        sha      = check.json()["sha"]
        existing = base64.b64decode(check.json()["content"]).decode("utf-8")

    slot_label  = SLOT_CONFIG[slot]["label"]
    new_content = existing + "\n\n---\n\n" + content if existing else content

    payload = {
        "message": f"📚 Agent2 大模型日课 {date_str} {slot_label}",
        "content": base64.b64encode(new_content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha

    resp = httpx.put(url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        print(f"✅ 已保存到 grape-data/daily-knowledge/{date_str}.md")
    else:
        print(f"⚠️ 保存到 grape-data 失败：{resp.status_code} {resp.text}")


if __name__ == "__main__":
    topics_data   = load_json(TOPICS_FILE)
    progress_data = load_json(PROGRESS_FILE)
    slot          = get_slot()

    print(f"当前时段：{SLOT_CONFIG[slot]['label']}（北京时间 {slot}:00）")

    topic, idx, total, learned = get_topic_for_slot(topics_data, progress_data, slot)
    print(f"今日词条：{topic['term']}（总第 {idx+1}/{total} 个，今日第 {SLOT_CONFIG[slot]['slot_no']+1}/2 条）")

    content = generate_knowledge(topic["term"], slot, progress_data, topics_data, learned, total)
    print("内容生成完毕，推送中...")

    send_to_dingtalk(content)

    print("保存到 grape-data...")
    today_str = datetime.now().strftime("%Y-%m-%d")
    save_to_grape_data(content, slot, today_str)

    # 最后一个时段推送完后更新进度
    progress_data = advance_progress(progress_data, topics_data, slot)
    save_json(PROGRESS_FILE, progress_data)
    print("进度已更新")
