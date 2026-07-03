"""
============================================================
CLI 版本：chaoxing_post.py
功能：学习通答案提交（命令行交互版）
用法：python chaoxing_post.py
      或修改 CONFIG 后直接运行
============================================================
"""

import json
import os
import sys

from core import detect_answer_type, parse_answers_text, smart_detect_types, submit

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# CONFIG - 预设参数（填写后跳过交互）
# ============================================================
CONFIG = {
    "url": "",
    "cookie": "",
    "question_ids": "",  # 逗号分隔的题目ID列表（从页面HTML提取，优先于 first_question_id）
    "total_questions": 0,
    "answers_file": "正确答案.md",
    "type_distribution": [],  # [单选, 多选, 判断]
    "cpi": "",
    "workRelationId": "",
    "workAnswerId": "",
    "standardEnc": "",
    "referer": "",
}


def load_answers_file(filepath: str) -> str:
    """加载答案文件内容。"""
    resolved = filepath if os.path.isabs(filepath) else os.path.join(SCRIPT_DIR, filepath)
    if not os.path.exists(resolved):
        cwd_path = os.path.join(os.getcwd(), filepath)
        if os.path.exists(cwd_path):
            resolved = cwd_path
        else:
            print(f"❌ 文件不存在：{resolved}")
            sys.exit(1)

    for enc in ["utf-8", "utf-8-sig", "gbk", "gb2312"]:
        try:
            with open(resolved, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    print("❌ 无法读取文件")
    sys.exit(1)


def ask(prompt: str, default: str = "") -> str:
    val = input(prompt).strip()
    return val if val else default


def main():
    print("\n" + "=" * 60)
    print("  学习通答案批量提交工具 (CLI)")
    print("=" * 60)

    config = {}

    # 收集参数
    config["url"] = CONFIG["url"] or ask("\n📎 请求网址: ")
    config["cookie"] = CONFIG["cookie"] or ask("\n🍪 Cookie: ")
    qids_input = CONFIG["question_ids"] or ask("\n🔢 题目ID列表（逗号分隔，如从页面提取）: ")
    config["question_ids"] = [x.strip() for x in qids_input.split(",") if x.strip()]
    config["total_questions"] = int(CONFIG["total_questions"] or ask("\n📊 题目数量: "))
    ans_file = ask(f"\n📄 答案文件 [{CONFIG['answers_file']}]: ", CONFIG["answers_file"])
    config["answers_text"] = load_answers_file(ans_file)

    for p in ["cpi", "workRelationId", "workAnswerId", "standardEnc"]:
        config[p] = CONFIG.get(p, "") or ask(f"\n📋 {p}: ")

    # 预览答案
    answers = parse_answers_text(config["answers_text"])
    total = config["total_questions"]
    if len(answers) < total:
        total = len(answers)
    elif len(answers) > total:
        answers = answers[:total]

    auto_types = smart_detect_types(answers)
    print(f"\n🔍 自动识别：单选{auto_types.count(0)} / 多选{auto_types.count(1)} / 判断{auto_types.count(3)}")

    dist_input = ask(f"   题型分布 [单选,多选,判断] 如 15,10,10（回车=自动）: ")
    if dist_input:
        parts = [int(x.strip()) for x in dist_input.split(",")]
        if len(parts) == 3 and sum(parts) == total:
            config["type_distribution"] = parts
        else:
            print(f"❌ 格式错误或和≠{total}")
            sys.exit(1)
    else:
        config["type_distribution"] = None

    # 确认
    print(f"\n⚠️  即将提交 {total} 道题到 mooc1.chaoxing.com")
    print(f"   前3道答案：{answers[:3]}")
    if ask("确认提交？(y/n): ").lower() != "y":
        print("❌ 已取消")
        return

    # 提交
    result = submit(config)
    print(f"\n📬 状态：{result['status_code']}")
    print(f"   结果：{'✅ 成功' if result['success'] else '❌ 失败'}")
    print(f"   消息：{result.get('msg', '')}")
    if result.get("json"):
        print(json.dumps(result["json"], indent=2, ensure_ascii=False))

    # 保存调试
    debug_file = os.path.join(SCRIPT_DIR, "chaoxing_debug.json")
    with open(debug_file, "w", encoding="utf-8") as f:
        json.dump({"url": config["url"], "result": result}, f, indent=2, ensure_ascii=False)
    print(f"\n📝 调试信息：{debug_file}")


if __name__ == "__main__":
    main()
