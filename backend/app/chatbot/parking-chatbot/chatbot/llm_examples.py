"""Three fixed classification examples, chosen before the holdout was frozen."""
EXAMPLES = (
    ("청구된 내역의 액수만 열람할게요", "get_fee"),
    ("상주 인력의 상담 가능 시간을 알고 싶습니다", "admin_help"),
    ("수채화 물감은 어느 종이가 잘 받나요", "unknown"),
)


def messages():
    # Import only when enabled, preserving operation without optional packages.
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    result = []
    for index, (text, intent) in enumerate(EXAMPLES):
        call_id = f"classification-example-{index}"
        result.extend((HumanMessage(content=text),
                       AIMessage(content="", tool_calls=[{"name":intent, "args":{}, "id":call_id, "type":"tool_call"}]),
                       ToolMessage(content="업무를 실행하지 않은 분류 예시입니다.", tool_call_id=call_id)))
    return result
