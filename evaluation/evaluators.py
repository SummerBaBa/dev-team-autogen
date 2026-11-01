"""
LangSmith 评估器（Evaluators）—— 自动化质量评估

本模块实现与 langsmith.evaluate() 兼容的评估函数，用于
对多 Agent 软件开发流水线的输出进行自动化质量评估。

两种评估器类型：
  1. 启发式/规则评估器（Heuristic Evaluators）
     不依赖外部 LLM，纯 Python 逻辑，运行快、结果确定：
       - completion_evaluator    → 流水线完成率
       - duration_evaluator      → 执行耗时评分
       - code_validity_evaluator → 生成代码的语法正确性（AST 解析）
       - guardrail_evaluator     → 安全护栏拦截率

  2. LLM-as-Judge 评估器
     使用另一个 LLM 对生成产物进行多维质量评分：
       - _make_llm_judge_evaluator → 评估 PRD/代码/测试/修复/回归质量

设计决策：
  - 评估器是框架无关的（纯 Python，无 LangChain 依赖）
  - LLM-as-Judge 使用项目现有的 OpenAI 兼容客户端
  - 在资源不可用时优雅降级（返回 0.0 而非崩溃）

可在简历中描述为：
  "设计了 4 个自动化评估器（完成率/耗时/代码AST/护栏健康）和
   LLM-as-Judge 质量评分系统，实现对多 Agent 流水线输出的
   多维自动化评估，替代了传统的手动 BenchmarkRunner。"
"""

import ast
import re
from typing import Any, Optional


# ═════════════════════════════════════════════════════════════════
# 启发式 / 规则评估器
# 不依赖任何外部 LLM 调用，纯 Python 逻辑，结果确定且可复现。
# ═════════════════════════════════════════════════════════════════


def completion_evaluator(run: Any, example: Any) -> dict:
    """评估器：流水线完成率。

    从 pipeline 输出中提取 completion_rate（各阶段完成比例）。
    Score: 0.0（全部失败）~ 1.0（5 个阶段全部完成）

    这个评估器反映了流水线整体的稳定性：
      - 1.0 → 所有 Agent 都成功完成了任务
      - 0.6 → 5 个阶段中只有 3 个完成（中间某个 Agent 失败）
      - 0.0 → 可能在输入护栏就被拦截了
    """
    outputs = run.outputs or {}
    rate = outputs.get("completion_rate", 0.0)
    return {
        "key": "pipeline_completion",
        "score": rate,
        "comment": f"Pipeline completed at {rate:.0%}",
    }


def duration_evaluator(run: Any, example: Any) -> dict:
    """评估器：执行耗时评分。

    将原始耗时（毫秒）映射到 0.0~1.0 的评分：
      score = max(0, 1 - duration_ms / 600000)

    600 秒（10 分钟）被定义为健康运行的上限阈值。
    超过 10 分钟则评分为 0.0。

    这个评估器反映了流水线的执行效率：
      - 耗时越短，评分越高
      - 适用于对比不同模型/配置下的性能差异
    """
    outputs = run.outputs or {}
    ms = outputs.get("duration_ms", 0)
    score = max(0.0, 1.0 - ms / 600_000.0)
    return {
        "key": "duration_score",
        "score": round(score, 3),
        "comment": f"Duration: {ms / 1000:.1f}s",
    }


def code_validity_evaluator(run: Any, example: Any) -> dict:
    """评估器：生成代码的语法正确性。

    使用 Python 的 ast.parse() 对 Agent 生成的代码进行静态分析。
    支持从 Dev（02_CODE）和 Fixer（04_FIXED_CODE）两个阶段的
    产物中提取 Python 代码块。

    代码提取策略（由 _extract_python_code 实现）：
      1. Markdown 代码块（```python, ```py, ```）
      2. 从第一个 Python 语句到文本末尾
      3. 逐行扫描语法有效的 Python 片段

    Score: 0.0（语法错误/代码缺失） 或 1.0（语法正确）
    """
    outputs = run.outputs or {}
    artifacts: dict = outputs.get("artifacts", {})
    present = [k for k in ("02_CODE", "04_FIXED_CODE") if k in artifacts]
    n_missing = 2 - len(present)

    code_parts: list[str] = []
    for key in ("02_CODE", "04_FIXED_CODE"):
        raw = artifacts.get(key, "")
        block = _extract_python_code(raw)
        if block:
            code_parts.append(block)

    body = "\n\n".join(code_parts).strip()

    # Fallback: 尝试将完整产物内容作为 Python 解析
    if not body:
        for key in ("02_CODE", "04_FIXED_CODE"):
            raw = artifacts.get(key, "")
            candidate = raw.strip().removeprefix("```python").removeprefix("```py").removeprefix("```")
            candidate = candidate.removesuffix("```").strip()
            try:
                ast.parse(candidate)
                return {"key": "code_validity", "score": 1.0, "comment": "Valid (raw fallback)"}
            except SyntaxError:
                continue
        details = f"Missing {n_missing} artifact(s); " if n_missing else ""
        details += "no valid Python code found"
        return {"key": "code_validity", "score": 0.0, "comment": details}

    try:
        ast.parse(body)
        lines = body.count("\n") + 1
        return {
            "key": "code_validity",
            "score": 1.0,
            "comment": f"Valid Python syntax ({lines} lines, {len(present)} artifacts)",
        }
    except SyntaxError as e:
        return {
            "key": "code_validity",
            "score": 0.0,
            "comment": f"Syntax error at line {e.lineno}: {e.msg}",
        }


def guardrail_evaluator(run: Any, example: Any) -> dict:
    """评估器：安全护栏健康度。

    护栏拦截次数越少，评分越高。
    Score: 1.0 - (blocks / total_phases)

    示例：
      - 0 次拦截 / 5 个阶段 → score = 1.0（完美）
      - 1 次拦截 / 5 个阶段 → score = 0.8
      - 3 次拦截 / 5 个阶段 → score = 0.4

    这个评估器反映了模型的"安全性"——如果模型频繁产生
    被护栏拦截的内容，说明其行为不够稳定或安全。
    """
    outputs = run.outputs or {}
    blocks = outputs.get("guardrail_blocks", 0)
    phases = outputs.get("num_phases", 5)
    score = 1.0 - (blocks / max(phases, 1))
    return {
        "key": "guardrail_health",
        "score": round(max(score, 0.0), 3),
        "comment": f"{blocks} guardrail blocks across {phases} phases",
    }


# ═════════════════════════════════════════════════════════════════
# LLM-as-Judge 评估器
# 使用另一个 LLM 对生成产物进行多维质量评分。
# 需要 OpenAI 兼容的 API 端点（与流水线使用同一个）。
# ═════════════════════════════════════════════════════════════════


def _make_llm_judge_evaluator(
    api_base: str = "http://127.0.0.1:9000/v1",
    api_key: str = "12345678",
    model: str = "Qwen3.8-27B-MLX-4bit",
):
    """工厂函数：创建一个 LLM-as-Judge 评估器。

    评估维度（5 个，每项 0.0~1.0）：
      1. prd_quality:          PRD 是否完整、清晰、结构良好？
      2. code_quality:         代码是否正确、完整、高质量？
      3. test_quality:         测试用例是否全面、文档完善？
      4. fix_quality:          修复是否解决了报告的问题？
      5. regression_quality:   回归报告是否验证了修复？

    overall 分数由 LLM 综合给出。

    Args:
        api_base: OpenAI 兼容 API 地址（默认本地 127.0.0.1:9000）
        api_key:  API 密钥
        model:    用作 Judge 的模型名

    NOTE: 本地量化模型可能产生不可靠的评分。
    生产环境建议使用更强的模型（GPT-4o, Claude-3.5）作为 Judge。
    """
    from openai import OpenAI

    client = OpenAI(base_url=api_base, api_key=api_key)

    JUDGE_PROMPT = """You are an expert software engineering evaluator.
Assess the quality of the generated solution below.

Requirements: {requirement}

Generated PRD:
{prd}

Generated Code:
{code}

Test Report:
{report}

Fixed Code:
{fixed_code}

Regression Report:
{regression}

Score each dimension 0.0-1.0:
1. prd_quality: Is the PRD complete, clear, and well-structured?
2. code_quality: Is the code correct, complete, and well-written?
3. test_quality: Are the test cases comprehensive and well-documented?
4. fix_quality: Did the fixer address the reported issues?
5. regression_quality: Does the regression report validate the fixes?

Return ONLY a JSON object like:
{{"prd_quality": 0.9, "code_quality": 0.8, "test_quality": 0.7,
  "fix_quality": 0.85, "regression_quality": 0.75, "overall": 0.8}}"""

    def _judge(run: Any, example: Any) -> dict:
        """LLM-as-Judge 评估器内部函数。

        接收 pipeline 的输出产物，使用 LLM 进行多维质量评分。
        如果产物缺失或 LLM 调用失败，优雅降级返回 0.0。
        """
        outputs = run.outputs or {}
        artifacts: dict = outputs.get("artifacts", {})
        req = (example.inputs or {}).get("requirement", "")

        if not artifacts:
            return {"key": "llm_judge_quality", "score": 0.0, "comment": "No artifacts"}

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": JUDGE_PROMPT.format(
                            requirement=req,
                            prd=artifacts.get("01_PRD", "")[:2000],
                            code=artifacts.get("02_CODE", "")[:3000],
                            report=artifacts.get("03_TEST_REPORT", "")[:2000],
                            fixed_code=artifacts.get("04_FIXED_CODE", "")[:3000],
                            regression=artifacts.get("05_REGRESSION_REPORT", "")[:2000],
                        ),
                    }
                ],
                temperature=0.0,
                max_tokens=500,
            )
            import json

            scores = json.loads(resp.choices[0].message.content)
            overall = scores.get("overall", scores.get("code_quality", 0.0))
            return {
                "key": "llm_judge_quality",
                "score": overall,
                "comment": json.dumps(scores, ensure_ascii=False),
            }
        except Exception as e:
            return {"key": "llm_judge_quality", "score": 0.0, "comment": f"Error: {e}"}

    return _judge


# ═════════════════════════════════════════════════════════════════
# 辅助函数
# ═════════════════════════════════════════════════════════════════


def _extract_python_code(text: str) -> Optional[str]:
    """从 LLM 输出中提取 Python 代码，尝试多种策略。

    由于不同的 LLM 可能以不同格式输出代码，设计了三级提取策略：

    Strategy 1: Markdown 代码块
      匹配 ```python / ```py / ``` 包含的代码内容。
      这是最常见也是最可靠的提取方式。

    Strategy 2: Python 语句起始
      找到第一个以 import/from/def/class/print/if __name__ 开头的行，
      从该行到文本末尾作为代码。然后尝试 ast.parse 验证。

    Strategy 3: 逐行扫描
      逐行尝试 ast.parse，收集语法有效的代码行。
      作为最后的兜底策略，适用于格式极其不规范的输出。

    Returns:
        提取的 Python 代码字符串，如果无法提取则返回 None。
    """
    if not text:
        return None

    # Strategy 1: markdown code blocks
    for pattern in (r"```python\n(.*?)```", r"```py\n(.*?)```", r"```\n(.*?)```"):
        m = re.search(pattern, text, re.DOTALL)
        if m and m.group(1).strip():
            return m.group(1).strip()

    # Strategy 2: find the first line that starts a Python construct
    lines = text.split("\n")
    py_starts = {"import ", "from ", "def ", "class ", "print(", "if __name__", "#!"}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped.startswith(k) for k in py_starts):
            candidate = "\n".join(lines[i:]).strip()
            try:
                ast.parse(candidate)
                return candidate
            except SyntaxError:
                continue

    # Strategy 3: try to extract all lines that parse as valid Python statements
    chunks: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*", "```")):
            continue
        try:
            ast.parse(stripped)
            chunks.append(stripped)
        except SyntaxError:
            if chunks and not stripped.startswith(("```", "---", "===")):
                chunks.append(stripped)
    if chunks:
        candidate = "\n".join(chunks)
        try:
            ast.parse(candidate)
            return candidate
        except SyntaxError:
            pass

    return None


def _read_artifact(name: str, outdir: str = "output") -> Optional[str]:
    """从磁盘读取产物文件。"""
    path = f"{outdir}/{name}.md"
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
