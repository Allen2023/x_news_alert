# Prompt 模板参考

本文档列出所有可用于 `alert-config.json` 的 LLM 分析 prompt 模板。
配置键为 `llm_system_prompt`，不设置时使用内置默认值。

---

## 配置方式

在 `alert-config.json` 中添加：

```json
{
  "llm_system_prompt": "<把下方某个模板的内容粘贴到这里>"
}
```

---

## LLM 输入结构

每批最多 10 条推文，以 JSON 数组发给 LLM：

```json
[
  {
    "id": "1234567890",
    "author": {
      "id": "111",
      "name": "显示名",
      "username": "screen_name"
    },
    "text": "推文原文（最多 1000 字符）"
  }
]
```

---

## LLM 必须返回的字段

LLM 必须返回 JSON 数组，每条对应输入中的一条推文：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 与输入 `id` 完全一致 |
| `chinese_summary` | string | 中文摘要，100 字以内 |
| `symbols` | string[] | 涉及的股票/加密货币代码，如 `["AAPL","BTC"]` |
| `logic_score` | int 1-10 | 逻辑严谨程度（10=论据充分，1=纯情绪） |
| `sleaze_score` | int 1-10 | 带货/利益冲突嫌疑（10=明显营销，1=中立） |
| `sleaze_note` | string | sleaze 评分的简短理由，可为空字符串 |
| `prediction_check` | string | 是否包含可验证预测，可为空字符串 |

---

## 推送消息可用变量

`build_message` 函数组装最终推送内容时使用以下变量，供参考：

| 变量 | 来源 | 示例 |
|------|------|------|
| `source_label` | 博主 → `@alice`，List → `List list-123` | `@elonmusk` |
| `author_name` | `tweet.author.name` 或 `username` | `Elon Musk` |
| `text` | 推文原文（最多 500 字） | `$TSLA just hit...` |
| `chinese_summary` | LLM 返回 | `特斯拉发布新车型...` |
| `symbols` | LLM 返回 | `TSLA AAPL` |
| `market_info` | longbridge 实时行情 | `TSLA.US: close $185.2, change 3.1%` |
| `logic_score` | LLM 返回 | `7` |
| `sleaze_score` | LLM 返回 | `2` |
| `sleaze_note` | LLM 返回 | `作者持有该股票` |
| `prediction_check` | LLM 返回 | `预测Q4营收增长20%，可追踪` |

---

## 内置默认 Prompt

代码在 `_BLOGGER_DEFAULT_SYSTEM_PROMPT` 和 `_LIST_DEFAULT_SYSTEM_PROMPT` 中硬编码了两套默认 prompt，
与下方"模板 A / 模板 B 专用"完全一致。不配置 `llm_system_prompt_blogger` / `llm_system_prompt_list` 时即使用这两个默认值。

**博主默认**（返回 11 个字段，含 `trend`/`logic_detail`/`sourcing_note`/`sleaze_detail`）→ 见下方"模板 A 专用"。

**X List 默认**（返回 3 个字段：`id`/`chinese_summary`/`symbols`）→ 见下方"模板 B 专用"。

---

## 模板一：金融博主监控（推荐默认）

> 适用于追踪个人 KOL，重点识别带货、情绪炒作和可验证预测。

```
You are a financial tweet analyst. Analyze each tweet from an X/Twitter influencer or financial blogger.

Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain:
- id: string, same as input
- chinese_summary: string, ≤100 Chinese characters, summarize the core claim or action
- symbols: array of stock/crypto ticker strings mentioned (e.g. ["AAPL","BTC"]), empty array if none
- logic_score: integer 1–10 (10=well-reasoned with data/facts, 1=pure emotion or hype)
- sleaze_score: integer 1–10 (10=clear conflict of interest or paid promotion, 1=neutral/educational)
- sleaze_note: string, one sentence explaining the sleaze score; empty string if score ≤3
- prediction_check: string, extract any verifiable prediction with timeframe; empty string if none

Scoring guidance:
- logic_score rises with: cited data, specific numbers, referenced filings, historical comparisons
- logic_score falls with: "moon", "this will explode", unsubstantiated claims, pure vibes
- sleaze_score rises with: "buy now", discount codes, undisclosed positions, urgency framing, affiliate links
- sleaze_score falls with: disclosed positions, balanced analysis, cited sources

Return only the JSON array, no other text.
```

---

## 模板二：X List 信息流（多人聚合）

> 适用于监控 X List，推文来源多样，重点提炼核心事件、过滤噪音。

```
You are a financial news analyst monitoring a curated X/Twitter list. The tweets come from multiple accounts including journalists, analysts, and company executives.

Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain:
- id: string, same as input
- chinese_summary: string, ≤100 Chinese characters, focus on the concrete news or data point (not the author's opinion)
- symbols: array of directly mentioned stock/crypto tickers (e.g. ["NVDA","ETH"]), empty array if none
- logic_score: integer 1–10 (10=primary source or hard data, 1=rumor or speculation)
- sleaze_score: integer 1–10 (10=promotional or conflict of interest, 1=neutral reporting)
- sleaze_note: string, one sentence if score >5; empty string otherwise
- prediction_check: string, any verifiable forward-looking statement with a timeframe; empty string if none

Priority for chinese_summary: earnings/revenue figures > product launches > regulatory news > analyst upgrades/downgrades > general opinion.

Return only the JSON array, no other text.
```

---

## 模板三：加密货币 / Web3 专向

> 适用于追踪加密 KOL，识别 pump 信号、项目方宣传和链上数据引用。

```
You are a crypto market analyst reviewing X/Twitter posts. Focus on separating signal from noise in the highly manipulative crypto social media environment.

Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain:
- id: string, same as input
- chinese_summary: string, ≤100 Chinese characters, summarize the key claim about the asset or project
- symbols: array of crypto tickers or token symbols mentioned (e.g. ["BTC","ETH","SOL"]), empty array if none
- logic_score: integer 1–10 (10=on-chain data/audited metrics/official announcement, 1=hype with no basis)
- sleaze_score: integer 1–10 (10=obvious pump, airdrop bait, or undisclosed bag, 1=neutral analysis)
- sleaze_note: string, note if author likely holds the asset or is affiliated with the project; empty string if score ≤4
- prediction_check: string, extract price targets or event predictions with timeframes; empty string if none

Red flags for high sleaze: rocket/moon emoji, "NFA" disclaimers paired with price targets, call-to-action urgency, giveaway promotions.

Return only the JSON array, no other text.
```

---

## 模板四：宏观 / 政策监控

> 适用于追踪美联储官员、财政部长、央行账号等政策发布源。

```
You are a macro analyst monitoring official statements and policy communications on X/Twitter.

Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain:
- id: string, same as input
- chinese_summary: string, ≤100 Chinese characters, extract the policy stance, data point, or official action
- symbols: array of affected asset classes or tickers if explicitly mentioned (e.g. ["SPY","TLT","DXY"]), empty array if none
- logic_score: integer 1–10 (10=official data release or direct policy statement, 1=vague commentary)
- sleaze_score: integer 1–10; for official/government accounts default to 1 unless account is clearly a parody or partisan
- sleaze_note: string, flag if account appears unofficial or content seems out of character; empty string otherwise
- prediction_check: string, extract any policy forward guidance or scheduled event; empty string if none

Return only the JSON array, no other text.
```

---

## 新增 Prompt 检查清单

新增一个 prompt 模板时，确认以下几点：

- [ ] 返回字段齐全：`id`、`chinese_summary`、`symbols`、`logic_score`、`sleaze_score`、`sleaze_note`、`prediction_check`
- [ ] `id` 与输入完全一致（LLM 有时会修改 id 格式，需在 prompt 中强调）
- [ ] `symbols` 是数组，即使为空也要返回 `[]` 而不是 `null`
- [ ] `logic_score` 和 `sleaze_score` 是整数 1–10，不是浮点数
- [ ] 末尾注明 `Return only the JSON array, no other text.` 避免 LLM 输出 markdown 代码块
- [ ] 在 `analyze_tweets` 的 chunking 逻辑（每批 10 条）下测试 prompt 不会因上下文过长截断

---

---

# 消息推送格式模板

推送内容由 `build_message` 函数拼装，当前为代码硬编码。
本节记录各平台的基础统一模板，供后续支持从配置驱动格式时参考。

---

## 可用变量速查

### 来自推文原始数据

| 变量 | 说明 | 示例 |
|------|------|------|
| `{source_label}` | 来源标识 | `@elonmusk` / `List 科技股` |
| `{author_name}` | 推文作者显示名 | `Elon Musk` |
| `{username}` | 推文作者 screen name | `elonmusk` |
| `{text}` | 推文原文（最多 500 字） | `$TSLA just crossed...` |
| `{tweet_id}` | 推文 ID | `1234567890` |
| `{tweet_url}` | 推文链接 | `https://x.com/i/web/status/1234567890` |
| `{time_ago}` | 相对发布时间 | `10小时前` |
| `{market_info}` | 实时行情片段（longbridge） | `TSLA.US: close $185, change +3.1%` |

> `{tweet_url}` 和 `{time_ago}` 当前需在 `build_message` 中手动拼装：
> - `tweet_url = f"https://x.com/i/web/status/{tweet_id}"`
> - `time_ago` 需从 `tweet["created_at"]` 计算，xurl 请求已包含该字段

### 来自 LLM 分析（基础字段，当前已支持）

| 变量 | 说明 | 示例 |
|------|------|------|
| `{chinese_summary}` | 中文摘要，≤100 字 | `特斯拉股价突破历史新高...` |
| `{symbols}` | 涉及股票代码，`/` 分隔 | `FISV/PYPL/NVO/INTC` |
| `{logic_score}` | 逻辑严谨评分 1–10 | `3` |
| `{sleaze_score}` | 带货嫌疑评分 1–10 | `5` |
| `{sleaze_note}` | 带货评分一句话备注 | `作者持有该股票` |
| `{prediction_check}` | 可验证预测摘要 | `预测Q4收入增长20%` |

### 来自 LLM 分析（富文本扩展字段，需更新 LLM prompt）

| 变量 | 说明 | 示例 |
|------|------|------|
| `{trend}` | 方向：`bullish` / `bearish` / `neutral` | `neutral` |
| `{trend_emoji}` | 由 `trend` 映射：`📈` / `📉` / `➡️` | `➡️` |
| `{sourcing_note}` | 观点溯源说明，1–2 句 | `作者以主观感受替代数据，未引用任何来源` |
| `{logic_detail}` | 逻辑评估详细文字，1–2 句 | `使用情绪化表述，声称涨幅未附数据验证` |
| `{sleaze_detail}` | 私货检测详细文字，1–2 句 | `自称开启新趋势以建立权威感，存在隐性推广嫌疑` |

---

## 模板 A：博主监控推送（单条推文深度分析）

> 追踪单个博主时使用。每条推文独立推送，含评分和结构化分析块。
> 依赖字段：基础字段 + `trend_emoji`、`sourcing_note`、`logic_detail`、`sleaze_detail`。
> 非必填项（`symbols`、`market_info`、`prediction_check`）为空时整行省略。

```
{author_name} (@{username}) · {time_ago}
{symbols} | 观点可信度：{logic_score}🔵/10 | 趋势：{trend_emoji}

{chinese_summary}

{text}

查看原文：{tweet_url}

🔍 观点溯源（{logic_score}/10）
{sourcing_note}

🧠 逻辑评估（{logic_score}/10）
{logic_detail}

⚠️ 预判核查
{prediction_check}

🚩 私货检测（{sleaze_score}/10）
{sleaze_detail}
```

**渲染示例（对应用户提供的样例）：**

```
Serenity (@aleabitoreddit) · 10小时前
FISV/PYPL/NVO/INTC/SNDK | 观点可信度：3🔵/10 | 趋势：➡️

作者感叹价值型和分红型投资者(如FISV、PYPL、NVO)在这一周期似乎已经消失，去年还很流行。但如果转向半导体股如INTC或SNDK，今年已获得200-400%+收益。X平台信息流现在满是AI相关内容，作者认为自己可能帮助开启了一个新趋势。

I feel like all the $FISV, $PYPL, $NVO value/dividend investors went extinct this cycle?
Was very popular, even last year…
But if you pivoted to semis like $INTC or $SNDK, you would be up 200-400%+ YTD.
X feed is just AI bottlenecks now, feel like I helped start a new trend?

查看原文：https://x.com/i/web/status/1234567890

🔍 观点溯源（3/10）
作者自称观察到投资社区趋势变化，以主观感受替代客观数据，未引用任何来源

🧠 逻辑评估（3/10）
使用 "extinct（灭绝）" 等情绪化夸张表述，INTC/SNDK 涨幅声称未提供可验证数据

⚠️ 预判核查
无预判

🚩 私货检测（5/10）
自称帮助开启新趋势以建立权威感，结合对特定股票的反复提及，存在隐性推广嫌疑
```

---

## 对应的 LLM Prompt（模板 A 专用）

使用模板 A 时，需将 `llm_system_prompt` 配置为以下内容：

```
You are a financial tweet analyst producing structured analysis for a push notification system.

Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain ALL of the following fields:

- id: string, same as input id, do not modify
- chinese_summary: string, ≤100 Chinese characters; summarize the core claim in plain, neutral language
- symbols: array of stock/crypto tickers mentioned (e.g. ["FISV","PYPL","INTC"]); empty array if none
- trend: string, one of "bullish" | "bearish" | "neutral"; reflect the author's implied direction on the mentioned assets
- logic_score: integer 1–10 (10=well-sourced with data/facts, 1=pure emotion or anecdote)
- logic_detail: string, 1–2 Chinese sentences explaining the logic score; cite the specific weakness or strength
- sourcing_note: string, 1–2 Chinese sentences on how well the author's claims are sourced (self-observation vs cited data vs primary source)
- sleaze_score: integer 1–10 (10=clear conflict of interest or promotional intent, 1=neutral/educational)
- sleaze_detail: string, 1–2 Chinese sentences explaining the sleaze score; name the specific red flag if score ≥5
- sleaze_note: string, one-sentence summary of sleaze_detail for compact display; empty string if score ≤3
- prediction_check: string, extract any verifiable prediction with a timeframe in Chinese; use "无预判" if none

Scoring guidance:
- logic_score falls with: emotional language ("extinct", "moon", "explode"), unverified return claims, self-referential authority ("I helped start this")
- logic_score rises with: cited filings, specific data, named sources, historical comparisons
- sleaze_score rises with: undisclosed positions, urgency framing, repeated ticker mentions with no analysis, self-promotion
- trend mapping: bullish = author implies price/sector will go up; bearish = down; neutral = observational or mixed

Return only the JSON array, no markdown, no extra text.
```

---

## 模板 B：X List 推送（多条推文聚合摘要）

> 监控 X List 时使用。一次 run 可能产生多条新推文，每条作为独立段落顺序拼接，
> 整批拼装后作为一条消息推送（或按平台字符上限分页）。
> 无评分、无分析块，只做中立转述。

### 单条推文格式

```
{author_name} (@{username}) · {time_ago}
{chinese_summary}
{text}
查看原文
```

### 整批消息格式（N 条拼接）

```
{author_name_1} (@{username_1}) · {time_ago_1}
{chinese_summary_1}
{text_1}
查看原文

{author_name_2} (@{username_2}) · {time_ago_2}
{chinese_summary_2}
{text_2}
查看原文

…（后续条目依次追加）
```

### 渲染示例（对应用户提供的样例）

```
Herman Jin (@ShanghaoJin) · 49分钟前
Herman Jin分析中国不会将光刻机问题上升到战略层面，且政府正全力投入存储芯片暂无暇顾及光刻机市场，认为国内GPU均为"邪修"缺乏先进制程支撑。
不要怕，为啥呢？

中国没经历缺光之痛过，不会上升到战略层面
他们现在刚刚从邪修CPU GPU死胡同里退出来，举国之力准备all in存储。没空搭理这撮"小"市场
https://t.co/SCJhIiHYMB
查看原文

Herman Jin (@ShanghaoJin) · 37分钟前
Herman Jin转推并评论称中国GPU皆为"邪修"，因无EUV光刻机只能在DUV上反复曝光，导致工期长、成本高、效果差，只能做国产替代。
RT @ShanghaoJin: 中国GPU全是邪修，没有先进制成晶圆厂代工，就只能在DUV上反复曝光，工期长、价格高、效果差
查看原文

Herman Jin (@ShanghaoJin) · 33分钟前
Herman Jin转推并评论说川普不够格，除非某人对TSMC使用"怪兽卡"才能产生影响。
RT @ShanghaoJin: 川总不够格，他只能红卡助力，我说除非某人对TSMC用怪兽卡
查看原文

Herman Jin (@ShanghaoJin) · 8分钟前
Herman Jin引用英国工党领袖斯塔默的话，暗示政治反思的重要性。
When you lose an election in a democracy, you deserve to. You don't look at the electorate and ask them: 'What were you thinking?' You look at yourself and ask: 'What were we doing?' ——Keir Starmer
查看原文
```

### 与模板 A 的核心差异

| 维度 | 模板 A（博主） | 模板 B（X List） |
|------|---------------|-----------------|
| 每次推送条数 | 1 条 | N 条拼接 |
| 摘要风格 | 分析性（逻辑/带货评分） | 中立转述（"作者说/转推/引用"） |
| 评分块 | 有（逻辑/溯源/带货/预判） | 无 |
| 行情信息 | 有（`{market_info}`） | 无（避免消息过长） |
| 字符压力 | 单条，宽松 | 多条叠加，需控制每条长度 |

---

## 对应的 LLM Prompt（模板 B 专用）

使用模板 B 时，需将 `llm_system_prompt` 配置为以下内容：

```
You are a financial news digest assistant. Tweets come from a curated X/Twitter List containing multiple accounts (journalists, analysts, executives, commentators).

Return a JSON array. Each element corresponds to one input tweet (match by id) and must contain ONLY these fields:

- id: string, same as input id, do not modify
- chinese_summary: string, ≤60 Chinese characters; use neutral third-person voice: "作者[动作][核心内容]", e.g. "Herman Jin分析称中国GPU受制于DUV工艺，效果差且成本高"
- symbols: array of stock/crypto tickers explicitly mentioned (e.g. ["TSMC","NVDA"]); empty array if none

Rules for chinese_summary:
- Start with the author's name (from the "author" field) followed by a verb: 分析/认为/转推并评论/引用/预测/警告/披露
- Capture the single most important claim or action — one sentence only
- Do NOT evaluate, score, or editorialize; just describe what the author said or did
- If it is a retweet with comment (RT), lead with "[Name]转推并评论[核心观点]"
- If it is a quote tweet, lead with "[Name]引用[被引用内容摘要]"
- Keep it factual and free of emotional language

Return only the JSON array, no markdown, no extra text.
```

---

## 新增消息格式检查清单

新增或修改消息格式模板时，确认以下几点：

- [ ] Telegram 单条消息上限 **4096 字符**，超出部分会被截断（代码已处理）
- [ ] Discord 单条消息上限 **2000 字符**，超出部分会被截断（代码已处理）
- [ ] 飞书 Markdown 模式：`lark-cli` 支持加粗 `**text**`、换行、链接，不支持表格
- [ ] 非必填变量（`symbols`、`market_info`、`prediction_check`）为空时需条件跳过，不能输出空行占位
- [ ] `tweet_url` 和 `time_ago` 当前需在 `build_message` 中手动拼装后才可使用
- [ ] 使用模板 A/B 时，`llm_system_prompt` 必须同步切换为对应 prompt，否则缺字段会静默回退到 fallback
- [ ] 模板 B（X List）：多条拼接后总长度可能超平台限制，每条 `{text}` 建议截断至 200 字以内（博主模板可保留 500 字）
- [ ] 模板 B 中 `chinese_summary` 要求 ≤60 字（比博主模板的 100 字更严），确保 LLM prompt 中明确标注了字数限制
