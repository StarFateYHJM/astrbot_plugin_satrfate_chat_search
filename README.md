# AstrBot 长期记忆插件 (Chat Search)

一个极简、稳定、零额外成本的 AstrBot 长期记忆插件。
关闭流式输出后，通过 after_message_sent 钩子获取完整的 AI 回复，
并与用户消息合并为一行存入 SQLite 数据库，按会话物理隔离。
每次对话时，自动用关键词检索历史记录并注入到 LLM 上下文，
实现真正的“永久记忆”。

## 核心特性

- 一问一答，合并为一行，永不错位
- 按会话隔离的 SQLite 数据库（私聊/群聊独立）
- 关键词 LIKE 检索，全局搜索历史记录
- 自动注入匹配的历史到 LLM 上下文
- 零额外 Token 消耗
- 代码极简，不到 100 行，易于维护

## 为什么关闭流式输出？

在 AstrBot 框架下，流式输出（打字机效果）与完整的 AI 回复记录是互斥的。
框架在流式模式下会跳过 after_message_sent 钩子，导致无法获取完整回复。
经过长时间的技术验证，我们选择关闭流式输出，以确保记忆的完整性和稳定性。

## 安装与配置

1. 将本插件文件夹放入 AstrBot 的插件目录：
   astrbot_plugin_satrfate_chat_search/

2. 修改 AstrBot 主配置文件 data/cmd_config.json：
   - 将 streaming_response 设为 false
   - 将 segmented_reply 内部的 enable 设为 false

3. 在 AstrBot 面板的插件管理中启用本插件，并填写：
   - bot_self_id: 机器人的 QQ 号

4. 重启 AstrBot

## 数据库说明

数据库文件存储在：
  data/plugin_data/astrbot_plugin_satrfate_chat_search/

命名规则：
  私聊: FriendMessage_{QQ号}.db
  群聊: GroupMessage_{群号}.db

每条记录包含字段：
  id, sender_id, sender_name, message_text, timestamp

message_text 格式示例：
  [用户名]：用户提问
  [assistant]：AI完整回复

## 检索注入机制

每次用户提问时，插件会：
1. 提取用户消息中的关键词（按空格分词，长度>=2）
2. 在过去所有历史记录中进行 LIKE 全文搜索
3. 返回所有匹配结果（最多100条）
4. 注入到 LLM 的 system_prompt 中，供 AI 参考回答

## 常用数据库查询命令（Windows）

查看全部数据库文件：
python -c "import os; files=[f for f in os.listdir(r'C:\Users\Administrator\.astrbot\data\plugin_data\astrbot_plugin_satrfate_chat_search') if f.endswith('.db')]; print(f'数据库文件数: {len(files)}'); [print(f'  {f}') for f in files]"

查看某数据库全部消息（按时间顺序）：
python -c "import sqlite3; conn=sqlite3.connect(r'C:\Users\Administrator\.astrbot\data\plugin_data\astrbot_plugin_satrfate_chat_search\FriendMessage_3384188179.db'); c=conn.cursor(); c.execute('SELECT sender_name, message_text, datetime(timestamp, \"unixepoch\", \"localtime\") as ts FROM messages ORDER BY timestamp ASC'); [print(f'[{r[2]}] [{r[0]}] {r[1][:150]}') for r in c.fetchall()]; conn.close()"

关键词搜索（例如搜索“索拉图”）：
python -c "import sqlite3; conn=sqlite3.connect(r'C:\Users\Administrator\.astrbot\data\plugin_data\astrbot_plugin_satrfate_chat_search\FriendMessage_3384188179.db'); c=conn.cursor(); c.execute(\"SELECT sender_name, message_text, datetime(timestamp, 'unixepoch', 'localtime') as ts FROM messages WHERE message_text LIKE '%索拉图%' ORDER BY timestamp ASC\"); [print(f'[{r[2]}] [{r[0]}] {r[1][:150]}') for r in c.fetchall()]; conn.close()"

## 版本历史

v9.1.0 (2026-05-03)
- 全局检索注入，最多返回100条匹配记录
- 一问一答合并为一行
- 移除所有冗余代码

v9.0.0 (2026-05-03)
- 首次稳定版，关闭流式，使用 after_message_sent 钩子

## 许可证

MIT
