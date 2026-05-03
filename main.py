import sqlite3, os, time, asyncio, json
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api import logger

@register("satrfate_chat_search", "you", "双触发流式拼接记忆插件", "6.2.2")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        self.napcat_ws = config.get("napcat_ws", "ws://127.0.0.1:3688?access_token=my_token") if config else ""
        self.bot_id = config.get("bot_self_id", "") if config else ""
        self.pending = {}
        self._session_tid = {}  # 映射 session_id → target_id
        if self.bot_id:
            asyncio.create_task(self._ws_loop())

    def _db(self, sid):
        return os.path.join(self.data_dir, sid.replace(':', '_') + ".db")

    def _save(self, sid, sender_id, name, text):
        if not text.strip():
            return
        db = self._db(sid)
        os.makedirs(os.path.dirname(db), exist_ok=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id TEXT, sender_name TEXT, message_text TEXT, timestamp REAL)")
        conn.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (None, sender_id, name, text, time.time()))
        conn.commit()
        conn.close()

    # ============ 钩子1：LLM 结束时触发写入 ============
    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        sid = f"FriendMessage:{event.get_sender_id()}" if event.is_private_chat() else f"GroupMessage:{event.get_group_id()}"
        tid = self._session_tid.pop(sid, None)  # 取出并删除映射
        if tid and tid in self.pending and self.pending[tid]["parts"]:
            if self.pending[tid]["timer"] and not self.pending[tid]["timer"].done():
                self.pending[tid]["timer"].cancel()
            await self._flush_now(tid, self.bot_id)
            logger.info(f"[ChatSearch] 通过LLM信号写入: {tid}")

    # ============ 钩子2：用户消息暂存 ============
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
        # 修复：私聊中用 sender_id 作为 target_id
        tid = str(event.get_sender_id()) if event.is_private_chat() else str(event.get_group_id())

        slot = self.pending.setdefault(tid, {"user": None, "parts": [], "timer": None, "sess": sid})
        slot["user"] = (uid, name, text)
        slot["sess"] = sid
        self._session_tid[sid] = tid  # 存储映射

        kw = [w for w in text.split() if len(w) >= 2]
        if kw:
            db = self._db(sid)
            if os.path.exists(db):
                hist = self._search(db, kw)
                if hist:
                    req.system_prompt = f"## 历史记录\n{self._fmt(hist)}\n---\n" + req.system_prompt

    # ============ WebSocket 监听 ============
    async def _ws_loop(self):
        from websockets import connect
        while True:
            try:
                async with connect(self.napcat_ws) as ws:
                    async for data in ws:
                        ev = json.loads(data)
                        if ev.get("post_type") != "message_sent" or ev.get("message_type") != "private":
                            continue
                        raw = ev["raw_message"].strip()
                        if not raw:
                            continue
                        tid = str(ev["target_id"])
                        sid = f"FriendMessage:{tid}"
                        slot = self.pending.setdefault(tid, {"user": None, "parts": [], "timer": None, "sess": sid})
                        slot["parts"].append(raw)
                        if slot["timer"] and not slot["timer"].done():
                            slot["timer"].cancel()
                        slot["timer"] = asyncio.create_task(self._flush(tid, ev["user_id"]))
            except Exception as e:
                logger.error(f"WS disconnect: {e}")
                await asyncio.sleep(5)

    # ============ 两套写入逻辑 ============
    async def _flush_now(self, tid, sender):
        slot = self.pending.pop(tid, None)
        if not slot:
            return
        ai = "".join(slot["parts"])
        usr = slot["user"]
        if usr and ai:
            combined = f"[{usr[1]}]：{usr[2]}\n[assistant]：{ai}"
            self._save(slot["sess"], usr[0], usr[1], combined)
        elif usr:
            self._save(slot["sess"], usr[0], usr[1], usr[2])
        elif ai:
            self._save(slot["sess"], sender, "assistant", ai)

    async def _flush(self, tid, sender):
        await asyncio.sleep(4.0)
        slot = self.pending.pop(tid, None)
        if not slot:
            return
        ai = "".join(slot["parts"])
        usr = slot["user"]
        if usr and ai:
            combined = f"[{usr[1]}]：{usr[2]}\n[assistant]：{ai}"
            self._save(slot["sess"], usr[0], usr[1], combined)
        elif usr:
            self._save(slot["sess"], usr[0], usr[1], usr[2])
        elif ai:
            self._save(slot["sess"], sender, "assistant", ai)

    # ============ 检索 ============
    def _search(self, db, kw):
        conn = sqlite3.connect(db)
        c = conn.cursor()
        conds = [f"message_text LIKE '%{k}%'" for k in kw]
        sql = f"SELECT sender_name, message_text, timestamp FROM messages WHERE {' AND '.join(conds)} ORDER BY timestamp DESC LIMIT 10"
        c.execute(sql)
        res = c.fetchall()
        conn.close()
        return res

    def _fmt(self, hist):
        return "\n".join(f"- [{r[0]}]: {r[1]}" for r in reversed(hist))
