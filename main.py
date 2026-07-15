import sqlite3
import os
import time
import re
import asyncio
import subprocess
import sys
from astrbot.api.event import filter, AstrMessageEvent
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
# 停用词表（完整，已提供）
# ============================================
STOP_WORDS = {
    此处为关键词 省略
}

@register("satrfate_chat_search", "Satrfate", "极简记忆插件·精准分词+用户固定记忆（仅私聊）", "9.3.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        # 固定记忆存储目录
        self.fixed_dir = os.path.join(self.data_dir, "fixed_memories")
        os.makedirs(self.fixed_dir, exist_ok=True)

        self.bot_id = config.get("bot_self_id", "") if config else ""
        self.debug = config.get("debug", False) if config else False
        self.max_inject = config.get("max_inject", 50) if config else 50
        self.max_search_limit = config.get("max_search_limit", 500) if config else 500
        self._pending = {}

        # 是否使用 jieba（配置可强制关闭）
        self.use_jieba = config.get("use_jieba", True) if config else True
        if self.use_jieba and not JIEBA_AVAILABLE:
            logger.warning("[ChatSearch] 配置要求使用 jieba 但 jieba 不可用，将降级为 bigram 模式。")
            self.use_jieba = False

        # 自定义停用词
        custom_stopwords = config.get("custom_stopwords", "") if config else ""
        if custom_stopwords:
            for w in custom_stopwords.strip().split("\n"):
                w = w.strip()
                if w and w not in STOP_WORDS:
                    STOP_WORDS.add(w)

        # 预编译停用词正则
        sorted_stops = sorted(STOP_WORDS, key=len, reverse=True)
        self.stop_regex = re.compile('|'.join(re.escape(w) for w in sorted_stops))

        # 启动 pending 清理
        asyncio.create_task(self._cleanup_pending())

        if self.debug:
            logger.info(f"[ChatSearch] 调试模式已开启（仅私聊），使用 jieba: {self.use_jieba}")

    # ========== 固定记忆文件操作 ==========
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
            logger.error(f"[ChatSearch] 读取固定记忆失败 {user_id}: {e}")
            return ""

    def _set_fixed_memory(self, user_id: str, content: str) -> bool:
        path = self._get_fixed_path(user_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content.strip())
            return True
        except Exception as e:
            logger.error(f"[ChatSearch] 保存固定记忆失败 {user_id}: {e}")
            return False

    def _clear_fixed_memory(self, user_id: str) -> bool:
        path = self._get_fixed_path(user_id)
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except Exception as e:
            logger.error(f"[ChatSearch] 删除固定记忆失败 {user_id}: {e}")
            return False

    # ---------- 数据库操作 ----------
    def _db(self, sid):
        return os.path.join(self.data_dir, sid.replace(':', '_') + ".db")

    def _save(self, sid, sender_id, name, text):
        if not text.strip():
            return
        db = self._db(sid)
        os.makedirs(os.path.dirname(db), exist_ok=True)
        try:
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT, sender_name TEXT, message_text TEXT, timestamp REAL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
            conn.execute(
                "INSERT INTO messages(sender_id, sender_name, message_text, timestamp) VALUES(?,?,?,?)",
                (sender_id, name, text, time.time())
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"[ChatSearch] 存储失败: {e}")
        finally:
            conn.close()
        if self.debug:
            logger.info(f"[ChatSearch] 存储 [{name}]：{text[:40]}...")

    def _search(self, db, kw):
        if not kw:
            return []
        try:
            conn = sqlite3.connect(db)
            c = conn.cursor()
            conditions = " OR ".join(["message_text LIKE ?"] * len(kw))
            params = [f"%{k}%" for k in kw]
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
            logger.info(f"[ChatSearch] 关键词检索：{kw}，命中 {len(res)} 条")
        return res

    # ---------- 格式化 ----------
    def _fmt(self, hist):
        lines = []
        for r in reversed(hist):
            text = r[1]
            text = text.replace("用户：", "你说：").replace("AI回复：", "我回应：")
            text = text.replace("[assistant]", "我").replace(f"[{r[0]}]", "你")
            lines.append(text)
        return "\n\n".join(lines)

    # ---------- pending 超时清理 ----------
    async def _cleanup_pending(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [sid for sid, data in self._pending.items() if now - data.get("time", 0) > 30]
            for sid in expired:
                del self._pending[sid]
                if self.debug:
                    logger.info(f"[ChatSearch] 清理超时 pending: {sid}")

    # ---------- 命令 ----------
    @filter.command("searchtest")
    async def cmd_search_test(self, event: AstrMessageEvent, message: str):
        if not event.is_private_chat():
            yield event.plain_result("⚠️ 当前插件已禁用群聊记忆功能，仅支持私聊搜索。")
            return

        uid = event.get_sender_id()
        sid = f"FriendMessage:{uid}"
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
            db_path = self._db(sid)
            if not os.path.exists(db_path):
                yield event.plain_result("🔍 当前私聊会话还没有任何聊天记录。")
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

    # ========== 固定记忆管理命令（修复版） ==========
    @filter.command("setfixed")
    async def cmd_set_fixed(self, event: AstrMessageEvent):
        """设置当前用户的固定记忆（覆盖原有）"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用此命令。")
            return
        # event.message_str 已经自动去除了 /setfixed 前缀和空格，直接就是内容
        content = event.message_str.strip()
        if not content:
            yield event.plain_result("用法：/setfixed 你的固定记忆内容（可换行）\n例如：/setfixed 我是...")
            return
        # 压缩多余空行（连续两个及以上换行符 → 单个换行符）
        import re
        content = re.sub(r'\n\s*\n+', '\n', content)
        uid = str(event.get_sender_id())
        if self._set_fixed_memory(uid, content):
            yield event.plain_result("✅ 固定记忆已保存。")
        else:
            yield event.plain_result("❌ 保存失败，请检查日志。")

    @filter.command("getfixed")
    async def cmd_get_fixed(self, event: AstrMessageEvent):
        """查看当前用户的固定记忆"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用此命令。")
            return
        uid = str(event.get_sender_id())
        content = self._get_fixed_memory(uid)
        if content:
            if len(content) > 500:
                content = content[:500] + "\n...(内容过长，已截断)"
            yield event.plain_result(f"你的固定记忆：\n{content}")
        else:
            yield event.plain_result("你还没有设置固定记忆。使用 /setfixed 内容 来设置。")

    @filter.command("clearfixed")
    async def cmd_clear_fixed(self, event: AstrMessageEvent):
        """清除当前用户的固定记忆"""
        if not event.is_private_chat():
            yield event.plain_result("请在私聊中使用此命令。")
            return
        uid = str(event.get_sender_id())
        if self._clear_fixed_memory(uid):
            yield event.plain_result("✅ 固定记忆已清除。")
        else:
            yield event.plain_result("❌ 清除失败，请检查日志。")

    # ---------- 核心钩子 ----------
    @filter.on_llm_request(priority=1)
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        if not event.is_private_chat():
            return

        text = event.message_str.strip()
        if not text or text.startswith("/"):
            return

        uid = event.get_sender_id()
        name = event.get_sender_name()
        sid = f"FriendMessage:{uid}"
        self._pending[sid] = {"user": (uid, name, text), "time": time.time()}
        if self.debug:
            logger.info(f"[ChatSearch] 暂存用户消息 [{name}]：{text[:40]}...")

        # 删除停用词
        filtered_text = self.stop_regex.sub('', text)

        kw = []
        if self.use_jieba and JIEBA_AVAILABLE:
            words = jieba.lcut(filtered_text)
            kw = [w for w in words if len(w) >= 2 and w not in STOP_WORDS]
            kw = list(set(kw))
        else:
            for i in range(len(filtered_text) - 1):
                bigram = filtered_text[i:i+2]
                if '\u4e00' <= bigram[0] <= '\u9fff' and '\u4e00' <= bigram[1] <= '\u9fff':
                    if bigram[0] in STOP_WORDS or bigram[1] in STOP_WORDS:
                        continue
                    if bigram not in STOP_WORDS:
                        kw.append(bigram)
            kw = list(set(kw))

        injection_parts = []

        # 固定记忆（按用户从文件读取）
        fixed_content = self._get_fixed_memory(str(uid))
        if fixed_content:
            injection_parts.append(f"## 【固定记忆】\n{fixed_content}")

        # 检索注入
        hist = []
        if kw:
            db = self._db(sid)
            if os.path.exists(db):
                hist = self._search(db, kw)
                if hist:
                    if len(hist) > self.max_inject:
                        hist = hist[-self.max_inject:]
                    history_text = self._fmt(hist)
                    injection_parts.append(f"## 【记忆回溯 - 共 {len(hist)} 条往事】\n{history_text}\n---\n上面是你脑海中浮现的往事。")

        if injection_parts:
            combined_injection = "\n\n".join(injection_parts)
            original_prompt = req.system_prompt or ""
            req.system_prompt = combined_injection + "\n\n" + original_prompt
            if self.debug:
                if fixed_content:
                    # 统计固定记忆的条数（按【...】分割，每个标题算一条）
                    fixed_count = fixed_content.count('【')
                    fixed_chars = len(fixed_content)
                    logger.info(f"[ChatSearch] 注入内容：固定注入 {fixed_count} 条 ({fixed_chars} 字)，检索注入 {len(hist)} 条")
                else:
                    logger.info(f"[ChatSearch] 注入内容：无固定记忆，检索注入 {len(hist)} 条")

    @filter.after_message_sent()
    async def on_after_sent(self, event: AstrMessageEvent):
        if not event.is_private_chat():
            return

        sid = f"FriendMessage:{event.get_sender_id()}"
        pending = self._pending.pop(sid, None)
        if not pending:
            return

        user = pending["user"]
        result = event.get_result()
        if not result or not result.chain:
            self._save(sid, user[0], user[1], user[2])
            return

        ai_text = ""
        for comp in result.chain:
            if hasattr(comp, 'text'):
                ai_text += comp.text
            else:
                ai_text += str(comp)
        ai_text = ai_text.strip()
        if not ai_text:
            self._save(sid, user[0], user[1], user[2])
            return

        # 删除 AI 回复中的空行
        ai_text = re.sub(r'\n\s*\n+', '\n', ai_text)

        user_text = user[2].strip()
        ai_text = ai_text.strip()
        combined = f"用户：{user_text}\nAI回复：{ai_text}"
        self._save(sid, user[0], user[1], combined)
