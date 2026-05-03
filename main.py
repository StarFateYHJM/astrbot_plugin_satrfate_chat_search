import sqlite3
import os
import time
import asyncio
import json
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

STOP_WORDS = {
    '你', '我', '他', '她', '它', '们', '的', '了', '是', '在', '有', '和', '不', '这', '那',
    '吗', '呢', '吧', '啊', '哦', '嗯', '还', '就', '都', '也', '要', '会', '能', '去', '来',
    '说', '看', '想', '知道', '记得', '告诉', '觉得', '可以', '应该', '怎么', '什么', '哪',
    '一个', '这个', '那个', '这样', '那样', '真的', '好', '很', '有点', '没有'
}

@register("satrfate_chat_search", "you", "极简记忆插件：拦截排队+多槽拼接+会话锁+去重", "5.3.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)

        self.config = config or {}
        self.debug = self.config.get("debug", False)
        self.napcat_ws = self.config.get("napcat_ws", "ws://127.0.0.1:3688?access_token=my_secure_token_123")
        self.bot_self_id = self.config.get("bot_self_id", "")

        self._session_locks = {}
        self._pending_user_msgs = {}
        self._reply_slots = {}
        self._reply_counter = {}
        self._last_msg = {}
        self._awake_flags = {}

        if self.debug:
            logger.info(f"[ChatSearch] 调试模式开启，WS: {self.napcat_ws}")

        if self.bot_self_id:
            asyncio.create_task(self._napcat_ws_monitor())
        else:
            logger.warning("[ChatSearch] 未配置 bot_self_id，消息监听未启动")

    def _get_db_path(self, session_id: str) -> str:
        safe_name = session_id.replace(':', '_').replace('\\', '_').replace('/', '_')
        return os.path.join(self.data_dir, f"{safe_name}.db")

    def _init_db(self, db_path: str):
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
        c.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp DESC)")
        conn.commit()
        conn.close()

    def _save_message(self, session_id: str, sender_id: str, sender_name: str, message_text: str):
        if not message_text.strip():
            return
        db_path = self._get_db_path(session_id)
        self._init_db(db_path)
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute(
                "INSERT INTO messages (sender_id, sender_name, message_text, timestamp) VALUES (?, ?, ?, ?)",
                (sender_id, sender_name, message_text, time.time())
            )
            conn.commit()
            conn.close()
            if self.debug:
                logger.info(f"[ChatSearch] 存储 [{sender_name}]：{message_text[:40]}...")
        except Exception as e:
            logger.error(f"[ChatSearch] 写入失败：{e}")

    async def _handle_user_message(self, session_id, user_id, sender_name, raw_text, from_queue=False):
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        lock = self._session_locks[session_id]

        if lock.locked() and not from_queue:
            self._pending_user_msgs[session_id] = (session_id, user_id, sender_name, raw_text)
            self._create_new_slot(session_id)
            if self.debug:
                logger.info(f"[ChatSearch] 会话 {session_id} 繁忙，已冻结跟进消息")
            return
        elif lock.locked() and from_queue:
            # 排队消息已被唤醒，正常处理
            pass

        await lock.acquire()
        self._save_message(session_id, user_id, sender_name, raw_text)
        self._create_new_slot(session_id)
        if self.debug:
            logger.info(f"[ChatSearch] 会话 {session_id} 开始新槽")

    def _create_new_slot(self, session_id):
        now = time.time()
        if session_id not in self._reply_slots:
            self._reply_slots[session_id] = []
        slots = self._reply_slots[session_id]
        if slots:
            slots[-1]['end_time'] = now
        if session_id not in self._reply_counter:
            self._reply_counter[session_id] = 0
        self._reply_counter[session_id] += 1
        new_id = self._reply_counter[session_id]
        new_slot = {
            'reply_id': new_id,
            'start_time': now,
            'end_time': float('inf'),
            'parts': [],
            'timer': None,
            'session_id': session_id
        }
        slots.append(new_slot)

    async def _release_lock(self, session_id, event=None):
        if session_id in self._session_locks:
            lock = self._session_locks[session_id]
            if lock.locked():
                lock.release()
                if self.debug:
                    logger.info(f"[ChatSearch] 会话 {session_id} 已解锁")

        if session_id in self._pending_user_msgs:
            pending = self._pending_user_msgs.pop(session_id)
            # 标记为唤醒消息，避免再次拦截
            await self._handle_user_message(*pending, from_queue=True)
            # 发送静默唤醒消息
            try:
                platform = event.get_platform() if event else None
                if platform and platform != 'mock':
                    await self.context.send_message(pending[0], ".", is_trigger=False)
            except Exception:
                pass

    @filter.on_llm_request(priority=-999)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        current_text = event.message_str.strip()
        if not current_text:
            return
        user_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        is_private = event.is_private_chat()
        if not is_private and f"[CQ:at,qq={self.bot_self_id}]" not in current_text:
            return
        if current_text.startswith("/"):
            return
        if is_private:
            session_id = f"FriendMessage:{user_id}"
        else:
            session_id = f"GroupMessage:{event.get_group_id()}"

        # 去重
        last_text = self._last_msg.get(session_id, "")
        if current_text == last_text:
            if self.debug:
                logger.info(f"[ChatSearch] 重复消息已忽略: {current_text[:30]}")
            return
        self._last_msg[session_id] = current_text

        # 检查锁，拦截跟进消息
        lock = self._session_locks.get(session_id)
        if lock and lock.locked():
            self._pending_user_msgs[session_id] = (session_id, user_id, sender_name, current_text)
            self._create_new_slot(session_id)
            if req.contexts and req.contexts[-1].get('role') == 'user':
                req.contexts.pop(-1)
            if self.debug:
                logger.info(f"[ChatSearch] 会话 {session_id} 已冻结跟进消息")
            return

        await self._handle_user_message(session_id, user_id, sender_name, current_text, from_queue=False)

        keywords = [w for w in current_text if len(w) >= 2 and w not in STOP_WORDS]
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
                logger.info(f"[ChatSearch] 为会话注入 {len(history)} 条历史记录")

    async def _napcat_ws_monitor(self):
        from websockets import connect
        ws_url = self.napcat_ws
        logger.info(f"[ChatSearch] NapCat 监听启动：{ws_url}")
        while True:
            try:
                async with connect(ws_url) as ws:
                    logger.info("[ChatSearch] ✅ 已连接 NapCat WebSocket")
                    async for data in ws:
                        try:
                            event = json.loads(data)
                            if event.get("post_type") != "message_sent":
                                continue
                            message_type = event.get("message_type")
                            raw_text = event.get("raw_message", "").strip()
                            if not raw_text:
                                continue
                            sender_id = str(event.get("user_id"))
                            ts = event.get("time", time.time())

                            if message_type == "private":
                                target_id = str(event.get("target_id"))
                                session_id = f"FriendMessage:{target_id}"
                            elif message_type == "group":
                                group_id = str(event.get("group_id"))
                                session_id = f"GroupMessage:{group_id}"
                                if "[CQ:at,qq=" not in raw_text:
                                    continue
                            else:
                                continue

                            slots = self._reply_slots.get(session_id, [])
                            matched_slot = None
                            for slot in slots:
                                if slot['start_time'] <= ts <= slot['end_time']:
                                    matched_slot = slot
                                    break
                            if matched_slot is None and slots:
                                matched_slot = slots[-1]

                            if matched_slot is None:
                                continue

                            if matched_slot['timer'] and not matched_slot['timer'].done():
                                matched_slot['timer'].cancel()
                            matched_slot['parts'].append(raw_text)
                            matched_slot['timer'] = asyncio.create_task(
                                self._flush_slot(matched_slot, sender_id, event)
                            )

                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logger.warning(f"[ChatSearch] 处理事件异常：{e}")

            except Exception as e:
                logger.error(f"[ChatSearch] 连接断开，5秒后重试：{e}")
                await asyncio.sleep(5)

    async def _flush_slot(self, slot: dict, sender_id: str, event=None):
        await asyncio.sleep(4.0)
        if slot['parts']:
            full_text = "".join(slot['parts'])
            self._save_message(slot['session_id'], sender_id, "assistant", full_text)
            if self.debug:
                logger.info(f"[ChatSearch] 拼接写入 AI 回复 (reply_id={slot['reply_id']}, {len(full_text)} 字)")
        session_id = slot['session_id']
        if session_id in self._reply_slots:
            slots = self._reply_slots[session_id]
            if slot in slots:
                slots.remove(slot)
        await self._release_lock(session_id, event)

    def _search_history(self, db_path: str, keywords: list, limit: int = 10) -> list:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        conditions = []
        params = []
        for kw in keywords:
            conditions.append("message_text LIKE ?")
            params.append(f"%{kw}%")
        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""SELECT sender_name, message_text, timestamp 
                  FROM messages 
                  WHERE {where} 
                  AND NOT (sender_name = 'assistant' AND message_text LIKE '%🔍 检索%')
                  ORDER BY timestamp DESC LIMIT ?"""
        params.append(limit)
        c.execute(sql, params)
        results = c.fetchall()
        conn.close()
        return results

    def _format_history(self, history: list) -> str:
        lines = []
        for sender_name, msg_text, ts in reversed(history):
            lines.append(f"- [{sender_name}]: {msg_text}")
        return "\n".join(lines)

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
                    for sender_name, msg_text, ts in self._search_history(db_path, keywords):
                        all_results.append((f"[{sid[:30]}...] {sender_name}", msg_text, ts))
            all_results.sort(key=lambda x: x[2])
            history = all_results[:30]
            scope = f"全局（{len(all_results)} 条）"
        else:
            keywords = args if args else message.strip().split()
            db_path = self._get_db_path(session_id)
            if not os.path.exists(db_path):
                yield event.plain_result("🔍 当前会话还没有任何聊天记录。")
                return
            history = self._search_history(db_path, keywords)
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

    async def terminate(self):
        if self.debug:
            logger.info("[ChatSearch] 插件已卸载")
