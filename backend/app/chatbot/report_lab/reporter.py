LABELS = {"revenue": "오늘 모의 매출", "alerts": "경보 상태 집계",
          "spaces": "현재 빈자리", "fees": "요금·결제 조회",
          "vehicle": "차량 위치", "policies": "이용 정책·시설 안내"}


def render(tasks, state):
    cards = []
    for task in tasks:
        result = state[task]
        cards.append({"title": LABELS[task], **result})
    return cards
