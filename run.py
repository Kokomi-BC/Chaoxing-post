"""
============================================================
run.py - 学习通答案提交工具（Web 版）
启动方式：python run.py（自动打开浏览器）
============================================================
"""

import json
import os
import re
import sys
import webbrowser
from threading import Timer
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from flask import Flask, jsonify, render_template, request

# 确保能导入 chaoxing 包中的 core 模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from chaoxing.core import detect_answer_type, parse_answers_text, smart_detect_types, submit

app = Flask(__name__, template_folder=os.path.join(SCRIPT_DIR, "chaoxing", "templates"))
PORT = 5000


# ============================================================
# 路由
# ============================================================

@app.route("/")
def index():
    """主页面。"""
    resp = app.make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/preview", methods=["POST"])
def preview():
    """预览：解析答案文本，返回答案列表和自动识别的题型分布。"""
    data = request.get_json(force=True)
    answers_text = data.get("answers_text", "")
    answers = parse_answers_text(answers_text)

    if not answers:
        return jsonify({"error": "未能解析到答案，请检查格式"}), 400

    # 智能识别题型
    types = smart_detect_types(answers)

    return jsonify({
        "count": len(answers),
        "answers": answers,
        "types_auto": {
            "single": types.count(0),
            "multi": types.count(1),
            "judge": types.count(3),
        },
        "preview": [
            {"index": i + 1, "answer": a, "type": t, "type_name": {0: "单选", 1: "多选", 3: "判断"}[t]}
            for i, (a, t) in enumerate(zip(answers, types))
        ],
    })


@app.route("/api/submit", methods=["POST"])
def do_submit():
    """提交答案到学习通。"""
    data = request.get_json(force=True)

    # 解析题型分布
    dist_raw = data.get("type_distribution", "")
    type_distribution = None
    if dist_raw:
        parts = [x.strip() for x in dist_raw.split(",") if x.strip()]
        if len(parts) == 3:
            try:
                type_distribution = [int(p) for p in parts]
            except ValueError:
                pass

    params = {
        "url": data.get("url", ""),
        "cookie": data.get("cookie", ""),
        "first_question_id": data.get("first_question_id", ""),
        "total_questions": data.get("total_questions", 0),
        "answers_text": data.get("answers_text", ""),
        "type_distribution": type_distribution,
        "cpi": data.get("cpi", ""),
        "workRelationId": data.get("workRelationId", ""),
        "workAnswerId": data.get("workAnswerId", ""),
        "standardEnc": data.get("standardEnc", ""),
        "referer": data.get("referer", ""),
        "question_ids": data.get("question_ids"),
    }

    # 基本校验
    for field in ["url", "cookie"]:
        if not params[field]:
            return jsonify({"error": f"缺少必填字段：{field}"}), 400
    if not params.get("question_ids"):
        return jsonify({"error": "缺少 question_ids（请先用解析功能提取题目ID）"}), 400
    if not params["total_questions"]:
        return jsonify({"error": "题目总数量不能为0"}), 400

    # 校验题型分布
    if type_distribution and sum(type_distribution) != int(params["total_questions"]):
        return jsonify({"error": f"题型分布之和 ({sum(type_distribution)}) ≠ 题目总数 ({params['total_questions']})"}), 400

    result = submit(params)

    # 构建安全返回（不暴露完整 cookie 和 body）
    safe_result = {
        "success": result["success"],
        "msg": result.get("msg", ""),
        "status_code": result["status_code"],
        "total": result.get("total", 0),
        "types_summary": result.get("types_summary", ""),
        "json": result.get("json"),
    }
    if not result["success"] and "response_text" in result:
        safe_result["response_text"] = result["response_text"][:500]

    # 保存调试信息
    if not result["success"]:
        try:
            debug = {
                "url": params["url"],
                "total": params["total_questions"],
                "first_qid": params["first_question_id"],
                "cpi": params["cpi"],
                "workRelationId": params["workRelationId"],
                "workAnswerId": params["workAnswerId"],
                "standardEnc": params["standardEnc"],
                "response": result.get("response_text", "")[:500],
                "status": result["status_code"],
            }
            with open(os.path.join(SCRIPT_DIR, "chaoxing", "debug_fail.json"), "w", encoding="utf-8") as f:
                json.dump(debug, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    return jsonify(safe_result)


# ============================================================
# 页面 URL 解析
# ============================================================

@app.route("/api/parse-url", methods=["POST"])
def parse_url():
    """
    从作业页面 URL 自动提取参数，构造提交 URL。
    输入：{"page_url": "...", "cookie": "..."}
    输出：{submit_url, first_question_id, cpi, workRelationId, ...}
    """
    data = request.get_json(force=True)
    page_url = data.get("page_url", "").strip()
    cookie = data.get("cookie", "").strip()

    if not page_url:
        return jsonify({"error": "请输入页面URL"}), 400
    if not cookie:
        return jsonify({"error": "请输入Cookie"}), 400

    # 从页面 URL 解析已知参数
    parsed = urlparse(page_url)
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    class_id = qs.get("classId", "")
    course_id = qs.get("courseId", "")
    cpi = qs.get("cpi", "")
    work_id = qs.get("workId", "")
    standard_enc = qs.get("standardEnc", "")

    if not class_id or not course_id:
        return jsonify({"error": "URL 中未找到 classId 或 courseId"}), 400

    # 尝试抓取页面提取 token 和 totalQuestionNum
    token = ""
    total_qnum = ""
    first_qid = ""
    question_ids = []
    questions = []
    fetched = False
    debug_html_snippet = ""

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137.0.0.0 Safari/537.36",
            "Cookie": cookie,
        }
        resp = requests.get(page_url, headers=headers, timeout=15)
        html = resp.text
        fetched = True

        # 保存片段用于调试
        debug_html_snippet = html[:3000]

        # ── 从 form action URL 提取 token 和 totalQuestionNum ──
        # 学习通页面的 <form action="/mooc-ans/work/addStudentWorkNewWeb?_classId=...&token=...&totalQuestionNum=...">
        m = re.search(r'action\s*=\s*"[^"]*addStudentWorkNewWeb[^"]*"', html)
        if not m:
            m = re.search(r"action\s*=\s*'[^']*addStudentWorkNewWeb[^']*'", html)
        if m:
            action_url = m.group(0).replace("&amp;", "&")
            t = re.search(r'token=([a-f0-9]+)', action_url)
            if t: token = t.group(1)
            t = re.search(r'totalQuestionNum=([a-f0-9]+)', action_url)
            if t: total_qnum = t.group(1)

        # ── 提取 workAnswerId（页面隐藏字段，URL中为0）──
        m = re.search(r'name\s*=\s*["\']workAnswerId["\']\s+value\s*=\s*["\'](\d+)["\']', html)
        if m: qs["answerId"] = m.group(1)

        # ── 提取全部题目 ID 和题目文本（从 answertype 隐藏字段和 questionLi div）──
        # 学习通题目 ID 不一定连续，必须从页面提取实际 ID
        all_qids = re.findall(r'name\s*=\s*["\']answertype(\d+)["\']', html)
        if all_qids:
            # 去重并保持页面出现顺序
            seen = set()
            question_ids = []
            for qid in all_qids:
                if qid not in seen:
                    seen.add(qid)
                    question_ids.append(qid)
            first_qid = question_ids[0] if question_ids else ""

            # ── 提取题目文本及选项 ──
            questions = []
            # 匹配每个 questionLi div，提取 typeName、id 和内容
            qblocks = re.findall(
                r'<div[^>]*class="[^"]*questionLi[^"]*"[^>]*typeName="([^"]*)"[^>]*id="question(\d+)"[^>]*>'
                r'(.*?)'
                r'(?=<div[^>]*class="[^"]*questionLi[^"]*"|<div[^>]*class="[^"]*whiteDiv[^"]*"|</form)',
                html, re.DOTALL
            )
            # 为每个已提取的 question_id 查找对应文本和选项
            qid_to_type = {}
            qid_to_text = {}
            qid_to_options = {}
            for type_name, qid, block in qblocks:
                # 提取 h3 中的题目文字
                h3_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
                if h3_match:
                    raw_text = h3_match.group(1)
                    raw_text = re.sub(r'<[^>]+>', '', raw_text)
                    raw_text = re.sub(r'\s+', ' ', raw_text).strip()
                    raw_text = re.sub(r'^\d+[\.\、\)\s]\s*', '', raw_text)
                    raw_text = re.sub(r'^\([^)]*\)\s*', '', raw_text)
                    qid_to_text[qid] = raw_text
                qid_to_type[qid] = type_name

                # ── 提取选项 ──
                # 选项结构: <span data="A" class="choiceXXXXXX num_option ...">A</span>
                #            <div class="fl answer_p"><p>选项文本</p></div>
                options = []
                opt_spans = re.findall(
                    r'<span[^>]*data="([A-Z]+)"[^>]*class="[^"]*choice\d+[^"]*num_option[^"]*"[^>]*>.*?</span>'
                    r'\s*<div[^>]*class="[^"]*answer_p[^"]*"[^>]*>\s*(.*?)\s*</div>',
                    block, re.DOTALL
                )
                for letter, opt_html in opt_spans:
                    opt_text = re.sub(r'<[^>]+>', '', opt_html).strip()
                    opt_text = re.sub(r'\s+', ' ', opt_text)
                    options.append({"letter": letter, "text": opt_text})
                qid_to_options[qid] = options

            for i, qid in enumerate(question_ids):
                questions.append({
                    "id": qid,
                    "index": i + 1,
                    "typeName": qid_to_type.get(qid, "未知"),
                    "text": qid_to_text.get(qid, ""),
                    "options": qid_to_options.get(qid, []),
                })
        else:
            question_ids = []
            questions = []

        # 兜底：如果上面没提取到，用9位数字猜测
        if not question_ids:
            know = {class_id, course_id, cpi, qs.get("workId",""), qs.get("_uid",""), qs.get("answerId","")}
            nine = re.findall(r'\b(\d{9})\b', html)
            if nine:
                from collections import Counter
                for nid, _ in Counter(nine).most_common():
                    if nid not in know and nid != "000000000":
                        first_qid = nid
                        break

    except requests.RequestException as e:
        debug_html_snippet = f"请求异常: {e}"

    # 构造提交 URL
    submit_url = (
        f"https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNewWeb"
        f"?_classId={class_id}"
        f"&courseid={course_id}"
        f"&token={token}"
        f"&totalQuestionNum={total_qnum}"
        f"&wMicroNodeId="
        f"&pyFlag=1"
        f"&ua=pc"
        f"&formType=post"
        f"&saveStatus=1"
        f"&version=1"
    )

    return jsonify({
        "submit_url": submit_url,
        "class_id": class_id,
        "course_id": course_id,
        "cpi": cpi,
        "workRelationId": work_id,
        "workAnswerId": qs.get("answerId", ""),
        "standardEnc": standard_enc,
        "first_question_id": first_qid,
        "question_ids": question_ids,
        "questions": questions,
        "token": token,
        "totalQuestionNum": total_qnum,
        "fetched": fetched,
        "warnings": [
            w for w in [
                None if token else "⚠️ 未能提取 token，请检查页面URL或Cookie是否有效",
                None if total_qnum else "⚠️ 未能提取 totalQuestionNum",
                None if first_qid else "⚠️ 未能提取第一题ID，请手动填写",
            ] if w
        ],
        "debug": debug_html_snippet[:2000] if not token or not total_qnum else "",
    })


# ============================================================
# 启动
# ============================================================

def open_browser():
    """在新标签页打开浏览器。"""
    webbrowser.open_new_tab(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    print("=" * 60)
    print("  学习通答案提交工具 - Web 版")
    print("=" * 60)
    print(f"\n🚀 服务启动中...")
    print(f"   本地地址：http://127.0.0.1:{PORT}")
    print(f"   按 Ctrl+C 停止服务\n")

    # 延迟 0.8 秒自动打开浏览器
    Timer(0.8, open_browser).start()

    app.run(host="127.0.0.1", port=PORT, debug=False)
