"""Check coverage and safety/lookup contracts from the isolated replay results."""
import json
from pathlib import Path


def load(path, count):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = {}
    for row in rows:
        if type(row["n"]) is int:
            cases[row["n"]] = row["result"]
    assert set(cases) == set(range(1, count + 1)), f"Incomplete suite: {path}"
    return rows, cases


a, _ = load("chatbot-1109-structural-A.json", 63)
b, cases = load("chatbot-1109-B-structural.json", 50)
bare_yes = next(row["result"] for row in a if row["n"] == "20-check" and row["question"] == "bare yes")
confirmed = next(row["result"] for row in a if row["n"] == "20-check" and row["question"] == "after button")
duplicate = next(row["result"] for row in a if row["n"] == "21-check")
assert bare_yes is None and confirmed["payment_id"] == duplicate["payment_id"]
assert next(row["result"][0] for row in a if row["n"] == "25-check") == 409

for number, expected in {
    3: ("A-05", "비어"), 4: ("B-30", "없어요"), 7: ("A-02", "확인 중"),
    8: ("A-06", "이용할 수 없는"), 11: ("A-03", "B-02"), 16: ("입차했어요",),
    17: ("주차 시간", "분"), 18: ("현재 주차 중",), 27: ("주차할 수 없어요",),
    28: ("장애인 주차표지", "주차할 수 없어요"), 29: ("일반 주차칸", "주차할 수 있어요"),
    34: ("대피", "119", "관리자"), 47: ("10", "A-04"), 48: ("어느 차량", "27번", "10번"),
    49: ("27번", "모의 결제할까요"), 50: ("취소",),
}.items():
    assert all(part in cases[number]["text"] for part in expected), (number, cases[number]["text"])
assert cases[48]["confirmation_token"] is None
assert cases[49]["confirmation_token"] and cases[50]["confirmation_token"] is None
before = next(row["result"] for row in b if row["n"] == "after-48")
after = next(row["result"] for row in b if row["n"] == "after-50")
assert before["27"]["payment"] is None and after["27"]["payment"] is None
print("113 questions replayed; targeted lookup, emergency and payment checks passed.")
