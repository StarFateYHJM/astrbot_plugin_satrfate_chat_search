# Satrfate Chat Search — 极简长期记忆插件
为 AstrBot 提供一问一答式的永久记忆。关闭流式输出，通过 after_message_sent 钩子获取完整 AI 回复，合并存入 SQLite 数据库。支持全局关键词检索和上下文注入，关键词提取采用停用词过滤 + 中文双字拆分 + 双检测机制，精准匹配历史记录。

## 核心特性
• 一问一答，合并一行，永久保存
• 按会话隔离的 SQLite 数据库（私聊 / 群聊独立）
• 关键词 LIKE 检索，全局搜索历史对话
• 叙事性注入，将历史记录转译为自然语境，保护人格表现
• 双格式分离：数据库中 “用户 / AI回复”，注入时 “你 / 我”
• 极低 Token 消耗（每次仅注入精准匹配的关键词记录）
• 可配置 debug 开关、注入上限、自定义停用词
• 代码极简，不到 200 行

## 注意事项
• 必须关闭流式输出（streaming_response: false），否则 after_message_sent 钩子不触发。
• 分段回复（segmented_reply.enable）可安全开启，不影响钩子获取完整内容。

## 快速配置
1. 修改 AstrBot 主配置 data/cmd_config.json：
   "streaming_response": false
   （segmented_reply.enable 可保持 true）

2. 插件配置（AstrBot 面板）：
   配置项           说明              示例
   ─────────────────────────────────────
   bot_self_id      机器人 QQ 号       114515
   debug            调试日志开关        false
   max_inject       注入上限（条）      50
   custom_stopwords 自定义停用词（每行一个） 空

3. 重启 AstrBot。

## 数据库说明
存储路径：
/plugin_data/astrbot_plugin_satrfate_chat_search/

命名规则：
  私聊: FriendMessage_{QQ号}.db
  群聊: GroupMessage_{群号}.db

表结构：
  messages(id, sender_id, sender_name, message_text, timestamp)

记录示例：
  用户：慢点啦~
  AI回复：无奈地笑了笑...

## 检索注入机制
1. 停用词过滤：先移除整句中的无意义词（1800+ 词）
2. 中文双字拆分：对剩余文本做 bigram 提取
3. 双检测：要求每条记录至少命中 2 个关键词
4. 按时间排序，取最近 N 条（可配置，默认 50）
5. 叙事体注入 system_prompt，格式如下：

  ## 【记忆回溯 - 共 5 条往事】
  你说：索拉图是服装店老板~
  我回应：是的，索拉图在费尔斯小镇开店...
  ---
  上面是你脑海中浮现的往事。请继续用你的口气陪用户说话。

## 常用命令
查看所有数据库文件：
python -c "import os; d='data/plugin_data/astrbot_plugin_satrfate_chat_search'; [print(f) for f in os.listdir(d) if f.endswith('.db')]"

查看某数据库全部消息（按时间顺序）：
python -c "import sqlite3; c=sqlite3.connect('data/plugin_data/astrbot_plugin_satrfate_chat_search/FriendMessage_114514.db').cursor(); c.execute('SELECT message_text FROM messages ORDER BY timestamp ASC'); [print(r[0]) for r in c.fetchall()]"

关键词搜索（例如“索拉图”）：
python -c "import sqlite3; c=sqlite3.connect('data/plugin_data/astrbot_plugin_satrfate_chat_search/FriendMessage_114514.db').cursor(); c.execute(\"SELECT message_text FROM messages WHERE message_text LIKE '%索拉图%' ORDER BY timestamp ASC\"); [print(r[0]) for r in c.fetchall()]"

## 版本历史
v9.2.6  双检测 + 停用词过滤 + 中文双字拆分优化
v9.1.7  数据库中使用 "AI回复" 作为角色标签，注入使用 "你/我"
v9.1.0  全局检索注入，最多 100 条
v9.0.0  首次稳定版，关闭流式，after_message_sent 钩子

## 许可证
MIT License
Copyright (c) 2026 YHJM
