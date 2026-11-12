import {useId} from 'react';
import {Icon, ChatbotIcon} from './components.jsx';
import {translate} from './serviceLocale.js';
import './styles/parking-help.css';

const steps = [
  {
    title: '빈자리 확인',
    description: '주차 지도에서 주차장을 선택하고 빈자리, 전기차, 장애인 필터로 원하는 칸을 찾으세요.',
    icon: 'pin',
    preview: ['빈자리', '전기차', '장애인'],
  },
  {
    title: '내 차 찾기 및 요금 확인',
    description: '개발원·부산역은 스티커 번호 두 자리, 김해공항·신세계 백화점은 차량번호 뒤 네 자리로 검색하세요.',
    note: '검색 결과에서 주차 위치와 요금을 확인하고, 출차 전에 주차요금을 미리 정산할 수 있어요.',
    icon: 'wallet',
    preview: ['스티커 번호', '13', '차량번호 뒤 4자리', '1724'],
  },
  {
    title: '주차 챗봇 사용하기',
    description: '주차 챗봇에서 바로가기 버튼을 누르거나 빈자리, 내 차 위치, 주차요금을 질문하세요.',
    note: '마이크를 눌러 질문할 수도 있어요. 말을 멈춘 뒤 3초가 지나면 인식된 질문이 자동으로 전송돼요.',
    icon: 'chat',
    preview: ['빈자리 알려줘'],
  },
  {
    title: '주차 정보 다시 확인하기',
    description: '연결이 끊기면 이전에 조회한 정보가 표시돼요.\n연결이 돌아오면 화면 오른쪽 위의 새로고침 버튼을 눌러 주세요.',
    icon: 'refresh',
    preview: ['새로고침'],
  },
];

export default function ParkingHelp({language}) {
  const group = useId();
  const localize = message => translate(language, message);

  return <div className="parking-help">
    {steps.map(({title, description, note, icon, preview}, index) => (
      <details key={title} name={group} open={index === 0 ? true : undefined}>
        <summary>
          <span className="parking-help-icon">{icon === 'chat' ? <ChatbotIcon/> : <Icon name={icon}/>}</span>
          <span>{index + 1}. {localize(title)}</span>
        </summary>
        <div className="parking-help-content">
          <p>{localize(description)}</p>
          {note && <p>{localize(note)}</p>}
          <div className="parking-help-preview" aria-hidden="true">
            {preview.map(label => <span key={label}>{localize(label)}</span>)}
          </div>
        </div>
      </details>
    ))}
  </div>;
}
