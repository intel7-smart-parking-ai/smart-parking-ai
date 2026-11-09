"""Shortest paths on explicit DEMO pedestrian graphs in SVG drawing units.

Vehicle entrance/exit gates are deliberately absent from these graphs.
Connections are simulation assumptions, not surveyed public walking routes.
"""
from heapq import heappop, heappush
from math import hypot, isclose
import re
from .query import Clarification,core
from .source import read


def request(text):
    targets = [
        (r'国际货物|국제화물|화물\s*청사','cargo','C'),
        (r'국제선','international','C'), (r'국내선','domestic','C'),
        (r'화장실|toilet','restroom',None), (r'엘리베이터|엘베|승강기','elevator',None),
        (r'영화관','cinema','D'),
        (r'몰\s*(?:(?:연결\s*)?(?:출입구|입구)|까지|로|에|가려면)','mall_door','D'),
        (r'백화점\s*(?:(?:연결\s*)?(?:출입구|입구)|까지|으로|에|가려면)','department_door','D'),
    ]
    wanted=bool(re.search(r'가장|제일|가까운|가까워|최단|어떻게.{0,4}가|가는\s*길|이동\s*경로|가려면',text))
    if not wanted:
        return None,None
    for pattern,key,lot in targets:
        if re.search(pattern,text,re.I):
            return key,lot
    return None,None


def graph(lot):
    points={}; labels={}; edges={}; areas={}; targets={}
    def node(key,x,y,label):
        points[key]=[x,y];labels[key]=label;edges[key]=[]
    def link(a,b):
        distance=hypot(points[a][0]-points[b][0],points[a][1]-points[b][1])
        edges[a].append((b,distance));edges[b].append((a,distance))
    if lot=='C':
        targets['elevator']=[]
        for area,x,title,key in [('A',280,'P1 국내선','domestic'),('B',750,'P2 국제선','international'),('C',1235,'P3 여객·화물','cargo')]:
            node(area,x,365,title+' 구역 보행 연결지점')
            node('walk_'+area,x,250,'청사 앞 시연 연결통로')
            node(key,x,162,{'domestic':'국내선 여객청사','international':'국제선 여객청사','cargo':'국제화물청사'}[key]+' 연결지점')
            link('walk_'+area,key)
            areas[area]=title;targets[key]=[key]
            elevator='elevator_'+area
            elevator_walk=elevator+'_walk'
            node(elevator_walk,x,294,title+' 엘리베이터 앞 연결통로')
            node(elevator,x+47,294,title+' 엘리베이터')
            link(area,elevator_walk);link(elevator_walk,'walk_'+area)
            link(elevator_walk,elevator)
            targets['elevator'].append(elevator)
        link('walk_A','walk_B');link('walk_B','walk_C')
        targets['restroom']=[]
        for key,x in [('domestic',441),('international',911),('cargo',1411)]:
            walk=key+'_restroom_walk';wc=key+'_restroom'
            node(walk,x,162,'청사 내부 시연 연결통로')
            node(wc,x,107,labels[key].replace(' 연결지점','')+' 화장실')
            link(key,walk);link(walk,wc);targets['restroom'].append(wc)
    elif lot=='D':
        # Aisle/perimeter connectors avoid crossing through parked bays.
        for side,x in [('l',85),('r',755),('L',790),('R',1450)]:
            previous=None
            for y in (95,297,490,722,890):
                key=f'{side}{y}';node(key,x,y,'구역 옆 시연 보행 통로')
                if previous:link(previous,key)
                previous=key
        for area,x,y,left,right in [('A',419,297,'l','r'),('C',419,722,'l','r'),('B',1122,297,'L','R'),('D',1122,722,'L','R')]:
            node(area,x,y,area+'구역 보행 연결지점')
            link(area,f'{left}{y}');link(area,f'{right}{y}');areas[area]=area+'구역'
        for key,x,label,anchor in [('mall_door',419,'몰 연결 출입구','l890'),('department_door',1122,'백화점 연결 출입구','R890')]:
            node(key+'_cross',x,890,'건물 앞 연결통로');node(key,x,905,label)
            link(anchor,key+'_cross');link(key+'_cross',key)
        link('r890','mall_door_cross');link('L890','department_door_cross')
        node('cross',768,890,'중앙 시연 횡단 연결통로')
        link('mall_door_cross','cross');link('cross','department_door_cross')
        for key,x,y,label,anchor in [('elevator_mall',657,57,'몰 쪽 엘리베이터','r95'),
                                   ('elevator_department',1377,57,'백화점 쪽 엘리베이터','R95'),
                                   ('restroom_mall',320,66,'몰 쪽 화장실','l95'),
                                   ('restroom_department',1205,66,'백화점 쪽 화장실','R95'),
                                   ('cinema',492,57,'영화관 방향 연결지점','r95')]:
            node(key+'_walk',x,95,'건물 앞 시연 보행 통로');node(key,x,y,label)
            link(anchor,key+'_walk');link(key+'_walk',key)
        targets={'mall_door':['mall_door'],'department_door':['department_door'],
                 'elevator':['elevator_mall','elevator_department'],
                 'restroom':['restroom_mall','restroom_department'],'cinema':['cinema']}
    else:
        raise Clarification('개발원·부산역에는 보행 연결 경로가 등록되지 않았어요. 공항 또는 신세계 도면 경로를 확인할 수 있어요.')
    return points,labels,edges,areas,targets


def shortest(edges,start,end):
    queue=[(0,start,[start])];best={start:0}
    while queue:
        distance,node,path=heappop(queue)
        if distance!=best[node]:continue
        if node==end:return distance,path
        for other,cost in edges[node]:
            candidate=distance+cost
            if candidate<best.get(other,float('inf')):
                best[other]=candidate;heappush(queue,(candidate,other,path+[other]))
    raise Clarification('이 목적지까지 연결된 시연 보행 경로가 없어요.')


def add_bays(lot,points,labels,edges,areas):
    ids=core()[0].catalog.spaces(lot)
    def node(key,x,y,label):
        points[key]=[x,y];labels[key]=label;edges[key]=[]
    def link(a,b):
        cost=hypot(points[a][0]-points[b][0],points[a][1]-points[b][1])
        edges[a].append((b,cost));edges[b].append((a,cost))
    groups={area:[] for area in areas}
    for space in ids:
        area=space.split('-')[0]
        if lot=='C' and area=='B' and int(space.split('-')[1])>24:area='C'
        if area in groups:groups[area].append(space)
    bay_areas={}
    for area,spaces in groups.items():
        for index,space in enumerate(spaces):
            bay_areas[space]=area
            if lot=='C':
                left,columns={'A':(70,6),'B':(540,6),'C':(1010,4)}[area]
                width=420/columns;row=index//columns;x=left+(index%columns+.5)*width
                y=430+row*125
                node(space,x,y,space)
                front=space+'_front';walk_y=365 if row==0 else 385+row*125-15
                node(front,x,walk_y,'주차칸 앞 시연 통로');link(space,front)
                if row==0:link(front,area)
                else:
                    for side,outer_x in [('left',left-20),('right',left+435)]:
                        turn=space+'_'+side;top=turn+'_top'
                        node(turn,outer_x,walk_y,'주차칸 옆 시연 통로')
                        node(top,outer_x,365,'구역 북쪽 시연 통로')
                        link(front,turn);link(turn,top);link(top,area)
            else:
                left=119 if area in ('A','C') else 822
                upper=area in ('A','B');row=index//8
                top=(134 if upper else 570)+row*(191 if upper else 182)
                height=132 if row==0 else 120
                x=left+(index%8+.5)*75;y=top+height/2
                node(space,x,y,space)
                walk_rows=([95,297] if row==0 else [297,490]) if upper else ([490,722] if row==0 else [722,890])
                sides=('l','r') if area in ('A','C') else ('L','R')
                for walk_y in walk_rows:
                    front=space+'_walk_'+str(walk_y)
                    node(front,x,walk_y,'주차칸 앞 시연 통로');link(space,front)
                    for side in sides:link(front,f'{side}{walk_y}')
                    if walk_y==890:
                        link(front,'mall_door_cross' if area in ('A','C') else 'department_door_cross')
                    if walk_y==95:
                        for target in (('elevator_mall','restroom_mall','cinema') if area in ('A','C') else ('elevator_department','restroom_department')):
                            link(front,target+'_walk')
    return bay_areas


def fetch(_day,query):
    if len(query.lots)!=1:
        raise Clarification('어느 주차장의 경로인가요? 김해공항 또는 신세계를 선택해 주세요.')
    lot=query.lots[0]
    points,labels,edges,areas,targets=graph(lot)
    if query.route_destination not in targets:
        raise Clarification('이 도면에는 해당 목적지의 보행 연결지점이 등록되지 않았어요. 차량 입구·출구는 건물 출입구와 구분합니다.')
    if query.section and query.section not in areas:
        raise Clarification('출발 구역을 확인하지 못했어요. 공항은 P1·P2·P3, 신세계는 A·B·C·D구역으로 알려주세요.')
    bay_areas=add_bays(lot,points,labels,edges,areas)
    rows=read(f'/api/v1/parking-lots/{lot}/spaces',{'items':[
        {'space_id':space,'status':'empty','space_type':core()[0].catalog.space_zone(lot,space)} for space in bay_areas]})
    if not isinstance(rows.get('items'),list) or rows.get('next_cursor'):
        raise ValueError('Incomplete space data')
    known={row['space_id']:row for row in rows['items']}
    if len(known)!=len(rows['items']):raise ValueError('Duplicate spaces')
    if query.route_space:
        if query.route_space not in bay_areas or query.route_space not in known:
            raise Clarification('해당 주차칸을 찾지 못했어요. A-01처럼 칸 번호를 확인해 주세요.')
        origins=[query.route_space]
    else:
        kind=query.space_type or 'general'
        origins=[space for space,area in bay_areas.items() if (not query.section or area==query.section)
                 and space in known and known[space].get('status')=='empty' and known[space].get('space_type')==kind]
        if not origins:raise Clarification('현재 조건에 맞는 빈 주차칸을 확인하지 못했어요.')
    candidates=[]
    for origin in origins:
        for target in targets[query.route_destination]:
            distance,path=shortest(edges,origin,target)
            candidates.append((distance,origin,target,path))
    candidates.sort()
    winners=[row for row in candidates if isclose(row[0],candidates[0][0])]
    recommendations=winners
    if lot=='C' and query.route_destination=='elevator' and not query.route_space:
        best_by_area={}
        recommendations=[]
        for row in candidates:
            area=bay_areas[row[1]]
            best=best_by_area.setdefault(area,row[0])
            if isclose(row[0],best):
                recommendations.append(row)
    distance,origin,target,path=winners[0]
    steps=[]
    for key in path:
        if not steps or steps[-1]!=labels[key]:steps.append(labels[key])
    query.route_plan={'lot_id':lot,'points':[points[key] for key in path],
                      'origin':origin,'destination':labels[target], 'space_id':origin,
                      'area':areas[bay_areas[origin]],
                      'basis':'도면의 개별 주차칸·시연 통로 기준. 실제 보행 거리·시간 미측정.',
                      'alternatives':sorted({row[1] for row in recommendations}),
                      'diagram_units':round(distance,2)}
    result={}
    if lot=='C' and query.route_destination=='elevator' and not query.route_space:
        closest_areas={bay_areas[row[1]] for row in winners}
        result['시연 도면 기준 엘리베이터와 가장 가까운 구역']=' · '.join(areas[area] for area in sorted(closest_areas))
    result['추천 주차칸' if not query.route_space else '출발 주차칸']=' · '.join(query.route_plan['alternatives'])
    return result
