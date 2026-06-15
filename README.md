================================================================================
Satrfate Chat Search — 极简长期记忆插件
================================================================================

为 AstrBot 提供一问一答式的永久记忆。通过 after_message_sent 钩子获取完整 AI 回复，
合并存入 SQLite 数据库。支持关键词检索、上下文注入、固定记忆注入和 jieba 智能分词。

核心特性
--------------------------------------------------------------------------------
- 一问一答合并存储，永久保存
- 私聊会话独立数据库（可自行开启群聊）
- jieba 分词 + 停用词过滤，自动降级
- 模糊检索，按时间排序
- 固定记忆注入（不计入检索上限）
- 叙事性注入（你说：... 我回应：...）
- 低 Token 消耗，仅注入最近 N 条
- 配置灵活，代码极简

注意事项
--------------------------------------------------------------------------------
- 必须关闭流式输出：修改 data/cmd_config.json 中 "streaming_response": false
- 分段回复 (segmented_reply.enable) 可保持 true
- 当前版本默认仅私聊，若要启用群聊请移除代码中的 if not event.is_private_chat(): return

快速配置
--------------------------------------------------------------------------------
1. 修改 AstrBot 主配置 (data/cmd_config.json)
   {
     "streaming_response": false,
     "segmented_reply": { "enable": true }
   }

2. 插件配置文件 config.json（通过 AstrBot 面板或手动创建）
   {
     "bot_self_id": { "type": "string", "default": "", "description": "机器人QQ号（必填）" },
     "debug": { "type": "bool", "default": false, "description": "调试日志" },
     "max_inject": { "type": "int", "default": 50, "description": "每次注入最大条数" },
     "max_search_limit": { "type": "int", "default": 500, "description": "单次检索 LIMIT" },
     "custom_stopwords": { "type": "text", "default": "", "description": "自定义停用词，每行一个" },
     "fixed_memories": { "type": "text", "default": "", "description": "固定记忆，每行一条" },
     "use_jieba": { "type": "bool", "default": true, "description": "使用 jieba 分词（自动安装）" }
   }

3. 重启 AstrBot

数据库说明
--------------------------------------------------------------------------------
存储路径：
  data/plugin_data/astrbot_plugin_satrfate_chat_search/

命名规则：
  私聊：FriendMessage_{QQ号}.db
  群聊（若开启）：GroupMessage_{群号}.db

表结构：
  CREATE TABLE messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sender_id TEXT,
      sender_name TEXT,
      message_text TEXT,
      timestamp REAL
  );
  CREATE INDEX idx_timestamp ON messages(timestamp);

记录示例：
  用户：慢点啦~
  AI回复：无奈地笑了笑...

检索与注入机制
--------------------------------------------------------------------------------
1. 停用词过滤：预编译正则删除 1800+ 常见无意义词（长词优先）
2. 智能分词：优先使用 jieba 分词（自动安装），降级为连续汉字双字组（bigram）
3. 关键词筛选：保留长度 >=2 且不在停用词表中的词
4. 数据库检索：LIKE '%keyword%'（OR 组合），按时间倒序，限制 max_search_limit 条
5. 结果截取：取最新的 max_inject 条
6. 固定记忆注入：始终添加 ## 【固定记忆】 部分（若配置）
7. 注入到 system_prompt 的格式：

   ## 【固定记忆】
   你说：你的名字是樱恒佳梦
   我回应：记住了，我叫樱恒佳梦～

   ## 【记忆回溯 - 共 5 条往事】
   你说：索拉图是服装店老板~
   我回应：是的，索拉图在费尔斯小镇开店...
   ---
   上面是你脑海中浮现的往事。请继续用你的口气陪用户说话。

常用命令（用于调试）
--------------------------------------------------------------------------------
# 查看所有数据库文件
python -c "import os; d='data/plugin_data/astrbot_plugin_satrfate_chat_search'; [print(f) for f in os.listdir(d) if f.endswith('.db')]"

# 查看某数据库全部消息（按时间顺序）
python -c "import sqlite3; c=sqlite3.connect('data/plugin_data/astrbot_plugin_satrfate_chat_search/FriendMessage_114514.db').cursor(); c.execute('SELECT message_text FROM messages ORDER BY timestamp ASC'); [print(r[0]) for r in c.fetchall()]"

# 关键词搜索（例如“索拉图”）
python -c "import sqlite3; c=sqlite3.connect('data/plugin_data/astrbot_plugin_satrfate_chat_search/FriendMessage_114514.db').cursor(); c.execute(\"SELECT message_text FROM messages WHERE message_text LIKE '%索拉图%' ORDER BY timestamp ASC\"); [print(r[0]) for r in c.fetchall()]"

插件内置命令（私聊发送）
--------------------------------------------------------------------------------
searchtest 关键词1 关键词2      - 在当前私聊会话中检索历史记录
searchtest --all 关键词         - 全局检索所有数据库

版本历史
--------------------------------------------------------------------------------
v9.2.9  集成 jieba 分词、固定记忆注入、自动安装依赖、性能优化
v9.2.6  双检测 + 停用词过滤 + 中文双字拆分优化
v9.1.7  数据库中使用 "AI回复" 作为角色标签，注入使用 "你/我"
v9.1.0  全局检索注入，最多 100 条
v9.0.0  首次稳定版

许可证
--------------------------------------------------------------------------------
MIT License
Copyright (c) 2026 YHJM
