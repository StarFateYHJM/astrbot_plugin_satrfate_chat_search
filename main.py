import sqlite3
import os
import time
import jieba
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

@register("satrfate_chat_search", "you", "极简聊天记录关键词检索注入插件", "1.0.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "chat_history.db")

        self.debug = config.get("debug", False) if config else False
        if self.debug:
            logger.info("[ChatSearch] 调试模式已开启")

        self._init_db()

    def _init_db(self):
        """初始化数据库：主存储表 + FTS5 全文索引表"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 主存储表
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

        # FTS5 全文索引表
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                session_id,
                message_text,
                content=messages,
                content_rowid=id,
                tokenize='unicode61'
            )
        """)

        c.execute("CREATE INDEX IF NOT EXISTS idx_session_time ON messages(session_id, timestamp DESC)")
        conn.commit()
        conn.close()

        if self.debug:
            logger.info("[ChatSearch] 数据库与FTS索引初始化完毕")

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
        """存储消息并同步更新 FTS 索引"""
        session_id = event.unified_msg_origin
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        message_text = event.message_str

        if not message_text or not message_text.strip():
            return

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 插入主表
        c.execute("""
            INSERT INTO messages (session_id, sender_id, sender_name, message_text, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, sender_id, sender_name, message_text, time.time()))

        # 用 jieba 分词后写入 FTS 索引表
        tokens = ' '.join(jieba.cut(message_text))
        c.execute("INSERT INTO messages_fts (session_id, message_text) VALUES (?, ?)", (session_id, tokens))

        conn.commit()
        conn.close()

        if self.debug:
            logger.info(f"[ChatSearch] 已存储消息 [{sender_name}]: {message_text[:50]}...")
            logger.info(f"[ChatSearch] FTS分词结果: {tokens[:100]}...")

    # ========== 检索与注入 ==========
    @filter.on_llm_request(priority=1)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在 LLM 请求前，根据当前消息关键词检索历史并注入"""
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
        """用 jieba 提取中文关键词"""
        words = jieba.lcut(text)
        # 过滤长度小于2的词和纯标点
        keywords = [w for w in words if len(w) >= 2 and w.strip()]
        if self.debug:
            logger.info(f"[ChatSearch] 提取关键词: {keywords}")
        return keywords

    def _search_history(self, session_id: str, keywords: list, limit: int = 10) -> list:
        """通过 FTS5 全文索引高效检索历史消息"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 构建 FTS MATCH 查询：每个关键词用 AND 连接
        fts_query = ' AND '.join([f'"{kw}"' for kw in keywords])

        try:
            sql = """
                SELECT m.sender_name, m.message_text, m.timestamp
                FROM messages_fts f
                JOIN messages m ON f.rowid = m.id
                WHERE f.session_id = ? AND f.message_text MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            c.execute(sql, (session_id, fts_query, limit))
            results = c.fetchall()
        except sqlite3.OperationalError:
            # 如果 MATCH 语法错误，降级为 LIKE 查询
            if self.debug:
                logger.warning(f"[ChatSearch] FTS查询失败，降级为LIKE")

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
        """将检索到的历史记录格式化为适合 LLM 的文本"""
        lines = []
        for sender_name, msg_text, ts in reversed(history):
            lines.append(f"- [{sender_name}]: {msg_text}")
        return "\n".join(lines)

    async def terminate(self):
        if self.debug:
            logger.info("[ChatSearch] 插件已卸载")
