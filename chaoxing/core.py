"""
============================================================
模块：core.py
功能：学习通 POST 提交核心逻辑（供 CLI 和 Web 共用）
============================================================
"""

import re
from urllib.parse import parse_qs, urlparse

import requests

# 判断题映射
TF_MAPPING = {"A": "true", "B": "false"}


def parse_answers_text(text: str) -> list[str]:
    """从文本解析答案列表，自动过滤序号和空白行。"""
    import re
    answers = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 去除行首序号（如 "1." "1、" "1)" "1. " "1 "）
        stripped = re.sub(r'^\d+[\.\、\)\s]\s*', '', stripped)
        if stripped and not stripped.isdigit():
            answers.append(stripped)
    return answers


def detect_answer_type(answer: str) -> int:
    """单题识别：1=多选, 3=判断, 0=单选。"""
    if answer.lower() in ("true", "false", "对", "错"):
        return 3
    if len(answer) > 1 and answer.replace(" ", "").isalpha():
        return 1
    return 0


def smart_detect_types(answers: list[str]) -> list[int]:
    """
    智能识别题型列表：
      - 多字符纯字母（如 ABCDE）→ 多选题 (type=1)
      - "true"/"false"/"对"/"错" → 判断题 (type=3)
      - 最后一个多选题之后，若答案仅为 A 或 B → 判断题 (type=3)
      - 其余单字母 → 单选题 (type=0)
    """
    n = len(answers)
    types = [0] * n

    # 第一遍：标记多选和明确判断
    last_multi_idx = -1
    for i, a in enumerate(answers):
        if a.lower() in ("true", "false", "对", "错"):
            types[i] = 3
        elif len(a) > 1 and a.replace(" ", "").isalpha():
            types[i] = 1
            last_multi_idx = i

    # 第二遍：最后一个多选题之后，A/B → 判断题
    # 只有确实存在多选题时才触发此规则
    if last_multi_idx >= 0:
        for i in range(last_multi_idx + 1, n):
            if types[i] == 0:
                stripped = answers[i].strip().upper()
                if stripped in ("A", "B"):
                    types[i] = 3

    return types


def build_types(answers: list[str], distribution: list[int] | None) -> list[int]:
    """根据答案和分布构建题型列表。distribution 为 [单选,多选,判断]。"""
    total = len(answers)
    if distribution and len(distribution) == 3 and sum(distribution) == total:
        types = []
        types.extend([0] * distribution[0])
        types.extend([1] * distribution[1])
        types.extend([3] * distribution[2])
        return types
    else:
        return smart_detect_types(answers)


def format_answer(answer: str, qtype: int, tf_map: dict | None = None) -> str:
    """格式化答案值。"""
    if tf_map is None:
        tf_map = TF_MAPPING
    if qtype == 3:
        return tf_map.get(answer.upper(), answer.lower())
    return answer


def submit(params: dict) -> dict:
    """
    提交答案到学习通。

    params 必填字段：
        url, cookie, question_ids (从页面提取的题目ID列表), total_questions,
        answers_text (或 answers_list), type_distribution (可选),
        cpi, workRelationId, workAnswerId, standardEnc

    返回：{"success": bool, "msg": str, "status_code": int, "body": dict, "response_text": str}
    """
    url = params["url"]
    cookie = params["cookie"]
    first_id = params.get("first_question_id", "")
    total = int(params["total_questions"])
    cpi = params.get("cpi", "")
    work_relation_id = params.get("workRelationId", "")
    work_answer_id = params.get("workAnswerId", "")
    standard_enc = params.get("standardEnc", "")
    referer = params.get("referer", "")
    tf_map = params.get("tf_mapping", TF_MAPPING)

    # 解析答案
    if "answers_list" in params and params["answers_list"]:
        answers = params["answers_list"]
    else:
        answers = parse_answers_text(params.get("answers_text", ""))

    if not answers:
        return {"success": False, "msg": "未能解析到任何答案", "status_code": 0}

    if len(answers) < total:
        total = len(answers)
    elif len(answers) > total:
        answers = answers[:total]

    # 题型
    dist = params.get("type_distribution")
    types = build_types(answers, dist)

    # 解析 URL 参数
    parsed_url, url_params = _parse_url(url)
    class_id = url_params.get("_classId", url_params.get("classId", ""))
    course_id = url_params.get("courseid", url_params.get("courseId", ""))
    token_val = url_params.get("token", "")

    # 题目 ID：必须从页面提取的实际 ID 列表
    question_ids = params.get("question_ids")
    if not question_ids or len(question_ids) == 0:
        return {"success": False, "msg": "缺少 question_ids（请先用解析功能从页面提取题目ID）", "status_code": 0}
    if len(question_ids) < total:
        total = len(question_ids)
    question_ids = question_ids[:total]
    answerwqbid = ",".join(question_ids) + ","

    # 构建 body
    body = {
        "_classId": class_id,
        "courseid": course_id,
        "token": token_val,
        "totalQuestionNum": url_params.get("totalQuestionNum", ""),
        "wMicroNodeId": url_params.get("wMicroNodeId", ""),
        "pyFlag": url_params.get("pyFlag", "1"),
        "ua": url_params.get("ua", "pc"),
        "formType": url_params.get("formType", "post"),
        "saveStatus": url_params.get("saveStatus", "1"),
        "version": url_params.get("version", "1"),
        "courseId": course_id,
        "classId": class_id,
        "knowledgeid": "0",
        "cpi": cpi,
        "workRelationId": work_relation_id,
        "workAnswerId": work_answer_id,
        "jobid": url_params.get("jobid", ""),
        "standardEnc": standard_enc,
        "enc_work": token_val,
        "answerwqbid": answerwqbid,
        "mooc2": "1",
        "randomOptions": "false",
        "workTimesEnc": url_params.get("workTimesEnc", ""),
    }

    # 填写答案
    for i, qid in enumerate(question_ids):
        qtype = types[i]
        body[f"answertype{qid}"] = str(qtype)
        body[f"answer{qid}"] = format_answer(answers[i], qtype, tf_map)

    # 清理空值（保留必要的空字段）
    keep_empty = {"wMicroNodeId", "jobid", "workTimesEnc"}
    body = {
        k: v for k, v in body.items()
        if v != "" or k.startswith("answer") or k.startswith("answertype") or k in keep_empty
    }

    # 请求头
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": cookie,
        "Origin": f"{parsed_url.scheme}://{parsed_url.netloc}",
        "Referer": referer or url,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }

    # 发送请求
    try:
        resp = requests.post(url, data=body, headers=headers, timeout=30, allow_redirects=False)
    except requests.RequestException as e:
        return {"success": False, "msg": f"网络请求失败：{e}", "status_code": 0}

    result = {
        "status_code": resp.status_code,
        "response_text": resp.text,
        "body": body,
        "total": total,
        "types_summary": f"单选{types.count(0)}/多选{types.count(1)}/判断{types.count(3)}",
    }

    try:
        json_resp = resp.json()
        result["success"] = (json_resp.get("status") == True or str(json_resp.get("status")).lower() == "true")
        result["msg"] = json_resp.get("msg", "")
        result["json"] = json_resp
    except ValueError:
        result["success"] = False
        result["msg"] = resp.text[:200]

    return result


def _parse_url(url: str):
    """解析 URL 返回 (parsed, params_dict)。"""
    parsed = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    return parsed, params
