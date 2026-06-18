"""
每周五自动巡逻：从多个渠道发现新 AI 术语，写入 pending_topics.json，钉钉通知审核
"""
import os
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import httpx
from datetime import date

GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO  = "carrieputao-prog/grape-data"
DINGTALK_WEBHOOK = os.environ["DINGTALK_WEBHOOK"]
DINGTALK_SECRET  = os.environ["DINGTALK_SECRET"]

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TOPICS_FILE   = os.path.join(BASE_DIR, "topics.json")



def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_existing_terms(topics_data):
    return {t["term"] for t in topics_data["topics"]}


def scan_new_terms(existing_terms):
    """让 Gemini 联网搜索本周新出现的 AI 术语"""
    existing_list = "\n".join(sorted(existing_terms))
    prompt = f"""你是一个AI领域术语追踪专家。请通过联网搜索，找出**本周**（最近7天）在以下渠道中出现的新AI技术术语或概念：

重点监控渠道：
- X（Twitter）：@karpathy, @fchollet, @lilianweng, @AnthropicAI, @OpenAI, @GoogleDeepMind, @AlphaSignalAI, @rowancheung
- Newsletter：The Batch (Andrew Ng), Import AI, TLDR AI, Latent Space, AlphaSignal
- Hacker News：AI相关高热帖（评论数>50）
- arXiv：本周高引AI论文中的新概念
- 清华姜学长 B站最新视频（搜索："清华姜学长 bilibili 最新视频 2026"）
- 晓辉博士 B站最新视频（搜索："晓辉博士 bilibili 最新视频 2026"）

以下术语已在词库中，**不要重复推荐**：
{existing_list}

请输出3-5个候选新词，严格按JSON格式返回，不要任何其他内容：
{{
  "candidates": [
    {{
      "term": "术语名称（中英文）",
      "brief": "一句话说明这是什么",
      "source": "发现来源（账号/媒体名）",
      "why_important": "为什么值得加入词库（10字以内）"
    }}
  ]
}}"""

    response = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}]
        },
        timeout=120,
    )
    response.raise_for_status()
    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]

    # 清理可能的代码块标记
    raw = raw.strip().removeprefix("```json").removeprefix("```").strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    return json.loads(raw)


def update_pending(pending_data, candidates, topics_data):
    """将候选词写入 pending，自动分配ID"""
    all_topics = topics_data["topics"]
    next_id = max(t["id"] for t in all_topics) + 100  # pending用100+偏移避免冲突
    today = date.today().isoformat()

    existing_pending_terms = {p["term"] for p in pending_data["pending"]}
    added = []

    for c in candidates:
        if c["term"] in existing_pending_terms:
            continue
        entry = {
            "id": next_id,
            "term": c["term"],
            "brief": c["brief"],
            "source": c["source"],
            "why_important": c["why_important"],
            "added_date": today,
            "status": "pending"
        }
        pending_data["pending"].append(entry)
        added.append(entry)
        next_id += 1

    pending_data["last_updated"] = today
    return pending_data, added


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


def send_review_notice(added, pending_total):
    """发送审核通知到钉钉"""
    if not added:
        print("本周无新词候选，跳过推送")
        return

    lines = [f"## 🔍 葡萄大模型词库 · 周五新词巡逻\n"]
    lines.append(f"本周发现 **{len(added)}** 个候选新词，请审核后决定是否加入词库：\n")

    for i, c in enumerate(added, 1):
        lines.append(f"**{i}. {c['term']}**")
        lines.append(f"> {c['brief']}")
        lines.append(f"> 来源：{c['source']} · {c['why_important']}\n")

    lines.append(f"---")
    lines.append(f"📋 当前待审核池共 **{pending_total}** 个词")
    lines.append(f"✅ 确认加入：修改 `pending_topics.json` 中对应词条的 `status` 为 `active`，并移入 `topics.json`")

    content = "\n".join(lines)

    timestamp, sign = get_dingtalk_sign()
    url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": "🔍 新词审核通知", "text": content}
    }
    resp = httpx.post(url, json=payload, timeout=30)
    result = resp.json()
    if result.get("errcode") == 0:
        print(f"✅ 审核通知已发送，{len(added)} 个候选词")
    else:
        print(f"❌ 钉钉推送失败：{result}")


if __name__ == "__main__":
    topics_data  = load_json(TOPICS_FILE)
    pending_data = load_json(PENDING_FILE)

    existing_terms = get_existing_terms(topics_data)
    print(f"当前词库共 {len(existing_terms)} 个词，开始巡逻...")

    result = scan_new_terms(existing_terms)
    candidates = result.get("candidates", [])
    print(f"发现候选新词：{len(candidates)} 个")

    pending_data, added = update_pending(pending_data, candidates, topics_data)
    save_json(PENDING_FILE, pending_data)

    send_review_notice(added, len(pending_data["pending"]))
    print("巡逻完成")
