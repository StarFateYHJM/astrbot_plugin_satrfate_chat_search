import sqlite3, os, time
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

# ============================================
# 完整停用词表（整合百度、哈工大、川大词库 + 大幅扩充）
# ============================================
STOP_WORDS = {
    '一一', '一下', '一些', '一切', '一则', '一天', '一定', '一方面', '一旦',
    '一时', '一来', '一样', '一次', '一片', '一直', '一致', '一般', '一起',
    '一边', '一面', '万一', '上下', '上升', '上去', '上来', '上述', '上面',
    '下列', '下去', '下来', '下面', '不一', '不久', '不仅', '不会', '不但',
    '不光', '不单', '不变', '不只', '不可', '不同', '不够', '不如', '不得',
    '不怕', '不惟', '不成', '不拘', '不敢', '不断', '不是', '不比', '不然',
    '不特', '不独', '不管', '不能', '不要', '不论', '不足', '不过', '不问',
    '与', '与其', '与否', '与此同时', '专门', '且', '两者', '严格', '严重',
    '个', '个人', '个别', '中小', '中间', '丰富', '临', '为', '为主', '为了',
    '为什么', '为什麽', '为何', '为着', '主张', '主要', '举行', '乃', '乃至',
    '么', '之', '之一', '之前', '之后', '之後', '之所以', '之类', '乌乎',
    '乎', '乘', '也', '也好', '也是', '也罢', '了', '了解', '争取', '于',
    '于是', '于是乎', '云云', '互相', '产生', '人们', '人家', '什么', '什么样',
    '什麽', '今后', '今天', '今年', '今後', '仍然', '从', '从事', '从而',
    '他', '他人', '他们', '他的', '代替', '以', '以上', '以下', '以为', '以便',
    '以免', '以前', '以及', '以后', '以外', '以後', '以来', '以至', '以至于',
    '以致', '们', '任', '任何', '任凭', '任务', '企图', '伟大', '似乎', '似的',
    '但', '但是', '何', '何况', '何处', '何时', '作为', '你', '你们', '你的',
    '使得', '使用', '例如', '依', '依照', '依靠', '促进', '保持', '俺', '俺们',
    '倘', '倘使', '倘或', '倘然', '倘若', '假使', '假如', '假若', '做到', '像',
    '允许', '充分', '先后', '先後', '先生', '全部', '全面', '兮', '共同', '关于',
    '其', '其一', '其中', '其二', '其他', '其余', '其它', '其实', '其次',
    '具体', '具体地说', '具体说来', '具有', '再者', '再说', '冒', '冲', '决定',
    '况且', '准备', '几', '几乎', '几时', '凭', '凭借', '出去', '出来', '出现',
    '分别', '则', '别', '别的', '别说', '到', '前后', '前者', '前进', '前面',
    '加之', '加以', '加入', '加强', '十分', '即', '即令', '即使', '即便', '即或',
    '即若', '却不', '原来', '又', '及', '及其', '及时', '及至', '双方', '反之',
    '反应', '反映', '反过来', '反过来说', '取得', '受到', '变成', '另', '另一方面',
    '另外', '只是', '只有', '只要', '只限', '叫', '叫做', '召开', '可', '可以',
    '可是', '可能', '可见', '各', '各个', '各人', '各位', '各地', '各种', '各级',
    '各自', '合理', '同', '同一', '同时', '同样', '后来', '后面', '向', '向着',
    '吓', '吗', '否则', '吧', '吱', '呀', '呃', '呕', '呗', '呜', '呜呼', '呢',
    '周围', '呵', '呸', '呼哧', '咋', '和', '咚', '咦', '咱', '咱们', '咳', '哇',
    '哈', '哈哈', '哉', '哎', '哎呀', '哎哟', '哗', '哟', '哦', '哩', '哪', '哪个',
    '哪些', '哪儿', '哪天', '哪年', '哪怕', '哪样', '哪边', '哪里', '哼', '哼唷',
    '唉', '啊', '啥', '啦', '啪达', '喂', '喏', '喔唷', '嗡嗡', '嗬', '嗯', '嗳',
    '嘎', '嘎登', '嘘', '嘛', '嘻', '嘿', '因', '因为', '因此', '因而', '固然',
    '在', '在下', '地', '坚决', '坚持', '基本', '处理', '复杂', '多', '多少',
    '多数', '多次', '大力', '大多数', '大大', '大家', '大批', '大约', '大量',
    '失去', '她', '她们', '她的', '好的', '好象', '如', '如上所述', '如下', '如何',
    '如其', '如果', '如此', '如若', '存在', '宁', '宁可', '宁愿', '宁肯', '它',
    '它们', '它们的', '它的', '安全', '完全', '完成', '实现', '实际', '宣布',
    '容易', '密切', '对', '对于', '对应', '将', '少数', '尔后', '尚且', '尤其',
    '就', '就是', '就是说', '尽', '尽管', '属于', '岂但', '左右', '巨大', '巩固',
    '己', '已经', '帮助', '常常', '并', '并不', '并不是', '并且', '并没有', '广大',
    '广泛', '应当', '应用', '应该', '开外', '开始', '开展', '引起', '强烈', '强调',
    '归', '当', '当前', '当时', '当然', '当着', '形成', '彻底', '彼', '彼此', '往',
    '往往', '待', '後来', '後面', '得', '得出', '得到', '心里', '必然', '必要',
    '必须', '怎', '怎么', '怎么办', '怎么样', '怎样', '怎麽', '总之', '总是',
    '总的来看', '总的来说', '总的说来', '总结', '总而言之', '恰恰相反', '您', '意思',
    '愿意', '慢说', '成为', '我', '我们', '我的', '或', '或是', '或者', '战斗',
    '所', '所以', '所有', '所谓', '打', '扩大', '把', '抑或', '拿', '按', '按照',
    '换句话说', '换言之', '据', '掌握', '接着', '接著', '故', '故此', '整个',
    '方便', '方面', '旁人', '无宁', '无法', '无论', '既', '既是', '既然', '时候',
    '明显', '明确', '是', '是否', '是的', '显然', '显著', '普通', '普遍', '更加',
    '曾经', '替', '最后', '最大', '最好', '最後', '最近', '最高', '有', '有些',
    '有关', '有利', '有力', '有所', '有效', '有时', '有点', '有的', '有着', '有著',
    '望', '朝', '朝着', '本', '本着', '来', '来着', '极了', '构成', '果然', '果真',
    '某', '某个', '某些', '根据', '根本', '欢迎', '正在', '正如', '正常', '此',
    '此外', '此时', '此间', '毋宁', '每', '每个', '每天', '每年', '每当', '比',
    '比如', '比方', '比较', '毫不', '没有', '沿', '沿着', '注意', '深入', '清楚',
    '满足', '漫说', '焉', '然则', '然后', '然後', '然而', '照', '照着', '特别是',
    '特殊', '特点', '现代', '现在', '甚么', '甚而', '甚至', '用', '由', '由于',
    '由此可见', '的', '的话', '目前', '直到', '直接', '相似', '相信', '相反',
    '相同', '相对', '相对而言', '相应', '相当', '相等', '省得', '看出', '看到',
    '看来', '看看', '看见', '真是', '真正', '着', '着呢', '矣', '知道', '确定',
    '离', '积极', '移动', '突出', '突然', '立即', '第', '等', '等等', '管',
    '紧接着', '纵', '纵令', '纵使', '纵然', '练习', '组成', '经', '经常', '经过',
    '结合', '结果', '给', '绝对', '继续', '继而', '维持', '综上所述', '罢了',
    '考虑', '者', '而', '而且', '而况', '而外', '而已', '而是', '而言', '联系',
    '能', '能否', '能够', '腾', '自', '自个儿', '自从', '自各儿', '自家', '自己',
    '自身', '至', '至于', '良好', '若', '若是', '若非', '范围', '莫若', '获得',
    '虽', '虽则', '虽然', '虽说', '行为', '行动', '表明', '表示', '被', '要',
    '要不', '要不是', '要不然', '要么', '要是', '要求', '规定', '觉得', '认为',
    '认真', '认识', '让', '许多', '论', '设使', '设若', '该', '说明', '诸位',
    '谁', '谁知', '赶', '起', '起来', '起见', '趁', '趁着', '越是', '跟', '转动',
    '转变', '转贴', '较', '较之', '边', '达到', '迅速', '过', '过去', '过来',
    '运用', '还是', '还有', '这', '这个', '这么', '这么些', '这么样', '这么点儿',
    '这些', '这会儿', '这儿', '这就是说', '这时', '这样', '这点', '这种', '这边',
    '这里', '这麽', '进入', '进步', '进而', '进行', '连', '连同', '适应', '适当',
    '适用', '逐步', '逐渐', '通常', '通过', '造成', '遇到', '遭到', '避免', '那',
    '那个', '那么', '那么些', '那么样', '那些', '那会儿', '那儿', '那时', '那样',
    '那边', '那里', '那麽', '部分', '鄙人', '采取', '里面', '重大', '重新', '重要',
    '鉴于', '问题', '防止', '阿', '附近', '限制', '除', '除了', '除此之外', '除非',
    '随', '随着', '随著', '集中', '需要', '非但', '非常', '非徒', '靠', '顺',
    '顺着', '首先', '高兴', '是不是', '说说',
    '转身', '回头', '低头', '抬头', '伸手', '迈开', '停下', '顿了', '沉默', '走了', '看去',
    '目光', '眼神', '声音', '晨风', '阳光', '光线', '石板', '屋檐', '窗外',
    '轻轻', '微微', '淡淡', '深深', '浅浅', '缓缓', '慢慢',
    '打开天窗说亮话', '到目前为止', '赶早不赶晚', '常言说得好', '何乐而不为', '毫无保留地',
    '貌似', '好像', '或许', '大概', '恐怕', '难道', '莫非', '兴许', '没准', '别是',
    '据说', '听说', '说是', '好似', '如同', '宛如', '仿佛',
    '说道', '问道', '答道', '笑道', '哭道', '喊道', '叫到', '想来', '望去',
    '过来', '过去', '起来', '下来', '上来', '进去', '出去', '回来', '回去',
    '开始', '继续', '进行', '出现', '消失', '发生', '结束', '停止', '完成',
    '微笑', '苦笑', '轻笑', '浅笑', '笑意', '笑容', '笑着', '笑着说',
    '眉头', '嘴角', '指尖', '掌心', '胸口', '背影', '发梢', '肩膀', '手臂',
    '只是', '还是', '就是', '真是', '的话', '一定', '好了', '行啦', '来了', '真是的',
    '确实', '应该', '需要', '可以', '如果', '不错', '当然', '可能', '或许',
    '毕竟', '反正', '显然', '居然', '竟然', '罢了', '而已', '算了', '得了', '也罢', '不成',
    '怎么样', '什么样', '怎么着', '这么着', '那么着', '怎么着点',
    '点点', '斑斑', '阵阵', '缕缕', '丝丝', '片片', '团团的',
    '砰然', '忽然', '突然', '骤然', '猛然', '陡然', '蓦地', '倏地',
    '不住', '不禁', '不觉', '不由得', '情不自禁',
}

@register("satrfate_chat_search", "Satrfate", "极简记忆插件·精准分词", "9.2.6")
class SatrfateChatSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.data_dir = os.path.join("data", "plugin_data", "astrbot_plugin_satrfate_chat_search")
        os.makedirs(self.data_dir, exist_ok=True)
        self.bot_id = config.get("bot_self_id", "") if config else ""
        self.debug = config.get("debug", False) if config else False
        self.max_inject = config.get("max_inject", 50) if config else 50
        self._pending = {}

        custom_stopwords = config.get("custom_stopwords", "") if config else ""
        if custom_stopwords:
            for w in custom_stopwords.strip().split("\n"):
                w = w.strip()
                if w and w not in STOP_WORDS:
                    STOP_WORDS.add(w)

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

    @filter.command("searchtest")
    async def cmd_search_test(self, event: AstrMessageEvent, message: str):
        uid = event.get_sender_id()
        sid = f"FriendMessage:{uid}" if event.is_private_chat() else f"GroupMessage:{event.get_group_id()}"
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

        # 先移除整句停用词，再对剩余文本做 bigram 拆分
        filtered_text = text
        for sw in sorted(STOP_WORDS, key=lambda x: len(x), reverse=True):
            filtered_text = filtered_text.replace(sw, "")
        kw = []
        for i in range(len(filtered_text) - 1):
            bigram = filtered_text[i:i+2]
            if '\u4e00' <= bigram[0] <= '\u9fff' and '\u4e00' <= bigram[1] <= '\u9fff':
                if bigram[0] in STOP_WORDS or bigram[1] in STOP_WORDS:
                    continue
                if bigram not in STOP_WORDS:
                    kw.append(bigram)
        kw = list(set(kw))

        if kw:
            db = self._db(sid)
            if os.path.exists(db):
                hist = self._search(db, kw)
                if hist:
                    if len(hist) > self.max_inject:
                        hist = hist[-self.max_inject:]
                    req.system_prompt = (
                        f"## 【记忆回溯 - 共 {len(hist)} 条往事】\n"
                        f"{self._fmt(hist)}\n"
                        f"---\n"
                        f"上面是你脑海中浮现的往事。请继续用你的口气陪用户说话。\n"
                    ) + req.system_prompt
                    if self.debug:
                        logger.info(f"[ChatSearch] 注入 {len(hist)} 条历史记录")

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

    def _search(self, db, kw):
        conn = sqlite3.connect(db)
        c = conn.cursor()
        conds = [f"message_text LIKE '%{k}%'" for k in kw]
        sql = f"""
            SELECT sender_name, message_text, timestamp 
            FROM messages 
            WHERE {' OR '.join(conds)} 
            GROUP BY id 
            HAVING COUNT(*) >= 2 
            ORDER BY timestamp DESC
        """
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
            text = text.replace("用户：", "你说：").replace("AI回复：", "我回应：")
            text = text.replace("[assistant]", "我").replace(f"[{r[0]}]", "你")
            lines.append(text)
        return "\n\n".join(lines)
