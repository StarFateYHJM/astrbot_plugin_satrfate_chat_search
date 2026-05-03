import sqlite3, os, time, asyncio, json, hashlib
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api import logger

@register("satrfate_chat_search", "you", "纯信号驱动·流式拼接记忆插件", "7.0.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        self.napcat_ws = config.get("napcat_ws", "ws://127.0.0.1:3688?access_token=my_token") if config else ""
        self.bot_id = config.get("bot_self_id", "") if config else ""
        
        # 键：{target_id: {"parts": [], "user": (uid, name, text), "session": sid}}
        self._buffers = {}
        # 指纹去重字典：{hash: expire_time}
        self._dedup = {}
        
        if self.bot_id:
            asyncio.create_task(self._ws_loop())

    def _db(self, sid):
        return os.path.join(self.data_dir, sid.replace(':', '_') + ".db")

    def _save(self, sid, sender_id, name, text):
        """统一的保存入口，包含暴力去重"""
        if not text.strip():
            return

        # 对 AI 回复进行 SHA256 指纹去重（30 秒窗口）
        if name == "assistant":
            fp = hashlib.sha256(text.encode()).hexdigest()
            now = time.time()
            if fp in self._dedup and now < self._dedup[fp]:
                logger.info(f"[ChatSearch] 重复AI回复已跳过")
                return
            self._dedup[fp] = now + 30

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

    # ==================== 钩子1：用户消息暂存 ====================
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
        tid = str(uid) if event.is_private_chat() else str(event.get_group_id())

        # 创建或重置缓冲区（新请求会清空旧分片）
        self._buffers[tid] = {
            "parts": [],
            "user": (uid, name, text),
            "session": sid
        }

        # 注入历史上下文
        kw = [w for w in text.split() if len(w) >= 2]
        if kw:
            db = self._db(sid)
            if os.path.exists(db):
                hist = self._search(db, kw)
                if hist:
                    req.system_prompt = f"## 历史记录\n{self._fmt(hist)}\n---\n" + req.system_prompt

    # ==================== 钩子2：LLM 结束信号 → 唯一写入入口 ====================
    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        sid = f"FriendMessage:{event.get_sender_id()}" if event.is_private_chat() else f"GroupMessage:{event.get_group_id()}"
        tid = str(event.get_sender_id()) if event.is_private_chat() else str(event.get_group_id())
        
        buf = self._buffers.pop(tid, None)
        if not buf or not buf["parts"]:
            return

        user = buf["user"]
        full_ai = "".join(buf["parts"])
        if user and full_ai:
            combined = f"[{user[1]}]：{user[2]}\n[assistant]：{full_ai}"
            self._save(buf["session"], user[0], user[1], combined)
            logger.info(f"[ChatSearch] ✅ 通过LLM信号写入: {tid}")

    # ==================== WebSocket：纯分片收集 ====================
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

                        # 如果没有对应的缓冲区（已写入或未开始），直接丢弃分片
                        if tid not in self._buffers:
                            continue

                        self._buffers[tid]["parts"].append(raw)
            except Exception as e:
                logger.error(f"WS disconnect: {e}")
                await asyncio.sleep(5)

    # ==================== 检索逻辑（不变） ====================
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
