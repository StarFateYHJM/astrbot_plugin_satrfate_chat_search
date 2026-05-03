import sqlite3, os, time
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

@register("satrfate_chat_search", "you", "极简记忆插件：叙事性注入+第二人称记录+debug", "9.1.3")
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

        kw = [w for w in text.split() if len(w) >= 2]
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
                        f"上面是你脑海中浮现的往事。请继续用坩埚的口气陪主人说话。\n"
                    ) + req.system_prompt
                    if self.debug:
                        logger.info(f"[ChatSearch] 注入 {len(hist)} 条历史记录")
        else:
            if self.debug:
                logger.info("[ChatSearch] 未提取到有效关键词，跳过注入")

    @filter.after_message_sent()
    async def on_after_sent(self, event: AstrMessageEvent):
        sid = f"FriendMessage:{event.get_sender_id()}" if event.is_private_chat() else f"GroupMessage:{event.get_group_id()}"
        pending = self._pending.pop(sid, None)
        if not pending:
            if self.debug:
                logger.info("[ChatSearch] 无暂存用户消息，跳过存储")
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

        combined = f"你问：{user[2]}\n坩埚回答：{ai_text}"
        self._save(sid, user[0], user[1], combined)
        if self.debug:
            logger.info(f"[ChatSearch] 合并写入：{combined[:80]}...")

    def _search(self, db, kw):
        conn = sqlite3.connect(db)
        c = conn.cursor()
        conds = [f"message_text LIKE '%{k}%'" for k in kw]
        sql = f"SELECT sender_name, message_text, timestamp FROM messages WHERE {' AND '.join(conds)} ORDER BY timestamp DESC"
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
            text = text.replace("你问：", "这时，你问：")
            text = text.replace("坩埚回答：", "坩埚听了，说：")
            text = text.replace("[assistant]", "坩埚听了，说")
            text = text.replace(f"[{r[0]}]", "这时，你说")
            lines.append(text)
        return "\n\n".join(lines)
