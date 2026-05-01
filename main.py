import sqlite3
import os
import time
from astrbot.api.event import filter, AstrMessageEvent, EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

@register("satrfate_chat_search", "you", "极简聊天记录关键词检索注入插件", "1.0.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = os.path.join(context.get_data_dir(), "satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "chat_history.db")

        config = context.get_config() or {}
        self.debug = config.get("debug", False)
        if self.debug:
            logger.info("[ChatSearch] 调试模式已开启")

        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                sender_id TEXT,
                sender_name TEXT,
                message_text TEXT,
                timestamp REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_session_time ON messages(session_id, timestamp DESC)")
        conn.commit()
        conn.close()
        if self.debug:
            logger.info("[ChatSearch] 数据库初始化完毕")

    # ========== 指令：测试检索 ==========
    @filter.command("searchtest")
    async def cmd_search_test(self, event: AstrMessageEvent, message: str):
        session_id = event.unified_msg_origin
        keywords = message.strip().split() if message.strip() else []

        if not keywords:
            yield event.plain_result("❌ 请提供一个关键词，例如：/searchtest 索拉图")
            return

        history = self._search_history(session_id, keywords, limit=20)

        if not history:
            yield event.plain_result(f"🔍 在当前会话中未找到与「{' '.join(keywords)}」相关的历史记录。")
            return

        result_lines = [f"🔍 检索「{' '.join(keywords)}」命中 {len(history)} 条记录：\n"]
        for i, (sender_name, msg_text, ts) in enumerate(history, 1):
            time_str = time.strftime('%m-%d %H:%M', time.localtime(ts))
            preview = msg_text[:100] + ("..." if len(msg_text) > 100 else "")
            result_lines.append(f"{i}. [{time_str}] {sender_name}: {preview}")

        result_text = "\n".join(result_lines)
        if len(result_text) > 2000:
            result_text = result_text[:1990] + "\n...（内容过长已截断）"

        yield event.plain_result(result_text)

    # ========== 存储消息 ==========
    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def log_message(self, event: AstrMessageEvent):
        session_id = event.unified_msg_origin
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        message_text = event.message_str

        if not message_text or not message_text.strip():
            return

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            INSERT INTO messages (session_id, sender_id, sender_name, message_text, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, sender_id, sender_name, message_text, time.time()))
        conn.commit()
        conn.close()

        if self.debug:
            logger.info(f"[ChatSearch] 已存储消息 [{sender_name}]: {message_text[:50]}...")

    # ========== 检索与注入 ==========
    @filter.on_llm_request(priority=1)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        session_id = event.unified_msg_origin
        current_text = event.message_str

        if not current_text:
            return

        keywords = self._extract_keywords(current_text)
        if not keywords:
            if self.debug:
                logger.info("[ChatSearch] 未提取到有效关键词，跳过注入")
            return

        history = self._search_history(session_id, keywords, limit=10)
        if history:
            context_text = self._format_history(history)
            req.system_prompt += f"\n\n---\n[系统提示] 以下是与当前问题相关的历史聊天记录，供你参考：\n{context_text}\n---"

            if self.debug:
                logger.info(f"[ChatSearch] 为会话 {session_id} 注入 {len(history)} 条历史记录")
        else:
            if self.debug:
                logger.info(f"[ChatSearch] 未找到与关键词 {keywords} 匹配的历史记录")

    def _extract_keywords(self, text: str) -> list:
        return [w for w in text.split() if len(w) > 1]

    def _search_history(self, session_id: str, keywords: list, limit: int = 10) -> list:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        conditions = []
        params = [session_id]
        for kw in keywords:
            conditions.append("message_text LIKE ?")
            params.append(f"%{kw}%")

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT sender_name, message_text, timestamp
            FROM messages
            WHERE session_id = ? AND {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        c.execute(sql, params)
        results = c.fetchall()
        conn.close()

        if self.debug:
            logger.info(f"[ChatSearch] 检索关键词: {keywords}, 命中 {len(results)} 条记录")

        return results

    def _format_history(self, history: list) -> str:
        lines = []
        for sender_name, msg_text, ts in reversed(history):
            lines.append(f"- [{sender_name}]: {msg_text}")
        return "\n".join(lines)

    async def terminate(self):
        if self.debug:
            logger.info("[ChatSearch] 插件已卸载")
