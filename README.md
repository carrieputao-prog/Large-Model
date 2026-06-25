# 🍇 Large Model — 葡萄大模型日课

每天3次自动推送大模型知识点到钉钉，每天覆盖三个分类各1个词，每周五自动巡逻发现新词。

## 仓库结构

```
Large Model/
├── knowledge_push.py        # 主推送脚本（每天3次）
├── weekly_scan.py           # 周五新词巡逻脚本
├── topics.json              # 主词库（74个词条，只增不删）
├── progress.json            # 学习进度追踪（自动更新）
├── pending_topics.json      # 待审核新词池
└── .github/workflows/
    ├── knowledge_push.yml   # 每天3次定时推送
    └── weekly_scan.yml      # 每周五巡逻
```

## 推送时间（北京时间）

| 时段 | 北京时间 | UTC cron | 风格 |
|------|---------|---------|------|
| 晨读 | 03:00 | `0 19 * * *`（前一天） | 基础认知层 |
| 午间 | 12:00 | `0 4 * * *` | 工程与应用层 |
| 晚间 | 18:00 | `0 10 * * *` | 前沿与趋势层 |

## 取词规则

三个分类分别维护自己的取词位置：

- 晨读固定推 `基础认知层`
- 午间固定推 `工程与应用层`
- 晚间固定推 `前沿与趋势层`

晚间推送成功后，三个分类的取词位置一起前进一天。因为三个分类词条数不同，某个分类学完后会在本分类内循环。

## GitHub Secrets 配置

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret名称 | 说明 |
|-----------|------|
| `GEMINI_API_KEY` | Google Gemini API Key |
| `DINGTALK_WEBHOOK` | 钉钉机器人 Webhook URL |
| `DINGTALK_SECRET` | 钉钉机器人加签密钥 |

## 添加新词

**方式一：你手动发现**
直接在 `topics.json` 中添加词条，格式参考现有词条，`source` 填 `"user_manual"`。
词条分类只保留：`基础认知层`、`工程与应用层`、`前沿与趋势层`。

**方式二：周五巡逻自动发现**
每周五钉钉会收到候选新词通知，审核后将词条从 `pending_topics.json` 移入 `topics.json` 并将 `status` 改为 `"active"` 即可。

**方式三：Agent1 联动（进阶）**
在 AI 热点早报脚本中增加副任务，发现新词自动写入 `pending_topics.json`。

## 学习进度查询

把 `progress.json` 的内容发给 Claude，即可查询当前学习进展和历史记录。

## 注意事项

- `progress.json` 由 GitHub Actions 自动提交更新，无需手动维护
- 词库跑完一轮后自动进入第二轮，同词条换角度重新讲解
- 手动触发测试：Actions → 葡萄大模型日课 → Run workflow → 选择时段（3/12/18）
