import sqlite3
import os
import time
import jieba
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

@register("satrfate_chat_search", "you", "极简聊天记录关键词检索注入插件，官方标准写法", "1.0.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "chat_history.db")

        self.debug = config.get("debug", False) if config else False
        if self.debug:
            logger.info("[ChatSearch] Debug模式开启")

        self._init_db()

    def _init_db(self):
        """初始化数据库，增加 chat_type 字段"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                message_text TEXT NOT NULL,
                timestamp REAL NOT NULL,
                chat_type TEXT NOT NULL DEFAULT 'private'
            )
        """)

        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                session_id,
                sender_name,
                message_text,
                content=messages,
                content_rowid=id,
                tokenize='unicode61'
            )
        """)

        c.execute("CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id, chat_type, timestamp DESC)")
        conn.commit()
        conn.close()

        logger.info("[ChatSearch] 数据库初始化完毕")

    # ========== 工具函数：判断聊天类型 ==========
    def _get_chat_type(self, event: AstrMessageEvent) -> str:
        """返回 'private' 或 'group'"""
        return 'group' if not event.is_private_chat() else 'private'

    # ========== 核心：存储用户消息 ==========
    @filter.event_message_type(filter.EventMessageType.PRIVATE, priority=10)
    async def log_private_message(self, event: AstrMessageEvent):
        await self._save_message(event, 'private')

    @filter.event_message_type(filter.EventMessageType.GROUP, priority=10)
    async def log_group_message(self, event: AstrMessageEvent):
        await self._save_message(event, 'group')

    async def _save_message(self, event: AstrMessageEvent, chat_type: str):
        """通用存储逻辑，区分用户和AI消息"""
        session_id = event.unified_msg_origin
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        message_text = event.message_str

        # 跳过空消息和指令
        if not message_text or not message_text.strip() or message_text.startswith('/'):
            return

        self._insert_to_db(session_id, sender_id, sender_name, message_text, chat_type)

    # ========== 核心：存储机器人自己发出的消息 ==========
    @filter.after_message_sent()
    async def log_bot_response(self, event: AstrMessageEvent):
        """听取机器人发出的所有消息，并记录"""
        result = event.get_result()
        if not result or not result.chain:
            return

        session_id = event.unified_msg_origin
        chat_type = self._get_chat_type(event)
        message_text = str(result.chain).strip()

        if not message_text:
            return

        self._insert_to_db(session_id, event.get_self_id(), "assistant", message_text, chat_type)

    def _insert_to_db(self, session_id, sender_id, sender_name, message_text, chat_type):
        """数据库写入和分词入库"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 1. 写入主表
            c.execute("""
                INSERT INTO messages (session_id, sender_id, sender_name, message_text, timestamp, chat_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, sender_id, sender_name, message_text, time.time(), chat_type))

            # 2. 分词并更新 FTS 索引
            tokens = ' '.join(jieba.cut(message_text))
            c.execute("INSERT INTO messages_fts (session_id, sender_name, message_text) VALUES (?, ?, ?)",
                      (session_id, sender_name, tokens))

            conn.commit()
            conn.close()
            if self.debug:
                logger.info(f"[ChatSearch][{chat_type}] 已存: [{sender_name}] {message_text[:40]}...")
        except Exception as e:
            logger.error(f"[ChatSearch] 数据库写入失败: {e}")

    # ========== 核心：检索与注入 ==========
    @filter.on_llm_request(priority=1)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在LLM请求前，进行全文检索并强制注入"""
        session_id = event.unified_msg_origin
        chat_type = self._get_chat_type(event)
        current_text = event.message_str

        if not current_text:
            return

        keywords = self._extract_keywords(current_text)
        if not keywords: return

        history = self._search_history(session_id, chat_type, keywords, limit=10)
        if history:
            context_text = "## 【历史聊天记录 - 仅供参考]\n"
            for sender_name, msg_text, ts in reversed(history):
                context_text += f"- [{sender_name}]: {msg_text}\n"
            
            # 注入到系统提示词最前面
            injection = (
                f"{context_text}\n"
                f"---\n"
                f"你已自动检索到以上相关的历史聊天记录。接下来，请优先参考这些记录，用自然、亲切的语气回答用户。\n"
            )
            req.system_prompt = injection + req.system_prompt
            if self.debug:
                logger.info(f"[ChatSearch] [{chat_type}] 为会话注入 {len(history)} 条记录")
        else:
            if self.debug:
                logger.info(f"[ChatSearch] 未找到与 {keywords} 相关记录")

    def _extract_keywords(self, text: str) -> list:
        """中文分词并过滤"""
        words = jieba.lcut(text)
        return [w for w in words if len(w) >= 1 and w.strip() and not w.isascii()]

    def _search_history(self, session_id: str, chat_type: str, keywords: list, limit: int = 10) -> list:
        """FTS5 全文检索，按会话和聊天类型隔离"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        fts_query = ' AND '.join([f'"{kw}"' for kw in keywords])

        try:
            sql = """
                SELECT m.sender_name, m.message_text, m.timestamp
                FROM messages_fts f
                JOIN messages m ON f.rowid = m.id
                WHERE f.session_id = ? AND m.chat_type = ? AND f.message_text MATCH ?
                ORDER BY rank LIMIT ?
            """
            c.execute(sql, (session_id, chat_type, fts_query, limit))
            results = c.fetchall()
        except Exception:
            # 降级 LIKE
            conditions = ["m.chat_type = ?"]
            params = [chat_type]
            for kw in keywords:
                conditions.append("m.message_text LIKE ?")
                params.append(f"%{kw}%")
            where = " AND ".join(conditions)
            sql = f"SELECT m.sender_name, m.message_text, m.timestamp FROM messages m WHERE m.session_id = ? AND {where} ORDER BY m.timestamp DESC LIMIT ?"
            params.insert(0, session_id)
            params.append(limit)
            c.execute(sql, params)
            results = c.fetchall()

        conn.close()
        return results

    async def terminate(self):
        logger.info("[ChatSearch] 插件已卸载")
