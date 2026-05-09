# x-news-alert

追踪 X/Twitter 博主和 X List，分析后推送到 Telegram、Discord 或飞书。

## 远程安装

推荐用 `pipx`，安装后会得到全局命令 `x-news-alert`、`xna` 和 `x`：

```powershell
pipx install git+https://github.com/Allen2023/x_news_alert.git
```

如果用户没有 `pipx`，也可以直接用 `pip`：

```powershell
python -m pip install git+https://github.com/Allen2023/x_news_alert.git
```

发布到 PyPI 后，安装命令可以简化为：

```powershell
pipx install x-news-alert
```

## 初始化

安装后不需要指定项目目录，配置默认写入用户配置目录。也可以用 `X_NEWS_ALERT_HOME` 指定自定义目录。

```powershell
x-news-alert init --interactive
```

或者用命令参数初始化：

```powershell
x-news-alert init `
  --platform telegram `
  --chat-id=-100123456789 `
  --blogger alice
```

常用环境变量：

```powershell
$env:TWITTER_COOKIE="auth_token=xxx; ct0=xxx"
$env:OPENAI_API_KEY="sk-xxx"
$env:TELEGRAM_BOT_TOKEN="xxx"
```

运行：

```powershell
x-news-alert run all
```

需要机器可读输出时，加 `--structured`，输出会包成 `ok/schema_version/data/error` 结构：

```powershell
x-news-alert --structured config
```

结构化输出契约见 [SCHEMA.md](./SCHEMA.md)。

## X List 筛选

列表模式默认保留原行为。如果想减少低价值列表消息，可以在 `alert-config.json` 里开启：

```json
{
  "list_filter": {
    "enabled": true,
    "mode": "topN",
    "topN": 20,
    "minScore": 0,
    "lang": [],
    "excludeRetweets": false,
    "weights": {
      "likes": 1.0,
      "retweets": 3.0,
      "replies": 2.0,
      "bookmarks": 5.0,
      "views_log": 0.5
    }
  }
}
```

当前脚本也会归一化 `twitter-cli` 风格输出，例如 `author.screenName`、`createdAt`、`metrics.likes` 等字段。
