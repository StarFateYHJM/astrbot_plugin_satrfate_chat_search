import sqlite3, os, time
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

@register("satrfate_chat_search", "Satrfate", "极简记忆插件：中文逐字分词·叙事性注入", "9.1.9")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        self.bot_id = config.get("bot_self_id", "") if config else ""
        self.debug = config.get("debug", False) if config else False
        self._pending = {}
        if self.debug:
            logger.info("[ChatSearch] 调试模式已开启")

    def _db(self, sid):
        return os.path.join(self.data_dir, sid.replace(':', '_') + ".db")

    def _save(self, sid, sender_id, name, text):
        if not text.strip():
            return
        db = self._db(sid)
        os.makedirs(os.path.dirname(db), exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT, sender_name TEXT, message_text TEXT, timestamp REAL)"
        )
        conn.execute(
            "INSERT INTO messages(sender_id, sender_name, message_text, timestamp) VALUES(?,?,?,?)",
            (sender_id, name, text, time.time())
        )
        conn.commit()
        conn.close()
        if self.debug:
            logger.info(f"[ChatSearch] 存储 [{name}]：{text[:40]}...")

    # ── 指令：测试检索 ──
    @filter.command("searchtest")
    async def cmd_search_test(self, event: AstrMessageEvent, message: str):
        sid = event.unified_msg_origin
        args = message.strip().split()

        if args and args[0] == '--all':
            keywords = args[1:]
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
            db_path = self._db(sid)
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

    # ── 钩子1：用户消息暂存 + 检索注入 ──
    @filter.on_llm_request(priority=1)
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        text = event.message_str.strip()
        if not text or text.startswith("/"):
            return
        if not event.is_private_chat() and f"[CQ:at,qq={self.bot_id}]" not in text:
            return

        uid = event.get_sender_id()
        name = event.get_sender_name()
        sid = f"FriendMessage:{uid}" if event.is_private_chat() else f"GroupMessage:{event.get_group_id()}"
        self._pending[sid] = {"user": (uid, name, text), "time": time.time()}

        if self.debug:
            logger.info(f"[ChatSearch] 暂存用户消息 [{name}]：{text[:40]}...")

        # 关键词提取：中文逐字拆分
        kw = [c for c in text if '\u4e00' <= c <= '\u9fff']

        if kw:
            db = self._db(sid)
            if os.path.exists(db):
                hist = self._search(db, kw)
                if hist:
                    if len(hist) > 100:
                        hist = hist[-100:]
                    req.system_prompt = (
                        f"## 【记忆回溯 - 共 {len(hist)} 条往事】\n"
                        f"{self._fmt(hist)}\n"
                        f"---\n"
                        f"上面是你脑海中浮现的往事。请继续用你的口气陪用户说话。\n"
                    ) + req.system_prompt
                    if self.debug:
                        logger.info(f"[ChatSearch] 注入 {len(hist)} 条历史记录")

    # ── 钩子2：AI 回复完整获取 → 合并写入 ──
    @filter.after_message_sent()
    async def on_after_sent(self, event: AstrMessageEvent):
        sid = f"FriendMessage:{event.get_sender_id()}" if event.is_private_chat() else f"GroupMessage:{event.get_group_id()}"
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

        combined = f"用户：{user[2]}\nAI回复：{ai_text}"
        self._save(sid, user[0], user[1], combined)

    # ── 检索与格式化 ──
    def _search(self, db, kw):
        conn = sqlite3.connect(db)
        c = conn.cursor()
        conds = [f"message_text LIKE '%{k}%'" for k in kw]
        sql = f"SELECT sender_name, message_text, timestamp FROM messages WHERE {' OR '.join(conds)} ORDER BY timestamp DESC"
        c.execute(sql)
        res = c.fetchall()
        conn.close()
        if self.debug:
            logger.info(f"[ChatSearch] 关键词检索：{kw}，命中 {len(res)} 条")
        return res

    def _fmt(self, hist):
        lines = []
        for r in reversed(hist):
            text = r[1]
            text = text.replace("用户：", "你说：")
            text = text.replace("AI回复：", "我回应：")
            text = text.replace("[assistant]", "我")
            text = text.replace(f"[{r[0]}]", "你")
            lines.append(text)
        return "\n\n".join(lines)
