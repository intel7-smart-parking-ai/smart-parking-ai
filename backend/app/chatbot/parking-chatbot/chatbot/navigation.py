"""주차장·구역 조회와 목적지 감시. PROVISIONAL(API v1 확정 필요)."""
import re
import time
import uuid
from . import config
from .contracts import alerts_source
from .adapters import iso,now


def count(item, zone=None):
    value = item if zone is None else item.get("zones",{}).get(zone)
    return value.get("empty") if value is not None else None


def reason(snapshot, alert, lot, zone=None):
    item = snapshot["lots"].get(lot)
    if item is None:return "주차장 정보 없음"
    if alert.get("level",0)>=2 and alert.get("lot_id") in (None,lot):return "경보"
    if item.get("entry_allowed") is False:return "운영 제한" + (": "+item["restriction_reason"] if item.get("restriction_reason") else "")
    if snapshot.get("stale") or alert.get("stale") or (alert.get("unavailable") and alerts_source(alert) != "not_provided"):return "현재 상태 확인 불가"
    n=count(item,zone)
    if n is None:return "구역별 정보 미제공"
    return "만차" if n==0 else None


def status_sentence(lbl, why, changed=False):
    """이용 불가 사유를 쉬운 문장으로. why는 reason()의 결과."""
    if why == "만차":
        return f"{lbl}이 만차가 됐어요." if changed else f"{lbl}은 만차예요."
    if why == "경보":
        return f"{lbl}에 경보가 발생했어요." if changed else f"{lbl}은 경보 중이라 이용할 수 없어요."
    if why.startswith("운영 제한"):
        detail = why.split(": ", 1)[1] if ": " in why else ""
        base = f"{lbl}을 지금 이용할 수 없게 됐어요." if changed else f"{lbl}은 지금 이용할 수 없어요."
        return base + (f" ({detail})" if detail else "")
    if why == "현재 상태 확인 불가":
        return f"{lbl} 상태를 지금 확인할 수 없어요."
    if why == "구역별 정보 미제공":
        return f"{lbl} 정보는 아직 제공되지 않아요."
    return f"{lbl} 정보를 찾을 수 없어요."


def candidates(snapshot,alert,zone=None,exclude=None):
    order = {key:i for i,key in enumerate(config.catalog.ids())}
    valid = [key for key in snapshot["lots"] if key!=exclude and reason(snapshot,alert,key,zone) is None]
    return sorted(valid,key=lambda k:(-count(snapshot["lots"][k],zone),order.get(k,len(order))))[:2]


def label(lot,zone=None):
    return config.catalog.name(lot)+(f" {config.catalog.zone_name(zone)} 구역" if zone else "")


def go_id(lot,zone=None):return "go:"+lot+(":"+zone if zone else "")




def card(snapshot,alert,lot=None,zone=None):
    lots = {key: dict(item) for key, item in snapshot["lots"].items()}
    metadata = config.catalog.lots()
    for key,item in lots.items():
        meta = metadata.get(key, {})
        item.setdefault("name",meta.get("name",key));item.setdefault("kind",meta.get("kind","unknown"))
        item["display_empty"]=count(item,zone)
        item["display_uncertain"]=(item if zone is None else item.get("zones",{}).get(zone,{})).get("uncertain",0)
        item["status_reason"]=reason(snapshot,alert,key,zone)
    return {"type":"empty_spots","data":{"lots":lots,"focus":lot,"zone":zone,
            "zone_labels":{z["key"]:z["name"] for z in config.catalog.zones()},
            "blocked_lots":[k for k,v in lots.items() if v["status_reason"] and v["status_reason"]!="만차"]}}


def alternatives(snapshot,alert,lot=None,zone=None):
    others=candidates(snapshot,alert,zone,lot)
    if others:
        text=" / ".join(f"{label(k,zone)} {count(snapshot['lots'][k],zone)}자리" for k in others)
        chips=[go_id(k,zone) for k in others]
    else:
        text="모든 주차장이 만차예요." if all(reason(snapshot,alert,k,zone)=="만차" for k in snapshot["lots"]) else "지금은 이용 가능한 곳이 없어요."
        chips=["empty_all", "call_admin"]
    chips += ["destination_cancel"]  # 목적지가 없으면 engine에서 숨김
    return text,chips[:4]


def query(snapshot,alert,params):
    lot,zone=params.get("lot_id"),params.get("zone")
    view=card(snapshot,alert,lot,zone)
    if lot and lot not in snapshot["lots"]:return "해당 주차장 정보가 아직 연결되지 않았어요.",None,["empty_all"],snapshot["updated_at"]
    if lot:
        item=snapshot["lots"][lot];n=count(item,zone);why=reason(snapshot,alert,lot,zone)
        if n is None and why == "구역별 정보 미제공":
            text=f"구역별 정보는 아직 제공되지 않아요.\n{config.catalog.name(lot)} 전체 빈자리는 {item['empty']}자리예요."
            actions=["lot_"+lot,"empty_all"]
        elif why:
            other,actions=alternatives(snapshot,alert,lot,zone)
            text=f"{status_sentence(label(lot,zone),why)}\n{other}"
        else:
            text=f"{label(lot,zone)}에 {n}자리 있어요."
            actions=[go_id(lot,zone),"empty_all","destination_cancel"]
    else:
        best=candidates(snapshot,alert,zone)
        if best:
            key=best[0];text=f"{label(key,zone)}에 {count(snapshot['lots'][key],zone)}자리 있어요."
            actions=[go_id(k,zone) for k in best]
            actions += ["zone:"+z["key"] for z in config.catalog.zones() if z.get("home") and z["key"]!=zone]
        elif zone and any(count(v,zone) is None for v in snapshot["lots"].values()):
            text="구역별 정보는 아직 제공되지 않아요.\n전체 빈자리는 카드에서 확인해 주세요.";actions=["empty_all"]
        else:
            text,actions=alternatives(snapshot,alert,None,zone)
    # 확인 중 칸은 어느 주차장 것인지 분명히 붙인다. 주차장을 지정한 질문은 첫 줄(그 주차장 문장)에,
    # 전체 질문은 주차장 이름과 함께 끝에 붙인다.
    if lot:
        pending = view["data"]["lots"].get(lot,{}).get("display_uncertain",0)
        if pending:
            first, sep, rest = text.partition("\n")
            text = f"{first} (카메라 확인 중 {pending}칸){sep}{rest}"
    else:
        pending = [f"{config.catalog.name(k)} {v['display_uncertain']}칸" for k,v in view["data"]["lots"].items() if v.get("display_uncertain")]
        if pending: text += f" (카메라 확인 중: {', '.join(pending)})"
    return text,view,actions[:4],snapshot["updated_at"]


def resolve_context(s,intent,params,text,source):
    current=time.monotonic()
    if current-s.query_context_at>config.QUERY_CONTEXT_TTL_SEC:s.query_context={}
    inherited = (source!="chip" and s.last_intent=="get_empty_spots" and
                 (re.search(r"그럼|그러면|(?:는|은)\s*[?？]?\s*$",text or "") or (intent=="unknown" and (params.get("lot_id") or params.get("zone")))))
    if intent=="set_destination" and s.last_intent=="get_empty_spots":
        params={**s.query_context,**params}
    elif inherited and intent in ("unknown","get_empty_spots") and s.query_context:
        intent="get_empty_spots";params={**s.query_context,**params}
    if intent=="get_empty_spots":
        s.query_context={k:params[k] for k in ("lot_id","zone") if params.get(k)};s.query_context_at=current
    else:s.query_context={}
    return intent,params


def clear(s):
    s.destination=None;s.destination_problem=None;s.destination_expires=0


def public(s):
    if s.destination and time.monotonic()>=s.destination_expires:clear(s)
    return {k:v for k,v in s.destination.items() if not k.startswith("_")} if s.destination else None


def choose(s,snapshot,alert,params,vehicle=None):
    lot,zone=params.get("lot_id"),params.get("zone")
    if not lot:return "안내받을 주차장을 골라 주세요.",card(snapshot,alert),["lot_"+k for k in snapshot["lots"]][:4],snapshot["updated_at"]
    why=reason(snapshot,alert,lot,zone)
    if why:
        other,actions=alternatives(snapshot,alert,lot,zone)
        return f"{status_sentence(label(lot,zone),why)}\n{other}",card(snapshot,alert,lot,zone),actions,snapshot["updated_at"]
    if vehicle and vehicle.get("state")=="parked":
        clear(s)
        return "차량의 입차가 확인되어 목적지 감시를 종료했어요.",card(snapshot,alert,lot,zone),["find_car","fee"],snapshot["updated_at"]
    s.destination={"lot_id":lot,"zone":zone,"kind":(config.catalog.lot(lot) or {}).get("kind","unknown"),"set_at":iso(now()),"_id":uuid.uuid4().hex}
    s.destination_expires=time.monotonic()+config.DESTINATION_TTL_SEC;s.destination_problem=None
    return f"{config.catalog.name(lot)}을 목적지로 정했어요.\n{label(lot,zone)}에 {count(snapshot['lots'][lot],zone)}자리 있어요.",card(snapshot,alert,lot,zone),["empty_all","destination_cancel"],snapshot["updated_at"]


def monitor(s,sid,snapshot,alert,vehicle,notifications):
    destination=public(s)
    if not destination:return
    if vehicle.get("state")=="parked":clear(s);notifications.discard_prefix(sid,"destination:");return
    lot,zone=destination["lot_id"],destination["zone"]
    why=reason(snapshot,alert,lot,zone)
    problem=(lot,zone,why) if why else None
    if problem==s.destination_problem:return
    s.destination_problem=problem
    # 목적지 알림은 현재 상태를 전달한다. 미확인된 이전 상태는 교체한다.
    notifications.discard_prefix(sid, "destination:")
    if not problem:return
    text,actions=alternatives(snapshot,alert,lot,zone)
    payload={"text":f"{status_sentence(label(lot,zone),why,changed=True)}\n{text}","card":card(snapshot,alert,lot,zone),
             "chips":[],"action_ids":actions,"updated_at":snapshot["updated_at"],
             "meta":{"notification":"destination","destination_id":s.destination["_id"]}}
    notifications.enqueue(sid,payload,"destination:"+uuid.uuid4().hex)


def monitor_unavailable(s,sid,notifications):
    if not public(s) or s.destination_problem==("unavailable",):return
    s.destination_problem=("unavailable",)
    notifications.discard_prefix(sid, "destination:")
    notifications.enqueue(sid,{"text":"목적지의 최신 상태를 확인하지 못했어요.\n빈자리를 다시 확인해 주세요.","card":None,
        "chips":[],"action_ids":["empty_all","destination_cancel"],"updated_at":iso(now()),
        "meta":{"notification":"destination","destination_id":s.destination["_id"]}},"destination:"+uuid.uuid4().hex)
