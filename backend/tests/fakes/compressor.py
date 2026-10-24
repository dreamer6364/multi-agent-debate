"""开发档五栏压缩器：确定性输出，不调模型（脚本模式专用）。"""


def fake_columns(speeches):
    if not speeches:
        return {}
    return {
        "definition_judge": "双方定义与判准已记录在案。",
        "arguments": "双方论点继续展开并互相拆解。",
        "clashes": "本环节双方围绕判准与定义直接交锋。",
        "fallacies": "双方互相指出对方论证瑕疵。",
        "situation": "局势推进，战场逐渐聚焦。",
    }
