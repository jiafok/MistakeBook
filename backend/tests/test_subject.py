"""Regression checks for automatic subject detection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.subject import detect_subject, detect_subject_with_confidence

SAMPLES = {
    "化学": "一、选择题 构成氯化钠的微观粒子 原子 分子 离子 元素 化学方程式 催化剂 溶液",
    "物理": "匀速直线运动 加速度 弹簧测力计 电流 电压 电阻 串联 并联 密度 浮力 压强",
    "数学": "解方程 因式分解 二次函数 三角形全等 勾股定理 相似三角形 求证 不等式",
    "语文": "下列词语中加点字读音 病句修改 古诗文默写 文言文阅读 翻译句子 作文 记叙文",
    "英语": "Choose the best answer. Reading Comprehension. Fill in the blanks. grammar",
    "生物": "细胞 细胞膜 叶绿体 线粒体 光合作用 呼吸作用 遗传 变异 食物链 生态系统",
    "历史": "鸦片战争 辛亥革命 五四运动 唐朝 宋朝 文艺复兴 工业革命 条约 变法",
    "地理": "等高线地形图 季风气候 经纬度 板块运动 地球自转 温带 热带 洋流 山脉",
    "政治": "宪法 法治 权利 义务 国家机构 人民代表大会 依法治国 公民 未成年人保护法",
}


def test_each_subject_is_detected() -> None:
    for expected, sample in SAMPLES.items():
        got = detect_subject(sample)
        assert got == expected, f"expected {expected}, got {got!r} for sample"


def test_empty_or_tiny_text_is_not_guessed() -> None:
    assert detect_subject("") == ""
    assert detect_subject("   ") == ""
    assert detect_subject("1. 2. 3.") == ""


def test_ambiguous_text_returns_empty() -> None:
    # One weak hit in two different subjects: no clear winner, so stay silent.
    assert detect_subject("溶液 速度 质量 力") == ""


def test_confidence_is_a_fraction() -> None:
    subject, confidence = detect_subject_with_confidence(SAMPLES["化学"])
    assert subject == "化学"
    assert 0.0 < confidence <= 1.0


def test_math_text_with_numbers_is_not_chemistry() -> None:
    # Arithmetic inside a chemistry-looking page must not flip the result.
    text = "计算下列各题 12 x 8 = 96 45 + 27 = 72 三元一次方程组 因式分解 二次函数"
    assert detect_subject(text) == "数学"


if __name__ == "__main__":
    test_each_subject_is_detected()
    print("ok  all nine subjects detected")
    test_empty_or_tiny_text_is_not_guessed()
    print("ok  empty/short text is not guessed")
    test_ambiguous_text_returns_empty()
    print("ok  ambiguous text returns empty")
    test_confidence_is_a_fraction()
    print("ok  confidence is a fraction")
    test_math_text_with_numbers_is_not_chemistry()
    print("ok  arithmetic does not trigger chemistry")
    print("ALL SUBJECT CHECKS PASSED")
