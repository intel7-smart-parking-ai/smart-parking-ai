"""Vehicle/bay selection for demo C/D; never changes occupancy or sessions."""

VEHICLE_TYPES = ("general", "ev", "accessible")
REGISTERED_VEHICLES = {"9001": "ev", "9002": "accessible"}


def entry_vehicle_type(number, requested=None):
    registered = REGISTERED_VEHICLES.get(number)
    if registered and requested and registered != requested:
        raise ValueError("등록 차량의 종류와 선택한 차량 종류가 다릅니다.")
    return registered or requested or "general"


def choose_entry_type(rows, targets, available):
    """Fill all three types early, then balance their occupied/capacity ratios."""
    candidates = {row["space_type"] for row in available}
    def ratio(kind):
        bays = [row for row in rows if row["space_type"] == kind]
        occupied = sum(bool(targets[row["space_id"]]["occupied"]) for row in bays)
        return occupied / len(bays)
    return min((kind for kind in VEHICLE_TYPES if kind in candidates), key=ratio)
