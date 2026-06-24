"""
每周五自动巡逻：从多个渠道发现新 AI 术语，写入 grape-data/pending_topics.json，钉钉通知审核
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
DINGTALK_WEBHOOK = os.environ["DINGTALK_WEBHOOK"]
DINGTALK_SECRET  = os.environ["DINGTALK_SECRET"]
GITHUB_TOKEN     = os.environ["GITHUB_TOKEN"]
GITHUB_REPO      = "carrieputao-prog/grape-data"

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
TOPICS_FILE  = os.path.join(BASE_DIR, "topics.json")
ALLOWED_MODULES = {"基础认知层", "工程与应用层", "前沿与趋势层"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_existing_terms(topics_data):
    return {
        t["term"] for t in topics_data["topics"]
        if t.get("module") in ALLOWED_MODULES
    }


def read_pending_from_grape_data():
    """从 grape-data 仓库读取 pending_topics.json"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/pending_topics.json"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = httpx.get(url, headers=headers, timeout=30)
    if resp.status_code == 200:
        content = base64.b64decode(resp.json()["content"]).decode("utf-8")
        sha = resp.json()["sha"]
        return json.loads(content), sha
    else:
        return {"last_updated": date.today().isoformat(), "pending": []}, None


def write_pending_to_grape_data(pending_data, sha=None):
    """将 pending_topics.json 写入 grape-data 仓库"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/pending_topics.json"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {
        "message": f"🔍 weekly_scan 新词巡逻 {date.today().isoformat()}",
        "content": base64.b64encode(
            json.dumps(pending_data, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha

    resp = httpx.put(url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        print("✅ pending_topics.json 已写入 grape-data")
    else:
        print(f"⚠️ 写入 grape-data 失败：{resp.status_code} {resp.text}")


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

只允许推荐以下三个分类的术语：
- 基础认知层：大模型入门、核心原理、基础概念
- 工程与应用层：Prompt、RAG、Agent、部署、成本、企业应用
- 前沿与趋势层：多模态、推理模型、Agent趋势、AI Native、前沿研究

不要推荐“对齐与训练层”相关术语，例如 RLHF、奖励模型、PPO、DPO、对齐、安全训练、模型微调、量化、剪枝、评估等。

请输出3-5个候选新词，严格按JSON格式返回，不要任何其他内容：
{{
  "candidates": [
    {{
      "term": "术语名称（中英文）",
      "module": "基础认知层 / 工程与应用层 / 前沿与趋势层 三选一",
      "brief": "一句话说明这是什么",
      "source": "发现来源（账号/媒体名/博主名）",
      "why_important": "为什么值得加入词库（10字以内）"
    }}
  ]
}}"""

    response = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}]
        },
        timeout=120,
    )
    response.raise_for_status()
    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    raw = raw.strip().removeprefix("```json").removeprefix("```").strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    return json.loads(raw)


def update_pending(pending_data, candidates, topics_data):
    """将候选词追加到 pending，去重"""
    all_topics = topics_data["topics"]
    next_id = max(t["id"] for t in all_topics) + 100
    today = date.today().isoformat()
    existing_pending_terms = {p["term"] for p in pending_data["pending"]}
    existing_topic_terms = {t["term"] for t in all_topics}
    added = []

    for c in candidates:
        if c.get("module") not in ALLOWED_MODULES:
            continue
        if c["term"] in existing_topic_terms:
            continue
        if c["term"] in existing_pending_terms:
            continue
        entry = {
            "id": next_id,
            "term": c["term"],
            "module": c["module"],
            "brief": c["brief"],
            "source": c["source"],
            "why_important": c.get("why_important", ""),
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

    lines = ["## 🔍 葡萄大模型词库 · 周五新词巡逻\n"]
    lines.append(f"本周发现 **{len(added)}** 个候选新词，请审核后决定是否加入词库：\n")

    for i, c in enumerate(added, 1):
        lines.append(f"**{i}. {c['term']}**")
        lines.append(f"> 分类：{c['module']}")
        lines.append(f"> {c['brief']}")
        lines.append(f"> 来源：{c['source']} · {c.get('why_important', '')}\n")

    lines.append("---")
    lines.append(f"📋 当前待审核池共 **{pending_total}** 个词")
    lines.append("✅ 确认加入：在 grape-data 仓库的 `pending_topics.json` 中将对应词条 `status` 改为 `active`，并移入 `Large-Model` 仓库的 `topics.json`")

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
    topics_data = load_json(TOPICS_FILE)
    existing_terms = get_existing_terms(topics_data)
    print(f"当前词库共 {len(existing_terms)} 个词，开始巡逻...")

    result = scan_new_terms(existing_terms)
    candidates = result.get("candidates", [])
    print(f"发现候选新词：{len(candidates)} 个")

    print("读取 grape-data 中的待审核词池...")
    pending_data, sha = read_pending_from_grape_data()

    pending_data, added = update_pending(pending_data, candidates, topics_data)

    print("写入 grape-data...")
    write_pending_to_grape_data(pending_data, sha)

    send_review_notice(added, len(pending_data["pending"]))
    print("巡逻完成")
