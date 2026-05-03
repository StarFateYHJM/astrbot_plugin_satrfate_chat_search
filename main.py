import sqlite3
import os
import time
import asyncio
import json
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

STOP_WORDS = {
    '你', '我', '他', '她', '它', '们', '的', '了', '是', '在', '有', '和', '不', '这', '那',
    '吗', '呢', '吧', '啊', '哦', '嗯', '还', '就', '都', '也', '要', '会', '能', '去', '来',
    '说', '看', '想', '知道', '记得', '告诉', '觉得', '可以', '应该', '怎么', '什么', '哪',
    '一个', '这个', '那个', '这样', '那样', '真的', '好', '很', '有点', '没有'
}

@register("satrfate_chat_search", "you", "极简聊天记录检索注入插件，NapCat WebSocket 监控，按会话物理隔离", "4.0.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        # 数据库目录 = 插件ID
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)

        self.config = config or {}
        self.debug = self.config.get("debug", False)
        self.ws_url = self.config.get("napcat_ws_url", "ws://napcat:3002")
        self.bot_self_id = self.config.get("bot_self_id", "")

        if self.debug:
            logger.info("[ChatSearch] 调试模式已开启")
            logger.info(f"[ChatSearch] WS地址: {self.ws_url}, QQ: {self.bot_self_id}")

        # 启动 NapCat WebSocket 监控
        if self.bot_self_id:
            asyncio.create_task(self._napcat_monitor())
        else:
            logger.warning("[ChatSearch] 未配置 bot_self_id，消息监控不会启动")

    # ========== NapCat WebSocket 监控 ==========
    async def _napcat_monitor(self):
        """
        连接 NapCat WebSocket 服务器（正向 WS），获取完整的 OneBot v11 事件。
        
        NapCat 侧的配置要求：
        - NapCat WebUI → 网络配置 → 新建 → WebSocket 服务器
        - 主机：0.0.0.0，端口：自行设定（如 3002），消息格式：Array
        - 连接建立后，NapCat 会通过该连接推送所有 OneBot v11 事件

        事件过滤逻辑（依据 OneBot v11 标准）：
        - post_type == "message"：消息事件
        - message_type == "private"：私聊消息
        - 通过 user_id 区分用户消息与机器人自己的回复
        """
        from websockets import connect

        logger.info(f"[ChatSearch] NapCat 监控已启动，连接 {self.ws_url}")
        while True:
            try:
                async with connect(self.ws_url) as ws:
                    logger.info("[ChatSearch] ✅ 已连接到 NapCat WebSocket 服务器")
                    async for data in ws:
                        try:
                            msg = json.loads(data)

                            # 只处理私聊消息事件
                            if msg.get("post_type") != "message":
                                continue
                            if msg.get("message_type") != "private":
                                continue

                            sender_id = str(msg.get("user_id"))
                            # raw_message 是未经 CQ 码处理的原始文本[reference:0]
                            raw_text = msg.get("raw_message", "")
                            if not raw_text or not raw_text.strip():
                                continue

                            # 区分用户和 AI：如果 sender_id 等于机器人 QQ 号，则为 AI 回复
                            if sender_id == self.bot_self_id:
                                sender_name = "assistant"
                            else:
                                # sender.nickname 是发送者的 QQ 昵称[reference:1]
                                sender_name = msg.get("sender", {}).get("nickname", "User")

                            # 写入对应会话的物理隔离数据库
                            session_id = f"FriendMessage:{sender_id}"
                            db_path = self._get_db_path(session_id)
                            self._init_db(db_path)
                            self._insert_to_db(db_path, sender_id, sender_name, raw_text)

                        except json.JSONDecodeError:
                            continue

            except Exception as e:
                logger.error(f"[ChatSearch] WebSocket 连接异常: {e}，5秒后重试")
                await asyncio.sleep(5)

    def _get_db_path(self, session_id: str) -> str:
        safe_name = session_id.replace(':', '_').replace('\\', '_').replace('/', '_')
        return os.path.join(self.data_dir, f"{safe_name}.db")

    def _init_db(self, db_path: str):
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                message_text TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp DESC)")
        conn.commit()
        conn.close()

    def _insert_to_db(self, db_path: str, sender_id: str, sender_name: str, message_text: str):
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                INSERT INTO messages (sender_id, sender_name, message_text, timestamp)
                VALUES (?, ?, ?, ?)
            """, (sender_id, sender_name, message_text, time.time()))
            conn.commit()
            conn.close()
            if self.debug:
                logger.info(f"[ChatSearch] 已存储 [{sender_name}]: {message_text[:50]}...")
        except Exception as e:
            logger.error(f"[ChatSearch] 写入失败: {e}")

    def _search_history(self, db_path: str, keywords: list, limit: int = 10) -> list:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        conditions = []
        params = []
        for kw in keywords:
            if len(kw) > 1:
                conditions.append("message_text LIKE ?")
                params.append(f"%{kw}%")
        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""SELECT sender_name, message_text, timestamp 
                  FROM messages 
                  WHERE {where} 
                  AND NOT (sender_name = 'assistant' AND message_text LIKE '%🔍 检索%')
                  ORDER BY timestamp DESC LIMIT ?"""
        params.append(limit)
        c.execute(sql, params)
        results = c.fetchall()
        conn.close()
        return results

    def _format_history(self, history: list) -> str:
        lines = []
        for sender_name, msg_text, ts in reversed(history):
            lines.append(f"- [{sender_name}]: {msg_text}")
        return "\n".join(lines)

    # ========== 指令：测试检索 ==========
    @filter.command("searchtest")
    async def cmd_search_test(self, event: AstrMessageEvent, message: str):
        session_id = event.unified_msg_origin
        args = message.strip().split()

        if args and args[0] == '--all':
            keywords = args[1:]
            all_results = []
            for f in os.listdir(self.data_dir):
                if f.endswith('.db'):
                    db_path = os.path.join(self.data_dir, f)
                    sid = f[:-3]
                    for sender_name, msg_text, ts in self._search_history(db_path, keywords):
                        all_results.append((f"[{sid[:30]}...] {sender_name}", msg_text, ts))
            all_results.sort(key=lambda x: x[2])
            history = all_results[:30]
            scope = f"全局（{len(all_results)} 条）"
        else:
            keywords = args if args else message.strip().split()
            db_path = self._get_db_path(session_id)
            if not os.path.exists(db_path):
                yield event.plain_result(f"🔍 当前会话还没有任何聊天记录。")
                return
            history = self._search_history(db_path, keywords)
            scope = "当前会话"

        if not history:
            yield event.plain_result(f"🔍 在{scope}中未找到与「{' '.join(keywords)}」相关的历史记录。")
            return

        result_lines = [f"🔍 检索「{' '.join(keywords)}」{scope}命中 {len(history)} 条记录：\n"]
        for i, (sender_name, msg_text, ts) in enumerate(history, 1):
            time_str = time.strftime('%m-%d %H:%M', time.localtime(ts))
            preview = msg_text[:100] + ("..." if len(msg_text) > 100 else "")
            result_lines.append(f"{i}. [{time_str}] {sender_name}: {preview}")

        result_text = "\n".join(result_lines)
        if len(result_text) > 2000:
            result_text = result_text[:1990] + "\n...（内容过长已截断）"
        yield event.plain_result(result_text)

    # ========== 检索与注入 ==========
    @filter.on_llm_request(priority=1)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        session_id = event.unified_msg_origin
        current_text = event.message_str
        if not current_text:
            return

        keywords = [w for w in current_text if len(w) >= 1 and '\u4e00' <= w <= '\u9fff' or (w.isalpha() and len(w) > 1)]
        if not keywords:
            return

        db_path = self._get_db_path(session_id)
        if not os.path.exists(db_path):
            return

        history = self._search_history(db_path, keywords, limit=100)
        if history:
            context_text = self._format_history(history)
            injection = (
                f"## 【历史聊天记录 - 仅供参考】\n"
                f"{context_text}\n"
                f"---\n"
                f"你已自动检索到以上相关的历史聊天记录。接下来，请优先参考这些记录，用自然、亲切的语气回答用户。\n"
            )
            req.system_prompt = injection + req.system_prompt
            if self.debug:
                logger.info(f"[ChatSearch] 为会话注入 {len(history)} 条历史记录")

    async def terminate(self):
        if self.debug:
            logger.info("[ChatSearch] 插件已卸载")
