import os
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# ── 环境变量 ──────────────────────────────────────────
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
DINGTALK_WEBHOOK = os.environ["DINGTALK_WEBHOOK"]
DINGTALK_SECRET  = os.environ["DINGTALK_SECRET"]

# ── 时间段映射（北京时间小时 → 推送风格） ──────────────
SLOT_CONFIG = {
    3:  {"label": "晨读", "emoji": "🌅", "module": "基础认知层", "style": "偏重概念理解，适合早晨清醒头脑，语言简洁有力"},
    12: {"label": "午间", "emoji": "☀️", "module": "工程与应用层", "style": "偏重实际应用和业务举例，适合中午快速充电"},
    18: {"label": "晚间", "emoji": "🌙", "module": "前沿与趋势层", "style": "语言轻松有趣，适合傍晚回味，结尾留一个思考题"},
}
ALLOWED_MODULES = ["基础认知层", "工程与应用层", "前沿与趋势层"]

# ── 文件路径 ──────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TOPICS_FILE   = os.path.join(BASE_DIR, "topics.json")
PROGRESS_FILE = os.path.join(BASE_DIR, "progress.json")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def beijing_today():
    return datetime.now(BEIJING_TZ).date().isoformat()


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
    return [
        t for t in topics_data["topics"]
        if t.get("status") == "active" and t.get("module") in ALLOWED_MODULES
    ]


def get_topics_by_module(topics_data):
    """按保留分类拆分 active 词条，保持 topics.json 中的原始顺序。"""
    topics_by_module = {module: [] for module in ALLOWED_MODULES}
    for topic in get_active_topics(topics_data):
        topics_by_module[topic["module"]].append(topic)
    return topics_by_module


def get_module_indexes(progress_data):
    """兼容旧进度文件：没有 module_indexes 时，用 current_index 初始化三个分类。"""
    fallback_index = progress_data.get("current_index", 0)
    module_indexes = progress_data.setdefault("module_indexes", {})
    for module in ALLOWED_MODULES:
        module_indexes.setdefault(module, fallback_index)
    return module_indexes


def get_topic_for_slot(topics_data, progress_data, slot):
    """
    一天3次，每个时段固定推一个分类：
    03:00 基础认知层、12:00 工程与应用层、18:00 前沿与趋势层。
    每个分类独立维护取词索引。
    """
    module = SLOT_CONFIG[slot]["module"]
    topics_by_module = get_topics_by_module(topics_data)
    module_topics = topics_by_module[module]
    if not module_topics:
        raise RuntimeError(f"分类 {module} 没有 active 词条")

    module_indexes = get_module_indexes(progress_data)
    raw_idx = module_indexes[module]
    idx = raw_idx % len(module_topics)
    learned = idx + 1
    return module_topics[idx], idx, len(module_topics), learned


def advance_progress(progress_data, topics_data, slot):
    """最后一个时段（18点）推送完后，day_index+1"""
    if slot != 18:
        return progress_data
    today_str = beijing_today()
    topics_by_module = get_topics_by_module(topics_data)
    module_indexes = get_module_indexes(progress_data)
    terms_today = []
    for module in ALLOWED_MODULES:
        module_topics = topics_by_module[module]
        idx = module_indexes[module] % len(module_topics)
        topic = module_topics[idx]
        terms_today.append({
            "module": module,
            "term": topic["term"]
        })
        module_indexes[module] += 1

    progress_data["daily_log"].append({
        "date": today_str,
        "day_index": progress_data["current_index"],
        "terms": terms_today
    })
    progress_data["current_index"] += 1
    # 三个分类独立轮转，最长分类跑完视作一轮完成
    days_per_round = max(len(items) for items in topics_by_module.values())
    if progress_data["current_index"] >= days_per_round:
        progress_data["current_index"] = 0
        progress_data["round"] += 1
        for module in ALLOWED_MODULES:
            module_indexes[module] = 0
    progress_data["last_updated"] = today_str
    return progress_data


def generate_knowledge(topic, slot, progress_data, topics_data, learned, total):
    """调用 Gemini API 生成知识卡片"""
    slot_cfg  = SLOT_CONFIG[slot]
    term      = topic["term"]
    module    = topic["module"]
    round_num = progress_data["round"]
    remaining = max(total - learned, 0)

    round_note = ""
    if round_num > 1:
        round_note = f"这是第{round_num}轮学习，请换一个全新的角度和例子来讲解，避免与上一轮重复。"

    prompt = f"""你是一位大模型领域的资深讲师，现在需要给一位从事ToB业务的AI产品销售/BD讲解一个知识点。

今日词条：**{term}｜{module}**
推送时段：{slot_cfg['label']}（{slot_cfg['style']}）
{round_note}

请严格按以下格式输出，不要添加任何多余内容：

{slot_cfg['emoji']} 葡萄大模型日课 · {slot_cfg['label']}

📌 **{term}｜{module}**

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
第 {round_num} 轮 · {module} · 本分类已学 {learned} 词 · 本分类还剩 {remaining} 词
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

    print(f"当前时段：{SLOT_CONFIG[slot]['label']}（北京时间 {slot}:00，{SLOT_CONFIG[slot]['module']}）")

    topic, idx, total, learned = get_topic_for_slot(topics_data, progress_data, slot)
    print(f"今日词条：{topic['term']}｜{topic['module']}（本分类第 {idx+1}/{total} 个）")

    content = generate_knowledge(topic, slot, progress_data, topics_data, learned, total)
    print("内容生成完毕，推送中...")

    send_to_dingtalk(content)

    print("保存到 grape-data...")
    today_str = beijing_today()
    save_to_grape_data(content, slot, today_str)

    # 最后一个时段推送完后更新进度
    progress_data = advance_progress(progress_data, topics_data, slot)
    save_json(PROGRESS_FILE, progress_data)
    print("进度已更新")
