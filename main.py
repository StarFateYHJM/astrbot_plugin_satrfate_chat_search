import sqlite3
import os
import time
import jieba
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

@register("satrfate_chat_search", "you", "极简聊天记录检索注入插件，按会话物理隔离，群聊只记录@消息", "2.1.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)

        self.debug = config.get("debug", False) if config else False
        if self.debug:
            logger.info("[ChatSearch] 调试模式已开启")

    def _get_db_path(self, session_id: str) -> str:
        """根据会话ID返回数据库文件路径（跨平台安全）"""
        safe_name = session_id.replace(':', '_').replace('\\', '_').replace('/', '_')
        return os.path.join(self.data_dir, f"{safe_name}.db")

    def _init_db(self, db_path: str):
        """初始化单个会话的数据库"""
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

        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                sender_name,
                message_text,
                content=messages,
                content_rowid=id,
                tokenize='unicode61'
            )
        """)

        c.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp DESC)")
        conn.commit()
        conn.close()

    # ========== 工具：检测 @机器人 ==========
    def _is_mentioned(self, event: AstrMessageEvent) -> bool:
        """判断群聊消息是否 @了机器人"""
        message_text = event.message_str
        self_id = event.get_self_id()
        return f'[CQ:at,qq={self_id}]' in message_text

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
                    results = self._search_history(db_path, keywords, limit=5)
                    for sender_name, msg_text, ts in results:
                        all_results.append((f"[{sid[:30]}...] {sender_name}", msg_text, ts))
            all_results.sort(key=lambda x: x[2], reverse=True)
            history = all_results[:20]
            scope = f"全局（{len(all_results)} 条）"
        else:
            keywords = args if args else message.strip().split()
            db_path = self._get_db_path(session_id)
            if not os.path.exists(db_path):
                yield event.plain_result(f"🔍 当前会话还没有任何聊天记录。")
                return
            history = self._search_history(db_path, keywords, limit=20)
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

    # ========== 存储用户消息（群聊只记录 @机器人）==========
    @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    async def log_message(self, event: AstrMessageEvent):
        session_id = event.unified_msg_origin
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        message_text = event.message_str

        if not message_text or not message_text.strip():
            return

        # 群聊只记录 @机器人 的消息
        if not event.is_private_chat():
            if not self._is_mentioned(event):
                return

        db_path = self._get_db_path(session_id)
        self._init_db(db_path)
        self._insert_to_db(db_path, sender_id, sender_name, message_text)

    # ========== 存储 AI 回复（群聊只记录 @机器人 引发的回复）==========
    @filter.after_message_sent()
    async def log_bot_response(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not result.chain:
            return

        session_id = event.unified_msg_origin

        # 群聊回复也只在@过的上下文里记录
        if not event.is_private_chat():
            # 简单判断：如果数据库已存在该会话的@消息，则记录回复
            db_path = self._get_db_path(session_id)
            if not os.path.exists(db_path):
                return
        else:
            db_path = self._get_db_path(session_id)

        message_text = ""
        for comp in result.chain:
            if hasattr(comp, 'text'):
                message_text += comp.text
            else:
                message_text += str(comp)
        message_text = message_text.strip()

        if not message_text:
            return

        self._init_db(db_path)
        self._insert_to_db(db_path, event.get_self_id(), "assistant", message_text)

    def _insert_to_db(self, db_path: str, sender_id: str, sender_name: str, message_text: str):
        """写入消息并同步 FTS 索引"""
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()

            c.execute("""
                INSERT INTO messages (sender_id, sender_name, message_text, timestamp)
                VALUES (?, ?, ?, ?)
            """, (sender_id, sender_name, message_text, time.time()))

            tokens = ' '.join(jieba.cut(message_text))
            c.execute("INSERT INTO messages_fts (sender_name, message_text) VALUES (?, ?)",
                      (sender_name, tokens))

            conn.commit()
            conn.close()

            if self.debug:
                logger.info(f"[ChatSearch] 已存储 [{sender_name}]: {message_text[:50]}...")
        except Exception as e:
            logger.error(f"[ChatSearch] 写入失败: {e}")

    # ========== 检索与注入 ==========
    @filter.on_llm_request(priority=1)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if hasattr(req, 'func_tool') and req.func_tool:
            req.func_tool = None
    
        session_id = event.unified_msg_origin
        current_text = event.message_str

        if not current_text:
            return

        keywords = self._extract_keywords(current_text)
        if not keywords:
            return

        db_path = self._get_db_path(session_id)
        if not os.path.exists(db_path):
            return

        history = self._search_history(db_path, keywords, limit=10)
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
                logger.info(f"[ChatSearch] 为会话 {session_id[:30]}... 注入 {len(history)} 条记录")
        else:
            if self.debug:
                logger.info(f"[ChatSearch] 未找到与 {keywords} 相关的历史记录")

    def _extract_keywords(self, text: str) -> list:
        words = jieba.lcut(text)
        return [w for w in words if len(w) >= 1 and w.strip() and not w.isascii()]

    def _search_history(self, db_path: str, keywords: list, limit: int = 10) -> list:
        """从指定数据库文件中检索历史消息"""
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        fts_query = ' AND '.join([f'"{kw}"' for kw in keywords])

        try:
            sql = """
                SELECT m.sender_name, m.message_text, m.timestamp
                FROM messages_fts f
                JOIN messages m ON f.rowid = m.id
                WHERE f.message_text MATCH ?
                ORDER BY rank LIMIT ?
            """
            c.execute(sql, (fts_query, limit))
            results = c.fetchall()
        except Exception:
            conditions = []
            params = []
            for kw in keywords:
                conditions.append("m.message_text LIKE ?")
                params.append(f"%{kw}%")
            where = " AND ".join(conditions)
            if where:
                sql = f"SELECT m.sender_name, m.message_text, m.timestamp FROM messages m WHERE {where} ORDER BY m.timestamp DESC LIMIT ?"
                params.append(limit)
                c.execute(sql, params)
                results = c.fetchall()
            else:
                results = []

        conn.close()
        return results

    def _format_history(self, history: list) -> str:
        lines = []
        for sender_name, msg_text, ts in reversed(history):
            lines.append(f"- [{sender_name}]: {msg_text}")
        return "\n".join(lines)

    async def terminate(self):
        if self.debug:
            logger.info("[ChatSearch] 插件已卸载")
