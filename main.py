import sqlite3
import os
import time
import re
import asyncio
import subprocess
import sys
from astrbot.api.event import filter, AstrMessageEvent, EventType
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

# ============================================
# 自动安装并导入 jieba
# ============================================
JIEBA_AVAILABLE = False
try:
    import jieba
    jieba.initialize()
    JIEBA_AVAILABLE = True
except ImportError:
    logger.warning("[ChatSearch] jieba 未安装，正在尝试自动安装...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "jieba", "-q"])
        import jieba
        jieba.initialize()
        JIEBA_AVAILABLE = True
        logger.info("[ChatSearch] jieba 自动安装成功！")
    except Exception as e:
        logger.error(f"[ChatSearch] jieba 自动安装失败: {e}，将降级为 bigram 提取方式。")

# ============================================
# 停用词（空，用户自行添加）
# ============================================
STOP_WORDS = set()
# 添加示例：STOP_WORDS = {"的", "了", "在", "是", "我", "有"}

# ============================================
# 插件主体
# ============================================
@register("satrfate_chat_search", "Satrfate", "极简记忆插件·支持群聊/私聊存储与检索", "9.3.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)

        self.fixed_dir = os.path.join(self.data_dir, "fixed_memories")
        os.makedirs(self.fixed_dir, exist_ok=True)

        self.debug = config.get("debug", False) if config else False
        self.max_inject = config.get("max_inject", 50) if config else 50
        self.max_search_limit = config.get("max_search_limit", 500) if config else 500
        self._pending = {}

        self.use_jieba = config.get("use_jieba", True) if config else True
        if self.use_jieba and not JIEBA_AVAILABLE:
            logger.warning("[ChatSearch] jieba 不可用，降级为 bigram 模式。")
            self.use_jieba = False

        # 自定义停用词（从配置读取）
        custom_stopwords = config.get("custom_stopwords", "") if config else ""
        if custom_stopwords:
            for w in custom_stopwords.strip().split("\n"):
                w = w.strip()
                if w:
                    STOP_WORDS.add(w)

        # 预编译停用词正则
        if STOP_WORDS:
            sorted_stops = sorted(STOP_WORDS, key=len, reverse=True)
            self.stop_regex = re.compile('|'.join(re.escape(w) for w in sorted_stops))
        else:
            self.stop_regex = None

        asyncio.create_task(self._cleanup_pending())

        logger.info("[ChatSearch] ========== 插件加载成功 ==========")
        logger.info(f"[ChatSearch] 数据目录: {self.data_dir}")
        logger.info(f"[ChatSearch] 调试模式: {self.debug}")
        logger.info(f"[ChatSearch] 使用 jieba: {self.use_jieba}")
        logger.info(f"[ChatSearch] 停用词数量: {len(STOP_WORDS)}")
        logger.info("[ChatSearch] 已支持私聊和群聊")

    # ============================================================
    # 固定记忆文件操作
    # ============================================================
    def _get_fixed_path(self, user_id: str) -> str:
        return os.path.join(self.fixed_dir, f"{user_id}.txt")

    def _get_fixed_memory(self, user_id: str) -> str:
        path = self._get_fixed_path(user_id)
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"[ChatSearch] 读取固定记忆失败: {e}")
            return ""

    def _set_fixed_memory(self, user_id: str, content: str) -> bool:
        path = self._get_fixed_path(user_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content.strip())
            return True
        except Exception as e:
            logger.error(f"[ChatSearch] 保存固定记忆失败: {e}")
            return False

    def _clear_fixed_memory(self, user_id: str) -> bool:
        path = self._get_fixed_path(user_id)
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except Exception as e:
            logger.error(f"[ChatSearch] 删除固定记忆失败: {e}")
            return False

    # ============================================================
    # 数据库操作
    # ============================================================
    def _db(self, session_id: str) -> str:
        parts = session_id.split(':')
        if len(parts) >= 3:
            simplified = ':'.join(parts[-2:])
        else:
            simplified = session_id
        return os.path.join(self.data_dir, simplified.replace(':', '_') + ".db")

    def _save(self, session_id: str, sender_id: str, sender_name: str, text: str):
        if not text.strip():
            return
        db = self._db(session_id)
        os.makedirs(os.path.dirname(db), exist_ok=True)
        try:
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT, sender_name TEXT, message_text TEXT, timestamp REAL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
            conn.execute(
                "INSERT INTO messages(sender_id, sender_name, message_text, timestamp) VALUES(?,?,?,?)",
                (sender_id, sender_name, text, time.time())
            )
            conn.commit()
            logger.info(f"[ChatSearch] ✅ 存储成功: {os.path.basename(db)} [{sender_name}]：{text[:30]}...")
        except sqlite3.Error as e:
            logger.error(f"[ChatSearch] 存储失败: {e}")
        finally:
            conn.close()

    def _search(self, db_path: str, keywords: list) -> list:
        if not keywords:
            return []
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            conditions = " OR ".join(["message_text LIKE ?"] * len(keywords))
            params = [f"%{k}%" for k in keywords]
            sql = f"""
                SELECT sender_name, message_text, timestamp
                FROM messages
                WHERE {conditions}
                ORDER BY timestamp DESC
                LIMIT {self.max_search_limit}
            """
            c.execute(sql, params)
            res = c.fetchall()
        except sqlite3.Error as e:
            logger.error(f"[ChatSearch] 检索失败: {e}")
            res = []
        finally:
            conn.close()
        if self.debug:
            logger.info(f"[ChatSearch] 关键词检索：{keywords}，命中 {len(res)} 条")
        return res

    # ============================================================
    # 格式化历史消息
    # ============================================================
    def _format_history(self, history: list) -> str:
        lines = []
        for row in reversed(history):
            sender_name = row[0]
            text = row[1]
            lines.append(f"[{sender_name}]：{text}")
        return "\n\n".join(lines)

    # ============================================================
    # pending 清理
    # ============================================================
    async def _cleanup_pending(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [key for key, data in self._pending.items() if now - data.get("time", 0) > 30]
            for key in expired:
                del self._pending[key]
                if self.debug:
                    logger.info(f"[ChatSearch] 清理超时 pending: {key}")

    # ============================================================
    # 命令：搜索历史
    # ============================================================
    @filter.command("searchtest")
    async def cmd_search_test(self, event: AstrMessageEvent, message: str):
        session_id = event.message_obj.session_id
        args = message.strip().split()

        if args and args[0] == '--all':
            keywords = args[1:]
            if not keywords:
                yield event.plain_result("用法：searchtest --all <关键词1> [关键词2...]")
                return
            all_results = []
            for f in os.listdir(self.data_dir):
                if f.endswith('.db'):
                    db_path = os.path.join(self.data_dir, f)
                    for sender_name, msg_text, ts in self._search(db_path, keywords):
                        all_results.append((f"[{f[:-3][:30]}...] {sender_name}", msg_text, ts))
            all_results.sort(key=lambda x: x[2])
            history = all_results[:30]
            scope = f"全局（{len(all_results)} 条）"
        else:
            keywords = args if args else message.strip().split()
            if not keywords:
                yield event.plain_result("用法：searchtest <关键词1> [关键词2...] 或 searchtest --all <关键词>")
                return
            db_path = self._db(session_id)
            if not os.path.exists(db_path):
                yield event.plain_result("🔍 当前会话还没有任何聊天记录。")
                return
            history = self._search(db_path, keywords)
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

    # ============================================================
    # 固定记忆管理命令
    # ============================================================
    @filter.command("setfixed")
    async def cmd_set_fixed(self, event: AstrMessageEvent):
        content = event.message_str.strip()
        if not content:
            yield event.plain_result("用法：/setfixed 你的固定记忆内容")
            return
        content = re.sub(r'\n\s*\n+', '\n', content)
        uid = str(event.get_sender_id())
        if self._set_fixed_memory(uid, content):
            yield event.plain_result("✅ 固定记忆已保存。")
        else:
            yield event.plain_result("❌ 保存失败。")

    @filter.command("getfixed")
    async def cmd_get_fixed(self, event: AstrMessageEvent):
        uid = str(event.get_sender_id())
        content = self._get_fixed_memory(uid)
        if content:
            if len(content) > 500:
                content = content[:500] + "\n...(内容过长，已截断)"
            yield event.plain_result(f"你的固定记忆：\n{content}")
        else:
            yield event.plain_result("你还没有设置固定记忆。")

    @filter.command("clearfixed")
    async def cmd_clear_fixed(self, event: AstrMessageEvent):
        uid = str(event.get_sender_id())
        if self._clear_fixed_memory(uid):
            yield event.plain_result("✅ 固定记忆已清除。")
        else:
            yield event.plain_result("❌ 清除失败。")

    # ============================================================
    # 核心：事件监听（存储群聊消息）
    # ============================================================
    @filter.event(EventType.AdapterMessageEvent)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息事件，存储群聊消息到数据库"""
        # 只处理群聊
        if not event.message_obj.group_id:
            return
        # 忽略机器人自己的消息
        if event.get_sender_id() == event.message_obj.self_id:
            return

        session_id = event.message_obj.session_id
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        message_text = event.message_str

        if not message_text or not message_text.strip():
            return

        logger.info(f"[ChatSearch] 📩 群聊消息 | {session_id} | {sender_name}: {message_text[:30]}...")
        self._save(session_id, sender_id, sender_name, message_text)

    # ============================================================
    # 核心钩子：拦截 LLM 请求并注入记忆（私聊可能仍有效）
    # ============================================================
    @filter.on_llm_request(priority=1)
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        text = event.message_str.strip()
        if not text or text.startswith("/"):
            return

        session_id = event.message_obj.session_id
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()

        pending_key = f"{session_id}:{sender_id}"
        self._pending[pending_key] = {"user": (sender_id, sender_name, text), "time": time.time()}

        # 提取关键词（使用停用词过滤）
        filtered_text = text
        if self.stop_regex:
            filtered_text = self.stop_regex.sub('', text)

        keywords = []
        if self.use_jieba and JIEBA_AVAILABLE:
            words = jieba.lcut(filtered_text)
            keywords = [w for w in words if len(w) >= 2 and w not in STOP_WORDS]
            keywords = list(set(keywords))
        else:
            for i in range(len(filtered_text) - 1):
                bigram = filtered_text[i:i+2]
                if '\u4e00' <= bigram[0] <= '\u9fff' and '\u4e00' <= bigram[1] <= '\u9fff':
                    if bigram not in STOP_WORDS:
                        keywords.append(bigram)
            keywords = list(set(keywords))

        injection_parts = []

        fixed_content = self._get_fixed_memory(str(sender_id))
        if fixed_content:
            injection_parts.append(f"## 【固定记忆】\n{fixed_content}")

        history = []
        if keywords:
            db_path = self._db(session_id)
            if os.path.exists(db_path):
                history = self._search(db_path, keywords)
                if history:
                    if len(history) > self.max_inject:
                        history = history[-self.max_inject:]
                    history_text = self._format_history(history)
                    injection_parts.append(
                        f"## 【记忆回溯 - 共 {len(history)} 条往事】\n{history_text}\n---\n上面是你脑海中浮现的往事。"
                    )

        if injection_parts:
            combined_injection = "\n\n".join(injection_parts)
            req.system_prompt = combined_injection + "\n\n" + (req.system_prompt or "")
            logger.info(f"[ChatSearch] ✅ 注入 {len(history)} 条记忆")

    # ============================================================
    # 核心钩子：消息发送后存储 AI 回复
    # ============================================================
    @filter.after_message_sent()
    async def on_after_sent(self, event: AstrMessageEvent):
        session_id = event.message_obj.session_id
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()

        pending_key = f"{session_id}:{sender_id}"
        pending = self._pending.pop(pending_key, None)
        if not pending:
            return

        user_text = pending["user"][2].strip()
        result = event.get_result()

        if not result or not result.chain:
            self._save(session_id, sender_id, sender_name, user_text)
            return

        ai_text = ""
        for comp in result.chain:
            ai_text += comp.text if hasattr(comp, 'text') else str(comp)
        ai_text = ai_text.strip()

        if not ai_text:
            self._save(session_id, sender_id, sender_name, user_text)
            return

        ai_text = re.sub(r'\n\s*\n+', '\n', ai_text)
        combined = f"用户 {sender_name}：{user_text}\nAI回复：{ai_text}"
        self._save(session_id, sender_id, sender_name, combined)

    # ============================================================
    # 对外公开 API
    # ============================================================
    def get_messages_since(self, session_id: str, after_timestamp: float, limit: int = 100) -> list:
        db_path = self._db(session_id)
        if not os.path.exists(db_path):
            return []
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(
                "SELECT sender_id, sender_name, message_text, timestamp FROM messages WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
                (after_timestamp, limit)
            )
            rows = c.fetchall()
            result = [{k: row[k] for k in row.keys()} for row in rows]
            conn.close()
            return result
        except sqlite3.Error as e:
            logger.error(f"[ChatSearch] 获取历史消息失败: {e}")
            return []

    def extract_keywords(self, text: str) -> list:
        if not text:
            return []
        filtered_text = text
        if self.stop_regex:
            filtered_text = self.stop_regex.sub('', text)
        if self.use_jieba and JIEBA_AVAILABLE:
            words = jieba.lcut(filtered_text)
            return list(set([w for w in words if len(w) >= 2 and w not in STOP_WORDS]))
        else:
            keywords = []
            for i in range(len(filtered_text) - 1):
                bigram = filtered_text[i:i+2]
                if '\u4e00' <= bigram[0] <= '\u9fff' and '\u4e00' <= bigram[1] <= '\u9fff':
                    if bigram not in STOP_WORDS:
                        keywords.append(bigram)
            return list(set(keywords))

    def get_messages_count(self, session_id: str, after_timestamp: float) -> int:
        db_path = self._db(session_id)
        if not os.path.exists(db_path):
            return 0
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM messages WHERE timestamp > ?", (after_timestamp,))
            count = c.fetchone()[0]
            conn.close()
            return count
        except sqlite3.Error as e:
            logger.error(f"[ChatSearch] 统计消息数失败: {e}")
            return 0
