"""Subject detection for a paper, based on its recognised text.

The classifier is deliberately simple and inspectable: weighted keyword evidence
per subject, normalised by text length. It returns the best subject only when the
evidence is clear; otherwise it returns "" so the caller can keep whatever the user
picked. Detection never overrides an explicit user choice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Subject labels must match the ones the frontend offers, so the value can be
# stored directly on Paper.subject without translation.
SUBJECTS = ("数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "政治")


@dataclass(frozen=True)
class _Signal:
    """Keywords for one subject.

    `strong` words are near-unique to the subject and count 3 points; `weak` words
    are suggestive and count 1. Chinese has no word boundaries, so keywords are
    matched as substrings, which is exactly what we want here.
    """

    strong: tuple[str, ...]
    weak: tuple[str, ...]


SIGNALS: dict[str, _Signal] = {
    "化学": _Signal(
        strong=(
            "化学", "分子", "原子", "离子", "元素", "化合价", "化学式", "化学方程式",
            "物质的量", "摩尔", "氧化还原", "酸碱盐", "溶液的pH", "催化剂", "沉淀",
            "氢气", "氧气", "二氧化碳", "碳酸钙", "氯化钠", "硫酸", "氢氧化钠",
            "混合物", "纯净物", "化合反应", "分解反应", "置换反应", "复分解",
        ),
        weak=("溶液", "气体", "反应", "实验", "微观", "宏观", "质量守恒", "相对原子质量"),
    ),
    "物理": _Signal(
        strong=(
            "物理", "牛顿", "加速度", "重力", "摩擦力", "压强", "浮力", "电流", "电压",
            "电阻", "欧姆", "电磁", "磁场", "波长", "频率", "透镜", "折射", "反射",
            "机械能", "动能", "势能", "功率", "密度", "比热容", "串联", "并联",
        ),
        weak=("速度", "质量", "力", "能量", "运动", "匀速", "弹簧测力计"),
    ),
    "数学": _Signal(
        strong=(
            "数学", "方程", "函数", "不等式", "几何", "三角形", "四边形", "平行线",
            "全等", "相似", "勾股定理", "一次函数", "二次函数", "反比例函数", "因式分解",
            "多项式", "分式", "根式", "概率", "统计", "数列", "导数", "集合", "向量",
            "正弦", "余弦", "正切", "锐角三角函数", "圆心角", "半径", "直径",
        ),
        weak=("计算", "求值", "化简", "解方程", "证明", "坐标", "面积", "周长", "绝对值"),
    ),
    "语文": _Signal(
        strong=(
            "语文", "下列词语", "字音", "字形", "成语", "病句", "修辞", "古诗文", "默写",
            "文言文", "文言", "实词", "虚词", "翻译句子", "阅读理解", "作文", "记叙文",
            "说明文", "议论文", "拼音", "注音", "标点符号",
        ),
        weak=("作者", "诗句", "段落", "中心思想", "概括", "描写", "抒情", "词语"),
    ),
    "英语": _Signal(
        strong=(
            "英语", "English", "vocabulary", "grammar", "composition",
            "Choose the best answer", "Fill in the blanks", "Reading Comprehension",
            "完形填空", "单项选择", "书面表达", "句型转换", "单词拼写",
        ),
        weak=("passage", "sentence", "phrase", "tense", "verb", "noun"),
    ),
    "生物": _Signal(
        strong=(
            "生物", "细胞", "细胞膜", "叶绿体", "线粒体", "DNA", "基因", "染色体",
            "光合作用", "呼吸作用", "遗传", "变异", "生态", "食物链", "种群", "群落",
            "组织", "器官", "系统", "神经系统", "内分泌", "消化", "循环",
        ),
        weak=("实验", "观察", "显微镜", "生命", "生长", "繁殖"),
    ),
    "历史": _Signal(
        strong=(
            "历史", "朝代", "秦朝", "汉朝", "唐朝", "宋朝", "明朝", "清朝", "辛亥革命",
            "鸦片战争", "抗日战争", "五四运动", "文艺复兴", "工业革命", "古代史", "近代史",
            "史料", "条约", "革命", "王朝", "皇帝", "变法",
        ),
        weak=("年代", "世纪", "战争", "改革", "制度"),
    ),
    "地理": _Signal(
        strong=(
            "地理", "经纬度", "经线", "纬线", "等高线", "季风", "气候", "地形", "山脉",
            "河流", "盆地", "高原", "平原", "洋流", "板块", "地球自转", "地球公转",
            "热带", "温带", "寒带", "人口密度", "城市化",
        ),
        weak=("地图", "降水", "气温", "区域", "资源", "环境"),
    ),
    "政治": _Signal(
        strong=(
            "政治", "道德与法治", "宪法", "法治", "权利", "义务", "国家机构",
            "人民代表大会", "社会主义核心价值观", "未成年人保护法", "消费者权益",
            "民族团结", "依法治国", "公民",
        ),
        weak=("法律", "制度", "社会", "责任", "公正", "诚信"),
    ),
}

# A subject must lead by this ratio over the runner-up to be accepted.
_MIN_LEAD = 1.25
# Below this weight the text simply does not contain enough evidence.
_MIN_SCORE = 6.0
# Texts shorter than this are too small to classify meaningfully.
_MIN_TEXT_LEN = 20


def _score(text: str, signal: _Signal) -> float:
    score = 0.0
    for keyword in signal.strong:
        if keyword in text:
            score += 3.0
    for keyword in signal.weak:
        if keyword in text:
            score += 1.0
    return score


def detect_subject(text: str) -> str:
    """Return the most likely subject for `text`, or "" when unclear."""
    if not text or len(text.strip()) < _MIN_TEXT_LEN:
        return ""

    # English keywords are matched case-insensitively; Chinese is unaffected.
    normalised = text + "\n" + text.lower()

    scores = {subject: _score(normalised, signal) for subject, signal in SIGNALS.items()}
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    best_subject, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_score < _MIN_SCORE:
        return ""
    # Require a clear lead, otherwise the text is ambiguous and the user's own
    # choice (or the default) is more trustworthy than a coin flip.
    if best_score < runner_up * _MIN_LEAD:
        return ""
    return best_subject


def detect_subject_with_confidence(text: str) -> tuple[str, float]:
    """Same as detect_subject but also returns a 0-1 confidence for the UI."""
    subject = detect_subject(text)
    if not subject:
        return "", 0.0
    normalised = text + "\n" + text.lower()
    scores = {name: _score(normalised, signal) for name, signal in SIGNALS.items()}
    total = sum(scores.values())
    if total <= 0:
        return subject, 0.0
    return subject, round(scores[subject] / total, 3)
