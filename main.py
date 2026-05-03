import sqlite3
import os
import time
import asyncio
import json
import re
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

# 停用词表（不参与关键词提取）
STOP_WORDS = {
    '你', '我', '他', '她', '它', '们', '的', '了', '是', '在', '有', '和', '不', '这', '那',
    '吗', '呢', '吧', '啊', '哦', '嗯', '还', '就', '都', '也', '要', '会', '能', '去', '来',
    '说', '看', '想', '知道', '记得', '告诉', '觉得', '可以', '应该', '怎么', '什么', '哪',
    '一个', '这个', '那个', '这样', '那样', '真的', '好', '很', '有点', '没有'
}

@register("satrfate_chat_search", "you", "极简记忆插件：NapCat监听+关键词检索注入", "5.0.0")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)

        self.config = config or {}
        self.debug = self.config.get("debug", False)
        self.napcat_debug_ws = self.config.get("napcat_debug_ws", "ws://127.0.0.1:8998")
        self.bot_self_id = self.config.get("bot_self_id", "")

        if self.debug:
            logger.info(f"[ChatSearch] 调试模式开启，监听地址：{self.napcat_debug_ws}，机器人QQ：{self.bot_self_id}")

        # 启动NapCat调试监听任务
        if self.bot_self_id:
            asyncio.create_task(self._napcat_debug_monitor())
        else:
            logger.warning("[ChatSearch] 未配置 bot_self_id，消息监听未启动")

    # ==================== 数据库工具方法 ====================
    def _get_db_path(self, session_id: str) -> str:
        """将会话ID转换为数据库文件路径"""
        safe_name = session_id.replace(':', '_').replace('\\', '_').replace('/', '_')
        return os.path.join(self.data_dir, f"{safe_name}.db")

    def _init_db(self, db_path: str):
        """初始化数据库表"""
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
        """写入消息到数据库"""
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

    # ==================== NapCat 调试监听 ====================
    async def _napcat_debug_monitor(self):
        """连接 NapCat 调试服务，接收完整消息事件并写入数据库"""
        from websockets import connect
        bot_id = self.bot_self_id
        ws_url = self.napcat_debug_ws

        logger.info(f"[ChatSearch] 启动NapCat监听：{ws_url}")
        while True:
            try:
                async with connect(ws_url) as ws:
                    logger.info("[ChatSearch] ✅ 已连接 NapCat 调试服务")
                    async for data in ws:
                        try:
                            event = json.loads(data)
                            post_type = event.get("post_type")

                            # ---------- 用户消息 ----------
                            if post_type == "message":
                                msg_type = event.get("message_type")
                                raw_text = event.get("raw_message", "").strip()
                                if not raw_text:
                                    continue
                                # 忽略指令消息
                                if raw_text.startswith("/"):
                                    continue

                                # 私聊：直接存储
                                if msg_type == "private":
                                    user_id = str(event.get("user_id"))
                                    sender_name = event.get("sender", {}).get("nickname", "User")
                                    self._save_message(
                                        f"FriendMessage:{user_id}", user_id, sender_name, raw_text
                                    )

                                # 群聊：只存储@机器人的消息
                                elif msg_type == "group":
                                    # 精准匹配：只当消息中包含 @机器人QQ号 时才记录
                                    if f"[CQ:at,qq={bot_id}]" not in raw_text:
                                        continue
                                    user_id = str(event.get("user_id"))
                                    group_id = str(event.get("group_id"))
                                    sender_name = event.get("sender", {}).get("nickname", "User")
                                    self._save_message(
                                        f"GroupMessage:{group_id}", user_id, sender_name, raw_text
                                    )

                            # ---------- AI 回复 ----------
                            elif post_type == "message_sent":
                                msg_type = event.get("message_type")
                                raw_text = event.get("raw_message", "").strip()
                                if not raw_text:
                                    continue

                                # 私聊回复：全部记录
                                if msg_type == "private":
                                    target_id = str(event.get("target_id"))
                                    sender_id = str(event.get("user_id"))
                                    self._save_message(
                                        f"FriendMessage:{target_id}", sender_id, "assistant", raw_text
                                    )

                                # 群聊回复：只记录包含 @某用户 的回复（过滤自动欢迎等）
                                elif msg_type == "group":
                                    if "[CQ:at,qq=" not in raw_text:
                                        continue
                                    group_id = str(event.get("group_id"))
                                    sender_id = str(event.get("user_id"))
                                    self._save_message(
                                        f"GroupMessage:{group_id}", sender_id, "assistant", raw_text
                                    )

                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logger.warning(f"[ChatSearch] 处理事件异常：{e}")

            except Exception as e:
                logger.error(f"[ChatSearch] 连接断开，5秒后重试：{e}")
                await asyncio.sleep(5)

    # ==================== 指令：测试检索 ====================
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

    # ==================== 检索与注入 ====================
    @filter.on_llm_request(priority=1)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        session_id = event.unified_msg_origin
        current_text = event.message_str
        if not current_text:
            return

        # 提取关键词（简单空格分词）
        keywords = [w for w in current_text.split() if len(w) >= 2 and w not in STOP_WORDS]
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

    def _search_history(self, db_path: str, keywords: list, limit: int = 10) -> list:
        """在数据库中执行 LIKE 全文检索"""
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
        """格式化为 LLM 友好的文本"""
        lines = []
        for sender_name, msg_text, ts in reversed(history):
            lines.append(f"- [{sender_name}]: {msg_text}")
        return "\n".join(lines)

    async def terminate(self):
        if self.debug:
            logger.info("[ChatSearch] 插件已卸载")
